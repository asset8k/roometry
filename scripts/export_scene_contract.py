"""Export scene, edit, upload, and analysis contracts and validated fixtures."""

import json
from pathlib import Path

from roometry.scenes.edit_schemas import PreviewEditRequest, PreviewEditResponse
from roometry.scenes.decor import DECOR_CATALOG
from roometry.scenes.equipment import EQUIPMENT_CATALOG
from roometry.scenes.fixture_models import FIXTURE_CATALOG
from roometry.scenes.furniture import FURNITURE_CATALOG
from roometry.scenes.schemas import RoomScene
from roometry.scenes.validation import validate_geometry
from roometry.uploads.schemas import (
    PreparedRoomInput,
    RoomInputOptions,
    UploadErrorResponse,
    UploadLimits,
)
from roometry.vision.proposal_schemas import SceneProposalRequest, SceneProposalResponse
from roometry.vision.schemas import (
    AnalysisErrorResponse,
    AnalyzeRoomRequest,
    RoomAnalysisResponse,
    RoomObservations,
)

ROOT = Path(__file__).resolve().parents[1]
SCENE_FIXTURES = ("bedroom_scene.json", "bedroom_after_scene.json", "kitchen_catalog_scene.json", "bathroom_catalog_scene.json", "decor_catalog_scene.json")


def main() -> None:
    outputs = {
        ROOT
        / "frontend/src/lib/scene-proposal-request.schema.json": SceneProposalRequest.model_json_schema(),
        ROOT
        / "frontend/src/lib/scene-proposal-response.schema.json": SceneProposalResponse.model_json_schema(
            mode="serialization"
        ),
        ROOT
        / "frontend/src/lib/room-analysis-error.schema.json": AnalysisErrorResponse.model_json_schema(
            mode="serialization"
        ),
        ROOT / "frontend/src/lib/scene.schema.json": RoomScene.model_json_schema(),
        ROOT
        / "frontend/src/lib/edit-request.schema.json": PreviewEditRequest.model_json_schema(),
        ROOT
        / "frontend/src/lib/edit-response.schema.json": PreviewEditResponse.model_json_schema(
            mode="serialization"
        ),
        ROOT
        / "frontend/src/lib/room-input-options.schema.json": RoomInputOptions.model_json_schema(),
        ROOT
        / "frontend/src/lib/room-input-limits.schema.json": UploadLimits.model_json_schema(
            mode="serialization"
        ),
        ROOT
        / "frontend/src/lib/room-input-response.schema.json": PreparedRoomInput.model_json_schema(
            mode="serialization"
        ),
        ROOT
        / "frontend/src/lib/room-input-error.schema.json": UploadErrorResponse.model_json_schema(
            mode="serialization"
        ),
        ROOT
        / "frontend/src/lib/room-analysis-request.schema.json": AnalyzeRoomRequest.model_json_schema(),
        ROOT
        / "frontend/src/lib/room-observations.schema.json": RoomObservations.model_json_schema(),
        ROOT
        / "frontend/src/lib/room-analysis-response.schema.json": RoomAnalysisResponse.model_json_schema(
            mode="serialization"
        ),
    }
    for filename in SCENE_FIXTURES:
        scene = RoomScene.model_validate_json(
            (ROOT / "data/fixtures" / filename).read_text()
        )
        report = validate_geometry(scene)
        if not report.valid:
            raise ValueError(f"{filename} failed baseline geometry: {report.errors}")
        outputs[ROOT / "frontend/src/data" / filename] = scene.model_dump(mode="json")
    options = RoomInputOptions.model_validate_json(
        (ROOT / "data/fixtures/room_input_options.json").read_text()
    )
    outputs[ROOT / "frontend/src/data/room_input_options.json"] = options.model_dump(
        mode="json"
    )
    analysis = RoomAnalysisResponse.model_validate_json(
        (ROOT / "data/fixtures/room_analysis.json").read_text()
    )
    outputs[ROOT / "frontend/src/data/room_analysis.json"] = analysis.model_dump(
        mode="json"
    )
    proposal = SceneProposalResponse.model_validate_json(
        (ROOT / "data/fixtures/room_scene_proposal.json").read_text()
    )
    if proposal.scene is None or not validate_geometry(proposal.scene).valid:
        raise ValueError("The proposal fixture must contain a valid base scene.")
    outputs[ROOT / "frontend/src/data/room_scene_proposal.json"] = proposal.model_dump(
        mode="json"
    )
    for path, data in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
        print(f"Exported {path.relative_to(ROOT)}")
    for filename, catalog in (
        ("core-furniture.generated.json", FURNITURE_CATALOG),
        ("equipment.generated.json", EQUIPMENT_CATALOG),
        ("fixtures.generated.json", FIXTURE_CATALOG),
        ("decor.generated.json", DECOR_CATALOG),
    ):
        # One compact entry per asset avoids excessive generated-file churn.
        catalog_path = ROOT / "frontend/src/data" / filename
        catalog_path.write_text(
            "[\n"
            + ",\n".join(
                "  "
                + json.dumps(asset.to_dict(), separators=(",", ":"), allow_nan=False)
                for asset in catalog.values()
            )
            + "\n]\n"
        )
        print(f"Exported {catalog_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
