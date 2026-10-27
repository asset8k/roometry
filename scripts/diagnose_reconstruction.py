"""Inspect a private saved run JSON offline; never calls a model or loads photos.

Usage: uv run python scripts/diagnose_reconstruction.py /path/to/run.json
The input needs the existing `analysis` and `proposal` fields. Optional `draft`
adds the original model/placement selection for rejected objects.
"""

import argparse
import json
from pathlib import Path

from roometry.vision.proposal_schemas import SceneDraft, SceneProposalResponse
from roometry.vision.reconstruction_diagnostics import omission_diagnostics
from roometry.vision.schemas import RoomAnalysisResponse


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    data = json.loads(args.run.read_text())
    analysis = RoomAnalysisResponse.model_validate_json(json.dumps(data["analysis"]))
    proposal = SceneProposalResponse.model_validate_json(json.dumps(data["proposal"]))
    draft = SceneDraft.model_validate_json(json.dumps(data["draft"])) if data.get("draft") else None
    report = omission_diagnostics(analysis, draft, proposal.scene, proposal.unresolved_entities)
    report["dimensions"] = {
        **proposal.dimensions.model_dump(), "estimated_axes": proposal.estimated_axes,
        "assumed_axes": proposal.assumed_axes,
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
