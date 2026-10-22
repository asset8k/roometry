"""Evaluate a saved agent response against a fixed photo annotation; no API calls.

Example: python scripts/run_evals.py --case data/evals/bedroom_multiview.json --run /tmp/run.json
Photo files are local references, not copied into the repository or transmitted.
"""

import argparse
import json
from pathlib import Path

from roometry.evaluation.runner import evaluate_reconstruction
from roometry.evaluation.schemas import ReconstructionCase
from roometry.scenes.schemas import RoomScene
from roometry.vision.schemas import RoomAnalysisResponse


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True, help="Saved agent response with analysis and working_scene")
    args = parser.parse_args()
    case = ReconstructionCase.model_validate_json(args.case.read_text())
    run = json.loads(args.run.read_text())
    scene = RoomScene.model_validate(run["working_scene"])
    analysis = RoomAnalysisResponse.model_validate_json(json.dumps(run["analysis"]))
    report = evaluate_reconstruction(case, analysis, scene)
    print(report.model_dump_json(indent=2))
    raise SystemExit(0 if report.passed else 1)


if __name__ == "__main__":
    main()
