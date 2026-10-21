"""Keep the frontend's committed JSON contracts aligned with Python authority."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts import export_scene_contract

from roometry.scenes.edit_schemas import PreviewEditRequest, PreviewEditResponse
from roometry.scenes.schemas import RoomScene
from roometry.scenes.validation import validate_geometry
from roometry.scenes.service import preview_edit
from roometry.uploads.schemas import RoomInputOptions, UploadLimits, PreparedRoomInput, UploadErrorResponse
from roometry.vision.proposal_schemas import SceneProposalRequest, SceneProposalResponse
from roometry.vision.schemas import AnalysisErrorResponse, AnalyzeRoomRequest, RoomAnalysisResponse, RoomObservations

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("filename,model,mode", [
    ("scene.schema.json", RoomScene, "validation"),
    ("scene-proposal-request.schema.json", SceneProposalRequest, "validation"),
    ("scene-proposal-response.schema.json", SceneProposalResponse, "serialization"),
    ("edit-request.schema.json", PreviewEditRequest, "validation"),
    ("edit-response.schema.json", PreviewEditResponse, "serialization"),
    ("room-input-options.schema.json", RoomInputOptions, "validation"),
    ("room-input-limits.schema.json", UploadLimits, "serialization"),
    ("room-input-response.schema.json", PreparedRoomInput, "serialization"),
    ("room-input-error.schema.json", UploadErrorResponse, "serialization"),
    ("room-analysis-request.schema.json", AnalyzeRoomRequest, "validation"),
    ("room-observations.schema.json", RoomObservations, "validation"),
    ("room-analysis-response.schema.json", RoomAnalysisResponse, "serialization"),
    ("room-analysis-error.schema.json", AnalysisErrorResponse, "serialization"),
])
def test_frontend_contract_matches_python(filename, model, mode):
    exported = json.loads((ROOT / "frontend/src/lib" / filename).read_text())
    assert exported == model.model_json_schema(mode=mode)


@pytest.mark.parametrize("filename", ["bedroom_scene.json", "bedroom_after_scene.json"])
def test_frontend_fixture_matches_validated_source(filename):
    source = RoomScene.model_validate_json((ROOT / "data/fixtures" / filename).read_text())
    exported = json.loads((ROOT / "frontend/src/data" / filename).read_text())
    assert exported == source.model_dump(mode="json")
    assert validate_geometry(source).valid


def test_frontend_input_fixture_matches_validated_source():
    source = RoomInputOptions.model_validate_json((ROOT / "data/fixtures/room_input_options.json").read_text())
    exported = json.loads((ROOT / "frontend/src/data/room_input_options.json").read_text())
    assert exported == source.model_dump(mode="json")


def test_frontend_analysis_fixture_matches_validated_source():
    source = RoomAnalysisResponse.model_validate_json((ROOT / "data/fixtures/room_analysis.json").read_text())
    exported = json.loads((ROOT / "frontend/src/data/room_analysis.json").read_text())
    assert exported == source.model_dump(mode="json")


def test_export_refuses_inconsistent_analysis_before_writing_any_output(tmp_path, monkeypatch):
    fixtures = tmp_path / "data/fixtures"
    fixtures.mkdir(parents=True)
    for name in (*export_scene_contract.SCENE_FIXTURES, "room_input_options.json", "room_analysis.json"):
        data = json.loads((ROOT / "data/fixtures" / name).read_text())
        if name == "room_analysis.json":
            data["observations"]["objects"][0]["source_image_ids"] = ["44444444-4444-4444-8444-444444444444"]
        (fixtures / name).write_text(json.dumps(data))
    sentinel = tmp_path / "frontend/src/lib/scene.schema.json"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("unchanged contract")
    monkeypatch.setattr(export_scene_contract, "ROOT", tmp_path)
    with pytest.raises(ValidationError):
        export_scene_contract.main()
    assert sentinel.read_text() == "unchanged contract"
    assert not (tmp_path / "frontend/src/data").exists()


def test_comparison_preserves_identity_room_openings_and_protected_objects():
    before, after = [RoomScene.model_validate_json((ROOT / "data/fixtures" / name).read_text())
                     for name in ("bedroom_scene.json", "bedroom_after_scene.json")]
    assert before.room == after.room
    assert before.openings == after.openings
    assert [obj.id for obj in before.objects] == [obj.id for obj in after.objects]
    changed = []
    for old, new in zip(before.objects, after.objects):
        # Only movable poses change; assets, dimensions, colors and references stay stable.
        assert old.model_dump(exclude={"position", "rotation_y_rad"}) == new.model_dump(exclude={"position", "rotation_y_rad"})
        if old.fixed or old.locked:
            assert old == new
        if old != new:
            changed.append(old.id)
    assert set(changed) == {"desk_1", "computer_1", "wardrobe_1", "rug_1"}


def test_alternative_support_edit_is_accepted_and_collision_rolls_back():
    source = RoomScene.model_validate_json((ROOT / "data/fixtures/bedroom_after_scene.json").read_text())
    original = source.model_dump()
    accepted = preview_edit(PreviewEditRequest.model_validate({
        "scene": source, "edit": {"operation": "move", "object_id": "desk_1", "position": {"x": 0.7, "z": 1.35}},
    }))
    assert accepted.accepted
    assert set(accepted.changed_object_ids) == {"desk_1", "computer_1"}
    for obj in accepted.scene.objects:
        if obj.id in accepted.changed_object_ids:
            assert obj.position.x == pytest.approx(0.7)
            assert obj.position.z == pytest.approx(1.35)
    rejected = preview_edit(PreviewEditRequest.model_validate({
        "scene": accepted.scene, "edit": {"operation": "move", "object_id": "desk_1", "position": {"x": 2.7, "z": 2.0}},
    }))
    assert not rejected.accepted
    assert rejected.scene == accepted.scene
    assert source.model_dump() == original


@pytest.mark.parametrize("filename", ["bedroom_scene.json", "bedroom_after_scene.json"])
@pytest.mark.parametrize("failure", ["schema", "geometry"])
def test_export_refuses_invalid_fixtures_before_writing_any_output(tmp_path, monkeypatch, filename, failure):
    fixtures = tmp_path / "data/fixtures"
    fixtures.mkdir(parents=True)
    for name in ("bedroom_scene.json", "bedroom_after_scene.json"):
        data = json.loads((ROOT / "data/fixtures" / name).read_text())
        if name == filename:
            if failure == "schema":
                del data["room"]
            else:
                data["objects"][0]["position"]["x"] = 99.0
        (fixtures / name).write_text(json.dumps(data))
    sentinel = tmp_path / "frontend/src/lib/scene.schema.json"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("unchanged contract")
    monkeypatch.setattr(export_scene_contract, "ROOT", tmp_path)
    with pytest.raises(ValidationError if failure == "schema" else ValueError):
        export_scene_contract.main()
    assert sentinel.read_text() == "unchanged contract"
    assert not (tmp_path / "frontend/src/data").exists()


@pytest.mark.parametrize("filename", ["room_scene_proposal.json", "room_scene_partial.json"])
def test_frontend_scene_proposal_fixture_matches_validated_source(filename):
    source = SceneProposalResponse.model_validate_json((ROOT / "data/fixtures" / filename).read_text())
    exported = json.loads((ROOT / "frontend/src/data" / filename).read_text())
    assert exported == source.model_dump(mode="json")
    assert source.scene is not None and validate_geometry(source.scene).valid
