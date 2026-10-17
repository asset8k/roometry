"""Deterministic edit rules, support transforms, and transactional demo previews."""

from copy import deepcopy
from math import cos, pi, sin, tau

import pytest
from pydantic import ValidationError

from roometry.scenes.edit_schemas import (
    FloorPosition, MoveObject, PreviewEditRequest, RotateObject,
)
from roometry.scenes.mutations import EditRejected, apply_edit
from roometry.scenes.schemas import RoomScene, SceneObject
from roometry.scenes.service import preview_edit
from roometry.scenes.validation import validate_geometry


def move(object_id="desk_1", x=2.2, z=2.0):
    return MoveObject(operation="move", object_id=object_id, position=FloorPosition(x=x, z=z))


def rotate(object_id="desk_1", angle=pi / 2):
    return RotateObject(operation="rotate", object_id=object_id, rotation_y_rad=angle)


def find(scene: RoomScene, object_id: str) -> SceneObject:
    return next(obj for obj in scene.objects if obj.id == object_id)


def preview(scene, edit):
    return preview_edit(PreviewEditRequest(scene=scene, edit=edit))


def test_move_carries_computer_and_preserves_every_other_field(bedroom_scene):
    before = deepcopy(bedroom_scene)
    result = preview(bedroom_scene, move(x=0.85, z=2.1))
    assert result.accepted
    assert result.changed_object_ids == ("computer_1", "desk_1")
    assert result.base_validation.valid and result.candidate_validation.valid
    assert not result.edit_errors
    assert find(result.scene, "desk_1").position.model_dump() == {"x": 0.85, "y": 0.0, "z": 2.1}
    assert find(result.scene, "computer_1").position.model_dump() == {"x": 0.85, "y": 0.75, "z": 2.1}
    for old, new in zip(before.objects, result.scene.objects):
        assert old.model_dump(exclude={"position"}) == new.model_dump(exclude={"position"})
        if old.id not in result.changed_object_ids:
            assert old == new
    assert result.scene.room == before.room
    assert result.scene.openings == before.openings
    assert result.scene.assumptions == before.assumptions
    assert result.scene.warnings == before.warnings
    assert bedroom_scene == before


@pytest.mark.parametrize("object_id,x,z", [
    ("rug_1", 2.1, 1.8), ("wardrobe_1", 3.1, 0.35), ("desk_1", 0.85, 2.0),
])
def test_movable_floor_types_can_be_previewed(bedroom_scene, object_id, x, z):
    assert preview(bedroom_scene, move(object_id, x, z)).accepted


@pytest.mark.parametrize("initial_angle", [0.0, 0.4, tau - 0.3])
def test_rotation_uses_parent_pivot_and_relative_angle(supported_scene, initial_angle):
    # Arrange an independently rotated base using the shared Three.js convention.
    desk = find(supported_scene, "desk_1")
    child = find(supported_scene, "computer_1")
    desk.rotation_y_rad = initial_angle
    child.position.x = 2.0 + cos(initial_angle) * 0.5 + sin(initial_angle) * 0.1
    child.position.z = 2.0 - sin(initial_angle) * 0.5 + cos(initial_angle) * 0.1
    child.rotation_y_rad = (0.2 + initial_angle) % tau
    base = RoomScene.model_validate(supported_scene)
    before = deepcopy(base)
    old_child = find(base, "computer_1")
    result = preview(base, rotate(angle=initial_angle + pi / 2))
    assert result.accepted
    transformed = find(result.scene, "computer_1")
    assert transformed.position.x == pytest.approx(2.0 + old_child.position.z - 2.0)
    assert transformed.position.z == pytest.approx(2.0 - (old_child.position.x - 2.0))
    assert transformed.position.y == 0.75
    assert transformed.rotation_y_rad == pytest.approx((old_child.rotation_y_rad + pi / 2) % tau)
    assert transformed.dimensions_m == old_child.dimensions_m
    assert base == before


def test_translation_of_rotated_support_preserves_child_orientation(supported_scene):
    rotated = preview(supported_scene, rotate()).scene
    before = deepcopy(rotated)
    result = preview(rotated, move(x=2.2, z=1.8))
    assert result.accepted
    child = find(result.scene, "computer_1")
    old_child = find(before, "computer_1")
    assert child.position.x == pytest.approx(old_child.position.x + 0.2)
    assert child.position.z == pytest.approx(old_child.position.z - 0.2)
    assert child.rotation_y_rad == old_child.rotation_y_rad
    assert child.position.y == old_child.position.y
    assert rotated == before


def test_all_surface_children_transform_atomically_and_ids_are_sorted(supported_scene):
    lamp_data = find(supported_scene, "computer_1").model_dump()
    lamp_data.update(
        id="lamp_1", type="table_lamp", asset_id="table_lamp_standard_01",
        position={"x": 1.4, "y": 0.75, "z": 2.1},
        dimensions_m={"width": 0.2, "depth": 0.2, "height": 0.4},
    )
    supported_scene.objects.insert(0, SceneObject.model_validate(lamp_data))
    result = preview(supported_scene, rotate())
    assert result.accepted
    assert result.changed_object_ids == ("computer_1", "desk_1", "lamp_1")
    lamp = find(result.scene, "lamp_1")
    assert lamp.position.x == pytest.approx(2.1)
    assert lamp.position.z == pytest.approx(2.6)
    assert result.candidate_validation.valid


@pytest.mark.parametrize("angle,expected", [
    (-pi / 2, 3 * pi / 2), (tau, 0.0), (-tau, 0.0), (7 * tau + pi / 2, pi / 2),
])
def test_rotation_normalization_matches_scene_contract(supported_scene, angle, expected):
    result = preview(supported_scene, rotate(angle=angle))
    assert result.accepted
    assert find(result.scene, "desk_1").rotation_y_rad == pytest.approx(expected)
    assert all(0 <= obj.rotation_y_rad < tau for obj in result.scene.objects)


def test_repeating_absolute_edits_does_not_apply_deltas_twice(supported_scene):
    first = preview(supported_scene, move())
    second = preview(first.scene, move())
    assert second.accepted and not second.changed_object_ids
    assert second.scene == first.scene
    first_rotation = preview(second.scene, rotate())
    repeated_rotation = preview(first_rotation.scene, rotate())
    assert repeated_rotation.accepted and not repeated_rotation.changed_object_ids
    assert repeated_rotation.scene == first_rotation.scene


def test_four_quarter_turns_return_child_to_original_pose(supported_scene):
    original = deepcopy(supported_scene)
    current = supported_scene
    for angle in (pi / 2, pi, 3 * pi / 2, tau):
        result = preview(current, rotate(angle=angle))
        assert result.accepted
        current = result.scene
    for obj in original.objects:
        final = find(current, obj.id)
        assert final.position.x == pytest.approx(obj.position.x)
        assert final.position.z == pytest.approx(obj.position.z)
        assert final.rotation_y_rad == pytest.approx(obj.rotation_y_rad)
    assert supported_scene == original


@pytest.mark.parametrize("edit_factory", [move, rotate])
@pytest.mark.parametrize("object_id,code", [
    ("bed_1", "object_locked"), ("tv_1", "object_fixed"),
    ("chandelier_1", "object_fixed"), ("curtain_1", "object_fixed"),
    ("missing_1", "object_not_found"), ("door_1", "object_not_found"),
])
def test_protected_targets_and_unknown_ids_are_rejected(bedroom_scene, edit_factory, object_id, code):
    before = deepcopy(bedroom_scene)
    result = preview(bedroom_scene, edit_factory(object_id=object_id))
    assert not result.accepted
    assert result.edit_errors[0].code == code
    assert result.edit_errors[0].entity_ids == (object_id,)
    assert not result.changed_object_ids
    assert result.candidate_validation is None
    assert result.scene == bedroom_scene == before


@pytest.mark.parametrize("flag,code", [("locked", "object_locked"), ("fixed", "object_fixed")])
def test_floor_target_protection_includes_noop_requests(supported_scene, flag, code):
    setattr(find(supported_scene, "desk_1"), flag, True)
    result = preview(supported_scene, move(x=2.0, z=2.0))
    assert not result.accepted
    assert result.edit_errors[0].code == code


@pytest.mark.parametrize("object_id", ["computer_1", "tv_1", "chandelier_1"])
def test_direct_nonfloor_edits_are_deferred_even_when_unprotected(bedroom_scene, object_id):
    target = find(bedroom_scene, object_id)
    target.fixed = target.locked = False
    result = preview(bedroom_scene, rotate(object_id))
    assert not result.accepted
    assert result.edit_errors[0].code == "unsupported_placement"
    assert result.scene == bedroom_scene


@pytest.mark.parametrize("flag", ["locked", "fixed"])
@pytest.mark.parametrize("edit", [move(), rotate(), move(x=2.0 + 1e-9)])
def test_support_edit_cannot_bypass_child_protection_even_below_geometry_tolerance(supported_scene, flag, edit):
    setattr(find(supported_scene, "computer_1"), flag, True)
    before = deepcopy(supported_scene)
    result = preview(supported_scene, edit)
    assert not result.accepted
    assert result.edit_errors[0].code == "protected_children"
    assert result.edit_errors[0].entity_ids == ("desk_1", "computer_1")
    assert result.scene == supported_scene == before


@pytest.mark.parametrize("edit", [move(x=2.0, z=2.0), rotate(angle=tau)])
def test_parent_noop_does_not_move_protected_child(supported_scene, edit):
    find(supported_scene, "computer_1").locked = True
    result = preview(supported_scene, edit)
    assert result.accepted
    assert result.scene == supported_scene
    assert result.changed_object_ids == ()


@pytest.mark.parametrize("edit,expected_code", [
    (move(x=-0.5), "object_outside_room"),
    (move(x=2.7), "object_overlap"),
    (rotate("wardrobe_1"), "object_outside_room"),
])
def test_invalid_candidate_rolls_back_entire_scene(bedroom_scene, edit, expected_code):
    before = deepcopy(bedroom_scene)
    result = preview(bedroom_scene, edit)
    assert not result.accepted
    assert not result.edit_errors
    assert result.base_validation.valid
    assert not result.candidate_validation.valid
    assert expected_code in {issue.code for issue in result.candidate_validation.errors}
    assert not result.changed_object_ids
    assert result.scene == bedroom_scene == before


def test_child_schema_limit_failure_is_a_structured_rejection(supported_scene):
    before = deepcopy(supported_scene)
    result = preview(supported_scene, move(x=100.0))
    assert not result.accepted
    assert result.edit_errors[0].code == "candidate_schema"
    assert result.edit_errors[0].entity_ids == ("computer_1",)
    assert result.candidate_validation is None
    assert result.scene == supported_scene == before


def test_invalid_base_is_reported_and_no_repair_edit_is_attempted(bedroom_scene):
    find(bedroom_scene, "desk_1").position.x = -0.5
    before = deepcopy(bedroom_scene)
    result = preview(bedroom_scene, move(x=0.65))
    assert not result.accepted
    assert not result.base_validation.valid
    assert result.candidate_validation is None
    assert result.edit_errors[0].code == "invalid_base_geometry"
    assert "desk_1" in result.edit_errors[0].entity_ids
    assert result.scene == bedroom_scene == before


@pytest.mark.parametrize("accepted", [True, False])
def test_returned_scene_is_independent_of_caller_input(supported_scene, accepted):
    before = deepcopy(supported_scene)
    result = preview(supported_scene, move(x=2.2 if accepted else -0.5))
    assert result.accepted is accepted
    find(result.scene, "desk_1").position.x = 99.0
    assert supported_scene == before


def test_pure_mutator_returns_candidate_and_service_handles_geometry(bedroom_scene):
    before = deepcopy(bedroom_scene)
    candidate = apply_edit(bedroom_scene, move(x=-0.5))
    assert not validate_geometry(candidate).valid
    assert bedroom_scene == before
    with pytest.raises(EditRejected, match="Locked"):
        apply_edit(bedroom_scene, rotate("bed_1"))


def test_model_instances_are_revalidated_at_service_and_mutator_boundaries(supported_scene):
    request = PreviewEditRequest(scene=supported_scene, edit=rotate())
    request.edit.rotation_y_rad = float("nan")
    with pytest.raises(ValidationError):
        preview_edit(request)
    invalid = RotateObject.model_construct(operation="rotate", object_id="desk_1", rotation_y_rad=float("inf"))
    with pytest.raises(ValidationError):
        apply_edit(supported_scene, invalid)


@pytest.mark.parametrize("extra", [
    {"locked": False}, {"fixed": False}, {"color": "#000000"},
    {"dimensions_m": {"width": 1.0, "height": 1.0, "depth": 1.0}},
])
def test_operation_contract_rejects_other_mutations(supported_scene, extra):
    data = {"scene": supported_scene.model_dump(), "edit": rotate().model_dump() | extra}
    with pytest.raises(ValidationError):
        PreviewEditRequest.model_validate(data)


@pytest.mark.parametrize("value", [True, "1.0", None, float("nan"), float("inf"), -float("inf")])
def test_edit_angles_are_strict_and_finite(value):
    with pytest.raises(ValidationError):
        RotateObject.model_validate({"operation": "rotate", "object_id": "desk_1", "rotation_y_rad": value})


@pytest.mark.parametrize("value", [True, "1.0", None, float("nan"), float("inf"), 100.1, -100.1])
def test_edit_coordinates_are_strict_finite_and_bounded(value):
    with pytest.raises(ValidationError):
        FloorPosition.model_validate({"x": value, "z": 2.0})


def test_move_cannot_change_floor_elevation():
    with pytest.raises(ValidationError):
        FloorPosition.model_validate({"x": 1.0, "y": 0.1, "z": 2.0})
