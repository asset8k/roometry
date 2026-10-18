"""Export scene/edit contracts and baseline-validated demo fixtures."""

import json
from pathlib import Path

from roometry.scenes.schemas import RoomScene
from roometry.scenes.edit_schemas import PreviewEditRequest, PreviewEditResponse
from roometry.scenes.validation import validate_geometry

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    outputs = {
        ROOT / "frontend/src/lib/scene.schema.json": RoomScene.model_json_schema(),
        ROOT / "frontend/src/lib/edit-request.schema.json": PreviewEditRequest.model_json_schema(),
        ROOT / "frontend/src/lib/edit-response.schema.json": PreviewEditResponse.model_json_schema(mode="serialization"),
    }
    for filename in ("bedroom_scene.json", "bedroom_after_scene.json"):
        scene = RoomScene.model_validate_json((ROOT / "data/fixtures" / filename).read_text())
        report = validate_geometry(scene)
        if not report.valid:
            raise ValueError(f"{filename} failed baseline geometry: {report.errors}")
        outputs[ROOT / "frontend/src/data" / filename] = scene.model_dump(mode="json")
    for path, data in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
        print(f"Exported {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
