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
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True, help="Saved agent response with analysis and working_scene")
    parser.add_argument("--draft", type=Path, help="Optional saved scene draft; absent draft stages stay unassessed")
    args = parser.parse_args()
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
