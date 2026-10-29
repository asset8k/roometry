"""Private offline reports and a reusable chat-review packet."""

import csv
import json
from pathlib import Path

from roometry.evaluation.benchmark import refresh_summaries
from roometry.evaluation.dataset import EvaluationInputError
from roometry.evaluation.reviews import blind_sides, review_template


REPOSITORY = Path(__file__).resolve().parents[3]
OUTPUT_ROOT = REPOSITORY / "artifacts/evaluations"


def validate_output(path: Path):
    root, target = OUTPUT_ROOT.resolve(), path.resolve()
    if not root.is_relative_to(REPOSITORY.resolve()) or not target.is_relative_to(root) or target == root:
        raise EvaluationInputError("Reports must use a new folder below artifacts/evaluations/.")
    if target.exists():
        raise EvaluationInputError("Report folder already exists; choose a new folder to preserve evidence.")


def _json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def _scene_evidence(output, folder, scene, render):
    directory = output / folder
    directory.mkdir(parents=True)
    _json(directory / "scene.json", scene.model_dump(mode="json"))
    result = {"scene": f"{folder}/scene.json", "renders": [], "render_success": None}
    if render:
        try:
            # Pure CPU/Pillow catalog rendering. No application or provider client.
            from roometry.vision.review_render import render_views
            for view in render_views(scene, annotate=False):
                filename = f"{view.view_id}.png"
                (directory / filename).write_bytes(view.image)
                result["renders"].append(f"{folder}/{filename}")
            result["render_success"] = True
        except (ValueError, ArithmeticError, KeyError):
            result["render_success"] = False
    return result


def write_report(dataset, report, output: Path, *, render=False):
    """Writes only ignored artifacts; refuses overwrites or public destinations."""
    validate_output(output)
    output.mkdir(parents=True)
    packet, keys = [], {}
    for row in report["trials"]:
        entry = dataset.entries[row["case_id"]]
        if not row.get("has_scene"):
            continue
        scene, original = row["_scene"], row["_original"]
        item = {"evaluation_id": row["evaluation_id"], "kind": row["kind"],
                "evidence_sha256": row["evidence_sha256"],
                "photos": [str((dataset.root / name).resolve()) for name in entry["images"]],
                "reference": entry["visual_reference"],
                "measurement_note": entry["measurement_note"]}
        base = f"evidence/{row['evaluation_id']}"
        if row["kind"] == "improvement" and original is not None:
            mapping = blind_sides(row)
            keys[row["evaluation_id"]] = mapping
            item["goal"] = entry["improvement_trial"]
            item["layouts"] = {side: _scene_evidence(output, f"{base}/{side}", original if role == "original" else scene, render)
                               for side, role in mapping.items()}
            rendered = all(layout["render_success"] for layout in item["layouts"].values()) if render else None
        else:
            item["layout"] = _scene_evidence(output, base, scene, render)
            rendered = item["layout"]["render_success"]
        row["metrics"]["render_success"] = rendered
        if rendered is False:
            row["code_pass"] = False
            row["issues"].append("local_render_failed")
        packet.append(item)
    refresh_summaries(dataset, report)
    clean = {**report, "trials": [{key: value for key, value in row.items() if not key.startswith("_")} for row in report["trials"]]}
    _json(output / "report.json", clean)
    _json(output / "visual-review.json", review_template(report["trials"]))
    _json(output / "review-packet.json", {
        "rubric_version": "roometry-visual-v1", "recording_mode": report["recording_mode"],
        "instructions": "Inspect source photos and all saved views. Fill visual-review.json in chat; no API. Judge improvements before opening report.json or blind-key.json.",
        "render_limitations": "Diagnostic CPU catalog views, not camera-matched or photorealistic; curves and transparency simplified, painter ordering can hide parts. Use scene JSON and multiple views. Request browser screenshots if appearance is inconclusive.",
        "cases": packet,
    })
    _json(output / "blind-key.json", keys)
    fields = ("evaluation_id", "case_id", "kind", "repeat", "recording_status", "usable", "code_pass", "overall_success",
              "major_object_coverage", "wall_relationships", "orientation", "proportions", "color_consistency",
              "meaningful_proxy_gain", "proxy_score_gain", "elapsed_seconds", "estimated_usd", "complete_usage", "visual_review_status", "issues")
    with (output / "results.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in clean["trials"]:
            flattened = {**row, **row["metrics"], **row["telemetry"], "visual_review_status": row["visual_review"]["status"]}
            flattened["issues"] = "; ".join(row["issues"] + row["metrics"].get("hard_issues", []) + row["metrics"].get("fidelity_issues", []))
            writer.writerow({key: flattened.get(key) for key in fields})
    recon = report["reconstruction"]
    def percentage(value):
        return "pending / not assessed" if value is None else f"{value:.1%}"
    summary = ["# Offline evaluation report", "", f"Mode: **{report['recording_mode']}**. This command made **no API calls**.",
               f"Dataset: `{report['dataset_fingerprint_sha256']}`", "",
               f"Reconstruction: {recon['attempted']}/{recon['planned']} attempted; {recon['not_run']} not run; {recon['repeated_recordings']} repeats reported separately.",
               f"Usable: {percentage(recon['usable_completion_rate'])}. Code pass: {percentage(recon['code_pass_rate'])}.",
               f"Overall success: {percentage(recon['overall_success_rate'])}; visual reviews pending: {recon['visual_reviews_pending']}.", "",
               "| Improvement | Attempted / planned | Code pass | Overall success |", "| --- | --- | --- | --- |"]
    for goal, values in report["improvements"].items():
        summary.append(f"| {goal} | {values['attempted']} / {values['planned']} | {percentage(values['code_pass_rate'])} | {percentage(values['overall_success_rate'])} |")
    spending = report["spending"]
    summary.extend(["", f"Recorded spend estimate: {spending['known_estimated_usd'] or 'unknown'} USD; complete usage: {spending['complete_usage']}.",
                    "See report.json for stage costs, failure denominators, latency, source/split breakdowns and unassessed checks.", "",
                    "Mock runs are plumbing tests, not evidence of model quality. Geometry proxies are not independent design judgments.",
                    "Review the saved photos/views and fill visual-review.json; re-run with --reviews into a new folder. Keep all artifacts private."])
    (output / "summary.md").write_text("\n".join(summary) + "\n")
    return clean
