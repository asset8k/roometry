"""Replay cost/timing evidence without initializing the application or providers."""

from collections import Counter, defaultdict
from math import isfinite
from pathlib import Path

from roometry.evaluation.costs import estimate_cost
from roometry.evaluation.dataset import EvaluationInputError, local_path, read_json


def number(value):
    return float(value) if type(value) in {int, float} and isfinite(value) and value >= 0 else None


def _call_key(call, path, position):
    identity = call.get("response_id") or call.get("provider_request_id")
    return ("response", str(identity)) if identity else (path, position)


def collect_telemetry(root: Path, trials, raw_runs):
    file_uses = Counter(trial.cost for trial in trials if trial.cost)
    time_uses = Counter(trial.timing for trial in trials if trial.timing)
    files, unique, per_trial, missing = {}, {}, {}, []
    fields = ("model", "input_tokens", "output_tokens", "cached_input_tokens", "cache_write_tokens",
              "cache_write_tokens_details", "service_tier")
    # Deduplicate before pricing individual trials as well as the whole batch.
    for path in file_uses:
        try:
            artifact = read_json(local_path(root, path))
            calls = artifact.get("calls") if isinstance(artifact, dict) else None
            if not isinstance(calls, list) or any(not isinstance(row, dict) for row in calls):
                raise EvaluationInputError("Cost evidence needs recorded calls.")
            files[path] = calls
            for i, call in enumerate(calls):
                key = _call_key(call, path, i)
                if key in unique and any(unique[key].get(field) != call.get(field) for field in fields):
                    unique[key] = {"model": "conflicting_usage_evidence"}
                elif key not in unique:
                    unique[key] = call
        except EvaluationInputError:
            files[path] = None
    for trial in trials:
        result = {"estimated_usd": None, "complete_usage": False, "elapsed_seconds": None,
                  "model_calls": None, "tool_calls": None, "repairs": None, "issues": []}
        raw = raw_runs.get(trial.evaluation_id) or {}
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
        limits = raw.get("limits") if isinstance(raw.get("limits"), dict) else {}
        for field in ("model_calls", "tool_calls"):
            result[field] = usage.get(field) if type(usage.get(field)) is int and usage[field] >= 0 else None
        calls = files.get(trial.cost)
        if calls is None:
            result["issues"].append("cost_not_recorded" if not trial.cost else "invalid_or_missing_cost_evidence")
        else:
            selected = {}
            attributable = file_uses[trial.cost] == 1 or any(row.get("run_id") for row in calls)
            for i, call in enumerate(calls):
                if any(row.get("run_id") for row in calls) and (not raw.get("run_id") or call.get("run_id") != raw["run_id"]):
                    continue
                key = _call_key(call, trial.cost, i)
                selected[key] = unique[key]
            if not attributable:
                result["issues"].append("shared_cost_cannot_be_attributed_to_trial")
            elif not selected and result["model_calls"] != 0:
                result["issues"].append("no_matching_usage_for_trial")
            else:
                priced = estimate_cost(list(selected.values()))
                result.update(estimated_usd=priced["estimated_usd"], complete_usage=priced["complete_usage"],
                              repairs=sum("repair" in str(row.get("operation", "")) for row in selected.values()))
                if result["model_calls"] is None or result["model_calls"] > len(selected):
                    result["complete_usage"] = False
                    result["issues"].append("model_usage_count_unverified_or_missing")
        if not result["complete_usage"]:
            missing.append(trial.evaluation_id)
        if trial.timing:
            try:
                timing = read_json(local_path(root, trial.timing))
                key = "reconstruction_elapsed_seconds" if trial.kind == "reconstruction" else "optimization_elapsed_seconds"
                value = timing.get(key)
                if value is None and time_uses[trial.timing] == 1:
                    value = timing.get("elapsed_seconds")
                result["elapsed_seconds"] = number(value)
                if result["elapsed_seconds"] is None:
                    result["issues"].append("timing_not_attributable_or_invalid")
            except (EvaluationInputError, AttributeError):
                result["issues"].append("invalid_or_missing_timing_evidence")
        result["budgets_respected"] = (
            all(0 <= usage[key] <= limits[limit] for key, limit in
                (("model_calls", "max_model_calls"), ("tool_calls", "max_tool_calls")))
            if all(type(usage.get(key)) is int and type(limits.get(limit)) is int for key, limit in
                   (("model_calls", "max_model_calls"), ("tool_calls", "max_tool_calls"))) else None
        )
        result["clarification_count"] = len(raw["questions"]) if isinstance(raw.get("questions"), list) else None
        result["no_paid_calls_on_resume_or_retry"] = None
        if trial.flow_checks:
            try:
                flow = read_json(local_path(root, trial.flow_checks))
                value = flow.get("no_paid_calls_on_prepare_or_retries")
                result["no_paid_calls_on_resume_or_retry"] = value if type(value) is bool else None
                # These are optional recorder assertions, never inferred from a final scene.
                for field in ("clarification_resumed_without_reanalysis", "tool_error_recovered"):
                    value = flow.get(field)
                    result[field] = value if type(value) is bool else None
            except (EvaluationInputError, AttributeError):
                result["issues"].append("invalid_or_missing_flow_evidence")
        per_trial[trial.evaluation_id] = result
    calls = list(unique.values())
    total = estimate_cost(calls)
    stage_rows = defaultdict(list)
    for call in calls:
        stage_rows[str(call.get("operation") or "unknown")].append(call)
    by_stage = {}
    for key, rows in sorted(stage_rows.items()):
        price = estimate_cost(rows)
        by_stage[key] = {"calls": len(rows), "known_estimated_usd": price["estimated_usd"], "unpriced_calls": price["unpriced_calls"]}
    complete = bool(files) and total["complete_usage"] and not missing
    return per_trial, {
        "known_estimated_usd": total["estimated_usd"] if any(value is not None for value in files.values()) else None,
        "complete_usage": complete, "lower_bound_only": not complete,
        "unpriced_calls": total["unpriced_calls"],
        "trials_missing_cost_evidence": sorted(set(missing)), "unique_recorded_calls": len(calls),
        "duplicate_response_rows_removed": sum(len(rows) for rows in files.values() if rows is not None) - len(calls),
        "by_stage": by_stage, "pricing_date": total["pricing_date"], "pricing_source": total["pricing_source"],
        "models_seen": sorted({str(call.get("model") or "unknown") for call in calls}),
        "rates_per_million": total["standard_rates_per_million"],
        "note": "Pinned standard-rate estimate from saved provider usage, not an invoice. Image and reasoning tokens are already included. Shared receipts and repeated response IDs are counted once.",
    }
