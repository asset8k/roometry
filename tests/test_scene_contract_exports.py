"""Keep the frontend's committed JSON contracts aligned with Python authority."""

import json
from pathlib import Path

import pytest

from roometry.scenes.edit_schemas import PreviewEditRequest, PreviewEditResponse
from roometry.scenes.schemas import RoomScene

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("filename,model,mode", [
    ("scene.schema.json", RoomScene, "validation"),
    ("edit-request.schema.json", PreviewEditRequest, "validation"),
    ("edit-response.schema.json", PreviewEditResponse, "serialization"),
])
def test_frontend_contract_matches_python(filename, model, mode):
    exported = json.loads((ROOT / "frontend/src/lib" / filename).read_text())
    assert exported == model.model_json_schema(mode=mode)


def test_frontend_fixture_matches_validated_source():
    source = RoomScene.model_validate_json((ROOT / "data/fixtures/bedroom_scene.json").read_text())
    exported = json.loads((ROOT / "frontend/src/data/bedroom_scene.json").read_text())
    assert exported == source.model_dump(mode="json")
