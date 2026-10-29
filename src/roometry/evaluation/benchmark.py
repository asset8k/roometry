"""Batch replay of saved recordings. No app, provider client, MCP or network."""

from datetime import UTC, datetime
import json
from pathlib import Path
from statistics import median

from pydantic import ValidationError

from roometry.evaluation.dataset import EvaluationInputError, load_dataset, load_recordings, local_path, read_json
from roometry.evaluation.matching import match_identities
from roometry.evaluation.metrics import evaluate_improvement, reconstruction_metrics
from roometry.evaluation.reviews import attach_reviews, evidence_fingerprint
from roometry.evaluation.runner import evaluate_reconstruction
from roometry.evaluation.telemetry import collect_telemetry
from roometry.scenes.schemas import RoomScene
from roometry.vision.proposal_schemas import SceneDraft, UnresolvedEntity
from roometry.vision.schemas import RoomAnalysisResponse


POLICY_VERSION = "roometry-evaluation-v1"


def distribution(values):
    values = sorted(value for value in values if value is not None)
    if not values:
        return {"assessed": 0, "mean": None, "median": None, "p90": None}
    # Nearest-rank percentile, disclosed for this small dataset.
    from math import ceil
    return {"assessed": len(values), "mean": sum(values) / len(values), "median": median(values), "p90": values[max(0, ceil(.9 * len(values)) - 1)]}


def summarize(rows):
    primary = [row for row in rows if not row["repeat"]]
    executed = [row for row in primary if row["recording_status"] != "not_run"]
    def rate(key):
        return sum(bool(row.get(key)) for row in executed) / len(executed) if executed else None
    def metric(key):
        values = [row["metrics"].get(key) for row in executed if row["metrics"].get(key) is not None]
        return {"mean": sum(values) / len(values) if values else None, "assessed": len(values), "attempted": len(executed)}
    def telemetry_rate(key):
        values = [row["telemetry"].get(key) for row in executed if type(row["telemetry"].get(key)) is bool]
        return {"rate": sum(values) / len(values) if values else None, "assessed": len(values)}
    return {
        "planned": len(primary), "attempted": len(executed), "not_run": len(primary) - len(executed),
        "complete": sum(row["metrics"].get("completion") == "complete" for row in executed),
        "usable_partial": sum(row["metrics"].get("completion") == "usable_partial" for row in executed),
        "usable_completion_rate": rate("usable"), "code_pass_rate": rate("code_pass"),
        "overall_success_rate": rate("overall_success") if executed and all(row["overall_success"] is not None for row in executed) else None,
        "visual_reviews_pending": sum(row["visual_review"]["status"] != "reviewed" or row["visual_review"].get("confidence") == "low"
                                      for row in executed if row.get("has_scene")),
        "metrics": {key: metric(key) for key in ("major_object_coverage", "major_inventory_precision_proxy", "wall_relationships", "orientation", "support_relationships", "proportions", "color_consistency")},
        "latency_seconds": distribution([row["telemetry"].get("elapsed_seconds") for row in executed]),
        "complete_cost_usd": distribution([float(row["telemetry"]["estimated_usd"]) for row in executed if row["telemetry"].get("complete_usage") and row["telemetry"].get("estimated_usd") is not None]),
        "cost_over_0_10_count": sum(float(row["telemetry"]["estimated_usd"]) > .1 for row in executed if row["telemetry"].get("complete_usage") and row["telemetry"].get("estimated_usd") is not None),
        "repeated_recordings": len(rows) - len(primary),
        "model_calls": distribution([row["telemetry"].get("model_calls") for row in executed]),
        "tool_calls": distribution([row["telemetry"].get("tool_calls") for row in executed]),
        "repairs": distribution([row["telemetry"].get("repairs") for row in executed]),
        "flow_checks": {key: telemetry_rate(key) for key in ("budgets_respected", "no_paid_calls_on_resume_or_retry", "clarification_resumed_without_reanalysis", "tool_error_recovered")},
        "schema_valid": metric("schema_valid"), "geometry_valid": metric("geometry_valid"),
        "render_success": metric("render_success"),
        "constraint_checks": {key: metric(key) for key in ("collision_free", "door_access_clear", "known_door_swings_clear", "clearance_preserved", "locked_object_preservation")},
    }


def _empty_row(entry, evaluation_id, kind="reconstruction", repeat=False):
    return {"evaluation_id": evaluation_id, "case_id": entry["id"], "kind": kind,
            "repeat": repeat, "room_type": entry["room_type"], "split": entry["split"],
            "source_type": entry["source"]["type"], "recording_status": "not_run",
            "has_scene": False, "usable": False, "code_pass": False, "overall_success": None,
            "visual_review": {"status": "pending"}, "metrics": {}, "telemetry": {}, "issues": []}


def _parse_field(raw, key, model, issues):
    if raw.get(key) is None:
        return None
    try:
        return model.model_validate_json(json.dumps(raw[key]))
    except (ValidationError, TypeError, ValueError):
        issues.append(f"invalid_{key}")
        return None


def _account_checks(index, trial, raw, entry, has_scene, parent, parent_raw):
    if index.recording_mode != "live":
        return {"status": "not_applicable_mock", "passed": None}
    # Optimization DTOs inherit ownership through their reconstruction; they do
    # not duplicate the AgentState.persistence field.
    account_raw = raw if trial.kind == "reconstruction" else parent_raw
    account = bool(index.account_id) and account_raw.get("persistence") == "account"
    receipt = trial.saved_room
    saved = bool(receipt and receipt.account_id == index.account_id
                 and receipt.title == Path(entry["primary_image"]).stem and not receipt.published)
    if trial.kind == "improvement" and has_scene:
        saved = saved and parent is not None and parent.saved_room is not None and receipt.room_id == parent.saved_room.room_id
    return {"status": "checked", "signed_in": account, "saved_to_my_rooms": saved if has_scene else None,
            "passed": account and (saved if has_scene else True),
            "room_id": receipt.room_id if receipt else None,
            "room_path": f"/#room/{receipt.room_id}" if receipt else None,
            "note": "Ownership/save receipt is recorder evidence, not an independent server authentication check."}


def run_benchmark(manifest_path: Path, recordings_path: Path, *, reviews_path: Path | None = None):
    dataset = load_dataset(manifest_path)
    index = load_recordings(recordings_path, dataset)
    root = recordings_path.parent
    raw_runs, errors = {}, {}
    for trial in index.trials:
        try:
            raw = read_json(local_path(root, trial.run))
            if not isinstance(raw, dict):
                raise EvaluationInputError("Run evidence must be an object.")
            raw_runs[trial.evaluation_id] = raw
        except EvaluationInputError:
            errors[trial.evaluation_id] = "missing_or_invalid_run_artifact"
    telemetry, spending = collect_telemetry(root, index.trials, raw_runs)
    rows, scenes = [], {}
    by_id = {trial.evaluation_id: trial for trial in index.trials}
    # Reconstruction parents are evaluated before their improvements regardless
    # of how entries were serialized. Repeats never inflate the main denominator.
    for trial in sorted(index.trials, key=lambda item: item.kind == "improvement"):
        entry, reference = dataset.entries[trial.case_id], dataset.references[trial.case_id]
        row = _empty_row(entry, trial.evaluation_id, trial.kind, trial.repeat)
        row.update(recording_status="recorded", telemetry=telemetry[trial.evaluation_id])
        raw = raw_runs.get(trial.evaluation_id, {})
        scene, original = None, None
        if trial.evaluation_id in errors:
            row["issues"].append(errors[trial.evaluation_id])
        try:
            if trial.kind == "reconstruction":
                scene = _parse_field(raw, "working_scene", RoomScene, row["issues"])
                analysis = _parse_field(raw, "analysis", RoomAnalysisResponse, row["issues"])
                draft = None
                if trial.draft:
                    try:
                        draft = SceneDraft.model_validate_json(local_path(root, trial.draft).read_text())
                    except (OSError, ValueError):
                        row["issues"].append("invalid_or_missing_draft")
                unresolved = []
                proposal = raw.get("proposal")
                unresolved_items = proposal.get("unresolved_entities", []) if isinstance(proposal, dict) else []
                if not isinstance(unresolved_items, list):
                    row["issues"].append("invalid_unresolved_items")
                    unresolved_items = []
                for item in unresolved_items:
                    try:
                        unresolved.append(UnresolvedEntity.model_validate_json(json.dumps(item)))
                    except ValueError:
                        row["issues"].append("invalid_unresolved_item")
                identities = match_identities(reference, analysis, scene, entry.get("visual_reference"), trial.identity_matches)
                likeness = evaluate_reconstruction(reference, analysis, scene, draft=draft, unresolved=unresolved,
                    identity_matches=identities.final, observation_matches=identities.observations)
                row["likeness"] = likeness.model_dump(mode="json")
                row["metrics"] = reconstruction_metrics(entry, reference, raw, scene, analysis, identities, likeness)
                row["usable"] = row["metrics"]["usable"]
                row["code_pass"] = row["usable"] and not identities.ambiguous
                scenes[trial.evaluation_id] = scene
            else:
                original = scenes.get(trial.parent_evaluation_id)
                row["metrics"] = evaluate_improvement(original, raw, entry["improvement_trial"]["focus"])
                scene = _parse_field(raw, "result_scene", RoomScene, row["issues"])
                row["usable"] = row["metrics"]["accepted"]
                row["code_pass"] = row["usable"] and row["metrics"].get("meaningful_proxy_gain") is not False
        except (ValueError, TypeError, KeyError, AttributeError):
            row["issues"].append("invalid_trial_evidence")
            row["code_pass"], row["usable"] = False, False
            row["metrics"] = {"completion": "failed", "schema_valid": False,
                              "major_object_coverage": 0. if trial.kind == "reconstruction" else None}
        row["has_scene"] = scene is not None
        row["account"] = _account_checks(index, trial, raw, entry, scene is not None,
            by_id.get(trial.parent_evaluation_id), raw_runs.get(trial.parent_evaluation_id, {}))
        if row["account"]["passed"] is False:
            row["issues"].append("account_or_save_requirement_failed")
            row["code_pass"] = False
        if row["telemetry"].get("budgets_respected") is False:
            row["issues"].append("model_or_tool_budget_exceeded")
            row["code_pass"] = False
        if (row["telemetry"].get("clarification_count") or 0) > 3:
            row["issues"].append("clarification_cap_exceeded")
            row["code_pass"] = False
        if row["telemetry"].get("no_paid_calls_on_resume_or_retry") is False:
            row["issues"].append("paid_call_replay_detected")
            row["code_pass"] = False
        row["evidence_sha256"] = evidence_fingerprint(dataset, row, scene, original)
        row["_scene"], row["_original"] = scene, original
        rows.append(row)
    for key, entry in dataset.entries.items():
        if not any(row["case_id"] == key and row["kind"] == "reconstruction" and not row["repeat"] for row in rows):
            rows.append(_empty_row(entry, "not_run_" + key))
    # Planned improvements remain visible when their reconstruction fails.
    for key, entry in dataset.entries.items():
        if entry.get("improvement_trial") and not any(row["case_id"] == key and row["kind"] == "improvement" and not row["repeat"] for row in rows):
            row = _empty_row(entry, "not_run_improvement_" + key, "improvement")
            row["metrics"]["focus"] = entry["improvement_trial"]["focus"]
            rows.append(row)
    review_document, review_errors = None, []
    if reviews_path:
        try:
            review_document = read_json(reviews_path)
        except EvaluationInputError:
            review_errors.append("missing_or_invalid_review_file")
    review_errors.extend(attach_reviews(rows, review_document))
    report = {
        "schema_version": 1, "policy_version": POLICY_VERSION, "created_at": datetime.now(UTC).isoformat(),
        "recording_mode": index.recording_mode, "execution": "offline_replay_no_api_calls",
        "dataset_id": dataset.manifest["dataset_id"], "dataset_fingerprint_sha256": dataset.manifest["dataset_fingerprint_sha256"],
        "gates": {"major_object_coverage": .9, "spatial_relationships": .85, "visual_score": 8,
                  "note": "Minor appearance failures are reported separately; code checks cannot replace pending visual judgments."},
        "spending": spending, "review_import_issues": review_errors,
        "limitations": dataset.manifest.get("limitations", []) + [
            "Mock recordings verify the evaluation plumbing, not real model quality or actual API spend.",
            "Practical improvement metrics reuse documented geometry proxies; visual usefulness is judged separately in chat.",
            "Incomplete usage is a lower bound. Missing measurements and unassessed scores are never silently replaced by zero.",
        ], "trials": rows,
    }
    refresh_summaries(dataset, report)
    return dataset, report


def refresh_summaries(dataset, report):
    """Recompute aggregates after optional local rendering; never judge images."""
    rows = report["trials"]
    for row in rows:
        review = row["visual_review"]
        if row["recording_status"] != "not_run":
            row["overall_success"] = (row["code_pass"] and review.get("passed", False)
                if review["status"] == "reviewed" and review.get("confidence") != "low"
                else False if not row["code_pass"] else None)
    reconstructions = [row for row in rows if row["kind"] == "reconstruction"]
    report["reconstruction"] = summarize(reconstructions)
    for group, field, keys in (
        ("by_room_type", "room_type", sorted({row["room_type"] for row in reconstructions})),
        ("by_source", "source_type", ("generated", "photographed")),
        ("by_split", "split", ("development", "held_out")),
    ):
        report[group] = {key: summarize([row for row in reconstructions if row[field] == key]) for key in keys}
    report["improvements"] = {key: summarize([row for row in rows if row["kind"] == "improvement"
        and dataset.entries[row["case_id"]]["improvement_trial"]["focus"] == key])
        for key in ("openness", "storage", "workspace", "aesthetics")}
