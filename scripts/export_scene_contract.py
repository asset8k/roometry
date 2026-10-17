"""Export scene/edit contracts and the validated demo fixture for the frontend."""

import json
from pathlib import Path

from roometry.scenes.schemas import RoomScene
from roometry.scenes.edit_schemas import PreviewEditRequest, PreviewEditResponse

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    scene = RoomScene.model_validate_json(
        (ROOT / "data/fixtures/bedroom_scene.json").read_text()
    )
    outputs = {
        ROOT / "frontend/src/lib/scene.schema.json": RoomScene.model_json_schema(),
        ROOT / "frontend/src/data/bedroom_scene.json": scene.model_dump(mode="json"),
        ROOT / "frontend/src/lib/edit-request.schema.json": PreviewEditRequest.model_json_schema(),
        ROOT / "frontend/src/lib/edit-response.schema.json": PreviewEditResponse.model_json_schema(mode="serialization"),
    }
    for path, data in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
        print(f"Exported {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
