"""Evaluate a saved agent response against a fixed photo annotation; no API calls.

Example: python scripts/run_evals.py --case data/fixtures/evaluation/synthetic_study.json --run /tmp/run.json
Photo references are not opened or transmitted by this offline command.
"""

import argparse
import json
from pathlib import Path

from roometry.evaluation.runner import evaluate_reconstruction
from roometry.evaluation.schemas import ReconstructionCase
from roometry.scenes.schemas import RoomScene
from roometry.vision.schemas import RoomAnalysisResponse
from roometry.vision.proposal_schemas import SceneDraft, UnresolvedEntity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path)
    parser.add_argument("--run", type=Path, help="Saved agent response with analysis and working_scene")
    parser.add_argument("--draft", type=Path, help="Optional saved scene draft; absent draft stages stay unassessed")
    parser.add_argument("--manifest", type=Path, help="Frozen public dataset manifest for batch replay")
    parser.add_argument("--recordings", type=Path, help="Local recording index; does not execute generations")
    parser.add_argument("--output", type=Path, help="New ignored directory below artifacts/evaluations/")
    parser.add_argument("--reviews", type=Path, help="Optional completed chat visual judgments")
    parser.add_argument("--render", action="store_true", help="Generate CPU diagnostic views locally")
    args = parser.parse_args()
    if args.manifest:
        if not args.recordings or not args.output or args.case or args.run or args.draft:
            parser.error("Batch mode needs --manifest, --recordings and --output; do not combine with single-case flags.")
        from roometry.evaluation.benchmark import run_benchmark
        from roometry.evaluation.dataset import EvaluationInputError
        from roometry.evaluation.reporting import validate_output, write_report
        try:
            validate_output(args.output)
            dataset, report = run_benchmark(args.manifest, args.recordings, reviews_path=args.reviews)
            clean = write_report(dataset, report, args.output, render=args.render)
        except EvaluationInputError as exc:
            parser.error(str(exc))
        print(json.dumps({"output": str(args.output), "recording_mode": clean["recording_mode"],
                          "reconstruction": clean["reconstruction"], "spending": clean["spending"]}, indent=2))
        # A report was produced successfully. Per-case failures remain in it;
        # pending visual judgments are not represented as a passing benchmark.
        return
    if not args.case or not args.run or any((args.recordings, args.output, args.reviews, args.render)):
        parser.error("Single-case mode needs --case and --run; use --manifest for batch replay.")
    case = ReconstructionCase.model_validate_json(args.case.read_text())
    run = json.loads(args.run.read_text())
    scene = RoomScene.model_validate(run["working_scene"]) if run.get("working_scene") is not None else None
    analysis = RoomAnalysisResponse.model_validate_json(json.dumps(run["analysis"])) if run.get("analysis") is not None else None
    draft = SceneDraft.model_validate_json(args.draft.read_text()) if args.draft else None
    unresolved = [UnresolvedEntity.model_validate(item) for item in (run.get("proposal") or {}).get("unresolved_entities", [])]
    report = evaluate_reconstruction(case, analysis, scene, draft=draft, unresolved=unresolved)
    print(report.model_dump_json(indent=2))
    raise SystemExit(0 if report.passed else 1)


if __name__ == "__main__":
    main()
