"""Baseline spatial checks; a passing report is not full physical acceptance."""

from copy import deepcopy
from math import pi, sqrt, tau
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError

from roometry.scenes.assets import ObjectType, Placement
from roometry.scenes.geometry import (
    ROTATION_TOLERANCE_RAD,
    TOLERANCE_M,
    boxes_overlap,
    object_bounds,
    rectangles_overlap,
)
from roometry.scenes.schemas import (
    ObjectDimensions, Opening, Position, RoomDimensions, RoomScene, SceneObject, Wall,
)
from roometry.scenes.validation import GeometryReport, validate_geometry

FIXTURE = Path(__file__).resolve().parents[1] / "data/fixtures/bedroom_scene.json"


def room(width: float = 4.0, length: float = 4.0, height: float = 3.0) -> RoomDimensions:
    return RoomDimensions(
        room_type="bedroom", width_m=width, length_m=length, height_m=height,
        measurements_source="estimated",
    )


def obj(
    object_id: str = "chair_1", *, kind: ObjectType = "chair",
    x: float = 2.0, y: float = 0.0, z: float = 2.0,
    width: float = 1.0, depth: float = 1.0, height: float = 1.0,
    rotation: float = 0.0, placement: Placement = "floor",
    support_id: str | None = None, wall: Wall | None = None,
    opening_id: str | None = None,
) -> SceneObject:
    return SceneObject(
        id=object_id, type=kind, asset_id=f"{kind}_standard_01",
        position=Position(x=x, y=y, z=z), rotation_y_rad=rotation,
        dimensions_m=ObjectDimensions(width=width, depth=depth, height=height),
        color="#808080", placement=placement, support_object_id=support_id,
        wall=wall, opening_id=opening_id, fixed=False, locked=False,
        provenance="user_added", measurements_source="estimated",
    )


def opening(
    opening_id: str = "window_1", *, kind: Literal["door", "window"] = "window",
    wall: Wall = "north", offset: float = 1.5, width: float = 1.0,
    bottom: float = 1.0, height: float = 1.0,
) -> Opening:
    return Opening(
        id=opening_id, type=kind, wall=wall, offset_m=offset, width_m=width,
        height_m=height, sill_height_m=bottom, swing="none", hinge=None,
        provenance="user_added", measurements_source="estimated",
    )


def scene(
    objects: list[SceneObject] | None = None, *,
    openings: list[Opening] | None = None, dimensions: RoomDimensions | None = None,
) -> RoomScene:
    return RoomScene(
        schema_version=1, room=dimensions if dimensions is not None else room(),
        objects=objects if objects is not None else [],
        openings=openings if openings is not None else [], assumptions=[], warnings=[],
    )


def codes(report: GeometryReport) -> set[str]:
    return {issue.code for issue in report.errors}


def wall_item(wall: Wall = "north", **changes) -> SceneObject:
    # Unequal room dimensions catch east/west versus north/south axis mistakes.
    positions: dict[Wall, tuple[float, float, float]] = {
        "north": (2.0, 0.05, 0.0), "south": (2.0, 2.95, pi),
        "west": (0.05, 1.5, pi / 2), "east": (4.95, 1.5, 3 * pi / 2),
    }
    x, z, rotation = positions[wall]
    values = dict(x=x, z=z, rotation=rotation, y=1.0, width=1.0, depth=0.1, height=0.5)
    values.update(changes)
    return obj("tv_1", kind="tv", placement="wall", wall=wall, **values)


def test_current_fixture_passes_baseline_without_mutation():
    original = RoomScene.model_validate_json(FIXTURE.read_text())
    before = deepcopy(original)
    report = validate_geometry(original)
    assert report.valid
    assert not report.errors
    assert report.policy_version == "components-v1"
    assert report.tolerance_m == TOLERANCE_M
    assert report.model_dump(mode="json")["valid"] is True
    assert report.model_dump(mode="json")["warnings"][0]["code"] == "baseline_scope"
    assert "not full acceptance" in report.warnings[0].message
    assert original == before


def test_empty_scene_is_valid_for_the_baseline_but_still_discloses_scope():
    report = validate_geometry(scene())
    assert report.valid
    assert report.warnings


@pytest.mark.parametrize("attribute,value", [("x", float("nan")), ("y", 0.1)])
def test_mutated_models_are_structurally_revalidated(attribute, value):
    candidate = scene([obj()])
    setattr(candidate.objects[0].position, attribute, value)
    with pytest.raises(ValidationError):
        validate_geometry(candidate)


def test_unknown_asset_raises_schema_error_instead_of_geometry_report():
    candidate = scene([obj()])
    candidate.objects[0].asset_id = "unknown_asset"
    with pytest.raises(ValidationError, match="Unknown asset_id"):
        validate_geometry(candidate)


def test_failures_are_structured_serializable_and_stable_across_list_order():
    candidate = scene([obj("chair_b"), obj("chair_a"), obj("chair_c", x=0.0)])
    before = deepcopy(candidate)
    report = validate_geometry(candidate)
    assert not report.valid
    assert {issue.entity_ids for issue in report.errors if issue.code == "object_overlap"} == {("chair_a", "chair_b")}
    assert report.model_dump(mode="json")["valid"] is False
    assert all(issue.entity_ids and issue.message for issue in report.errors)
    assert candidate == before
    candidate.objects.reverse()
    assert validate_geometry(candidate) == report


@pytest.mark.parametrize("width,length", [(4.0, 4.0), (5.0, 3.0), (3.0, 5.0)])
@pytest.mark.parametrize("rotation", [0.0, pi / 2, pi, 3 * pi / 2, tau])
def test_exact_room_boundary_contact_and_square_rooms(width, length, rotation):
    quarter_turn = rotation in {pi / 2, 3 * pi / 2}
    item = obj(
        x=width / 2, z=length / 2,
        width=length if quarter_turn else width,
        depth=width if quarter_turn else length, rotation=rotation,
    )
    assert validate_geometry(scene([item], dimensions=room(width, length))).valid


@pytest.mark.parametrize("changes", [
    {"x": 0.49}, {"x": 3.51}, {"z": 0.49}, {"z": 3.51}, {"height": 3.01},
    {"x": 0.6, "rotation": pi / 4},
])
def test_bounds_detect_every_room_edge_height_and_rotated_corners(changes):
    report = validate_geometry(scene([obj(**changes)]))
    assert codes(report) == {"object_outside_room"}


@pytest.mark.parametrize("excess,valid", [(0.0, True), (0.5 * TOLERANCE_M, True), (2 * TOLERANCE_M, False)])
def test_containment_distance_tolerance(excess, valid):
    assert validate_geometry(scene([obj(x=0.5 - excess)])).valid is valid
    assert validate_geometry(scene([obj(height=3.0 + excess)])).valid is valid


def test_bounds_follow_threejs_rotation_and_bottom_center_pivot():
    item = obj(
        kind="computer", placement="surface", support_id="desk_1", y=0.75,
        width=2.0, depth=1.0, height=0.45, rotation=pi / 2,
    )
    bounds = object_bounds(item)
    assert bounds.vertical == pytest.approx((0.75, 1.2))
    assert bounds.footprint.axes[1] == pytest.approx((1.0, 0.0))  # local front
    assert bounds.footprint.project((1.0, 0.0)) == pytest.approx((1.5, 2.5))
    assert bounds.footprint.project((0.0, 1.0)) == pytest.approx((1.0, 3.0))


@pytest.mark.parametrize("separation,overlaps", [
    (0.0, True), (0.9, True), (1.0 - 2 * TOLERANCE_M, True),
    (1.0 - 0.5 * TOLERANCE_M, False), (1.0, False), (1.1, False),
])
def test_solid_overlap_contact_and_tolerance(separation, overlaps):
    report = validate_geometry(scene([obj("chair_a", x=1.5), obj("chair_b", x=1.5 + separation)]))
    assert ("object_overlap" in codes(report)) is overlaps
    assert report.valid is not overlaps


@pytest.mark.parametrize("angle", [0.0, pi / 6, pi / 4, pi / 2])
def test_rotated_crossing_rectangles_overlap_symmetrically(angle):
    a = object_bounds(obj(width=2.0, depth=0.2, rotation=angle))
    b = object_bounds(obj(width=2.0, depth=0.2, rotation=angle + pi / 2))
    assert boxes_overlap(a, b)
    assert boxes_overlap(b, a)


def test_separating_axes_avoid_false_collisions_from_world_bounding_boxes():
    # Parallel diagonal strips with overlapping world AABBs, separated along
    # their narrow local axis. A world-axis-only check would reject this layout.
    a = obj("chair_a", x=1.5, z=1.5, width=2.0, depth=0.2, rotation=pi / 4)
    b = obj("chair_b", x=1.5 + 0.4 / sqrt(2), z=1.5 + 0.4 / sqrt(2), width=2.0, depth=0.2, rotation=pi / 4)
    assert validate_geometry(scene([a, b])).valid


def test_completely_contained_object_is_a_collision():
    report = validate_geometry(scene([obj("chair_a", width=3.0, depth=3.0), obj("chair_b", width=0.1, depth=0.1)]))
    assert codes(report) == {"object_overlap"}


@pytest.mark.parametrize("bottom,height,overlaps", [
    (2.1, 0.9, False), (2.0, 1.0, False),
    (2.0 - 0.5 * TOLERANCE_M, 1.0 + 0.5 * TOLERANCE_M, False),
    (2.0 - 2 * TOLERANCE_M, 1.0 + 2 * TOLERANCE_M, True),
    (1.9, 1.1, True),
])
def test_overhead_objects_use_vertical_extent_instead_of_only_footprint(bottom, height, overlaps):
    overhead = obj("projector_1", kind="projector", placement="ceiling", y=bottom, height=height)
    report = validate_geometry(scene([obj(height=2.0), overhead]))
    assert ("object_overlap" in codes(report)) is overlaps
    assert report.valid is not overlaps


def test_rug_can_overlap_furniture_but_still_requires_room_containment():
    carpet = obj("rug_1", kind="rug", width=2.0, depth=2.0, height=0.01)
    assert validate_geometry(scene([obj(), carpet])).valid
    carpet.position.x = 0.5
    report = validate_geometry(scene([obj(), carpet]))
    assert codes(report) == {"object_outside_room"}


@pytest.mark.parametrize("thickness", [0.01, TOLERANCE_M / 10])
def test_rug_layer_exception_never_allows_two_overlapping_rugs(thickness):
    a = obj("rug_a", kind="rug", x=1.0, width=2.0, height=thickness)
    b = obj("rug_b", kind="rug", x=2.0, width=2.0, height=thickness)
    report = validate_geometry(scene([a, b]))
    assert codes(report) == {"rug_overlap"}
    assert report.errors[0].entity_ids == ("rug_a", "rug_b")


def test_rugs_may_touch_at_their_edges():
    a = obj("rug_a", kind="rug", x=1.0, width=2.0, height=0.01)
    b = obj("rug_b", kind="rug", x=3.0, width=2.0, height=0.01)
    assert validate_geometry(scene([a, b])).valid


@pytest.mark.parametrize("angle", [0.0, pi / 4, pi / 2])
def test_child_fits_on_rotated_support_and_exact_top_contact_is_allowed(angle):
    desk = obj("desk_1", kind="desk", width=2.0, height=0.75, rotation=angle)
    computer = obj("computer_1", kind="computer", placement="surface", support_id="desk_1", y=0.75, width=0.5, depth=0.3, height=0.45, rotation=angle)
    assert validate_geometry(scene([desk, computer])).valid


@pytest.mark.parametrize("offset,valid", [
    (0.0, True), (0.5 * TOLERANCE_M, True), (-0.5 * TOLERANCE_M, True),
    (2 * TOLERANCE_M, False), (-2 * TOLERANCE_M, False),
])
def test_surface_contact_rejects_floating_or_penetrating_children(offset, valid):
    desk = obj("desk_1", kind="desk", height=0.75)
    computer = obj("computer_1", kind="computer", placement="surface", support_id="desk_1", y=0.75 + offset, width=0.5, depth=0.3, height=0.45)
    report = validate_geometry(scene([desk, computer]))
    assert report.valid is valid
    if not valid:
        assert "surface_support" in codes(report)
        assert ("object_overlap" in codes(report)) is (offset < 0)


def test_rotated_child_must_fit_all_corners_not_just_center():
    desk = obj("desk_1", kind="desk", height=0.75)
    computer = obj("computer_1", kind="computer", placement="surface", support_id="desk_1", y=0.75, width=0.9, depth=0.9, height=0.45, rotation=pi / 4)
    report = validate_geometry(scene([desk, computer]))
    assert codes(report) == {"surface_support"}
    assert report.errors[0].entity_ids == ("computer_1", "desk_1")


def test_support_containment_uses_local_axes_not_world_aabb():
    desk = obj("desk_1", kind="desk", width=2.0, depth=0.4, height=0.75, rotation=pi / 4)
    computer = obj("computer_1", kind="computer", placement="surface", support_id="desk_1", x=2 + 0.3 / sqrt(2), z=2 + 0.3 / sqrt(2), y=0.75, width=0.2, depth=0.2, height=0.45, rotation=pi / 4)
    assert codes(validate_geometry(scene([desk, computer]))) == {"surface_support"}


def test_wall_shelf_can_support_a_surface_object():
    shelf = obj("wall_shelf_1", kind="wall_shelf", placement="wall", wall="north", z=0.2, y=1.0, width=1.0, depth=0.4, height=0.1)
    vase = obj("vase_1", kind="vase", placement="surface", support_id=shelf.id, z=0.2, y=1.1, width=0.15, depth=0.15, height=0.3)
    assert validate_geometry(scene([shelf, vase])).valid


@pytest.mark.parametrize("wall", ["north", "south", "west", "east"])
def test_all_wall_orientations_and_depth_offsets(wall):
    candidate = scene([wall_item(wall)], dimensions=room(5.0, 3.0))
    assert validate_geometry(candidate).valid
    candidate.objects[0].rotation_y_rad += pi
    assert "wall_attachment" in codes(validate_geometry(candidate))


@pytest.mark.parametrize("gap,valid", [
    (0.0, True), (0.5 * TOLERANCE_M, True), (-0.5 * TOLERANCE_M, True),
    (2 * TOLERANCE_M, False), (-2 * TOLERANCE_M, False), (0.01, False),
])
def test_wall_back_contact_tolerance(gap, valid):
    item = wall_item(z=0.05 + gap)
    report = validate_geometry(scene([item], dimensions=room(5.0, 3.0)))
    assert report.valid is valid
    if not valid:
        assert "wall_attachment" in codes(report)


def test_north_facing_angle_can_wrap_around_tau_within_tolerance():
    item = wall_item(rotation=-0.5 * ROTATION_TOLERANCE_RAD)
    assert validate_geometry(scene([item], dimensions=room(5.0, 3.0))).valid


def test_wall_back_edge_must_touch_across_full_width():
    # The center is at the nominal depth offset, but a small angular error on
    # a long object moves its back corners beyond the distance tolerance.
    item = wall_item(width=4.0, rotation=0.75 * ROTATION_TOLERANCE_RAD)
    assert "wall_attachment" in codes(validate_geometry(scene([item], dimensions=room(5.0, 3.0))))


@pytest.mark.parametrize("gap,valid", [
    (0.0, True), (0.5 * TOLERANCE_M, True), (-0.5 * TOLERANCE_M, True),
    (2 * TOLERANCE_M, False), (-2 * TOLERANCE_M, False), (0.1, False),
])
def test_ceiling_top_contact(gap, valid):
    projector = obj("projector_1", kind="projector", placement="ceiling", y=2.3 - gap, height=0.7)
    report = validate_geometry(scene([projector]))
    assert report.valid is valid
    if not valid:
        assert "ceiling_attachment" in codes(report)


@pytest.mark.parametrize("wall,span", [("north", 5.0), ("south", 5.0), ("east", 3.0), ("west", 3.0)])
@pytest.mark.parametrize("excess,valid", [(0.0, True), (0.5 * TOLERANCE_M, True), (2 * TOLERANCE_M, False)])
def test_opening_bounds_use_host_wall_span_and_tolerance(wall, span, excess, valid):
    window = opening(wall=wall, offset=span - 1.0 + excess)
    report = validate_geometry(scene(openings=[window], dimensions=room(5.0, 3.0)))
    assert report.valid is valid
    if not valid:
        assert codes(report) == {"opening_outside_wall"}
        assert report.errors[0].entity_ids == ("window_1",)


@pytest.mark.parametrize("excess,valid", [(0.0, True), (0.5 * TOLERANCE_M, True), (2 * TOLERANCE_M, False), (0.1, False)])
def test_opening_top_must_fit_wall_height(excess, valid):
    report = validate_geometry(scene(openings=[opening(bottom=2.0 + excess)]))
    assert report.valid is valid
    if not valid:
        assert codes(report) == {"opening_outside_wall"}


@pytest.mark.parametrize("kind,bottom,height", [("window", 1.0, 1.0), ("door", 0.0, 2.1)])
def test_wall_objects_cannot_overlap_doors_or_windows(kind, bottom, height):
    item = wall_item()
    portal = opening(kind=kind, bottom=bottom, height=height)
    report = validate_geometry(scene([item], openings=[portal], dimensions=room(5.0, 3.0)))
    assert codes(report) == {"wall_opening_overlap"}
    assert report.errors[0].entity_ids == ("tv_1", "window_1")


@pytest.mark.parametrize("changes", [{"offset": 2.5}, {"bottom": 1.5}, {"wall": "east"}])
def test_opening_edge_contact_vertical_contact_and_different_wall_are_allowed(changes):
    report = validate_geometry(scene([wall_item()], openings=[opening(**changes)], dimensions=room(5.0, 3.0)))
    assert report.valid


@pytest.mark.parametrize("wall", ["north", "south", "east", "west"])
def test_opening_conflicts_use_along_wall_axis_for_all_walls(wall):
    offset = 1.5 if wall in {"north", "south"} else 1.0
    portal = opening(wall=wall, offset=offset)
    report = validate_geometry(scene([wall_item(wall)], openings=[portal], dimensions=room(5.0, 3.0)))
    assert codes(report) == {"wall_opening_overlap"}


def curtain() -> SceneObject:
    return obj("curtain_1", kind="window_covering", placement="wall", wall="north", opening_id="window_1", z=0.025, y=0.8, width=2.0, depth=0.05, height=1.4)


def test_curtain_can_cover_only_its_linked_window():
    linked = opening(offset=1.4, width=1.2)
    assert validate_geometry(scene([curtain()], openings=[linked])).valid
    unrelated = opening("window_2", offset=2.7, width=0.8)
    report = validate_geometry(scene([curtain()], openings=[linked, unrelated]))
    assert codes(report) == {"wall_opening_overlap"}
    assert report.errors[0].entity_ids == ("curtain_1", "window_2")


def test_curtain_exception_never_allows_covering_an_unrelated_door():
    door = opening("door_1", kind="door", offset=2.7, width=0.8, bottom=0.0, height=2.1)
    report = validate_geometry(scene([curtain()], openings=[opening(offset=1.4, width=1.2), door]))
    assert codes(report) == {"wall_opening_overlap"}
    assert report.errors[0].entity_ids == ("curtain_1", "door_1")


def test_curtain_can_cover_its_linked_door_but_not_another_opening():
    item = curtain().model_copy(update={"opening_id": "patio_door"})
    patio_door = opening("patio_door", kind="door", offset=1.4, width=1.2, bottom=0, height=2.1)
    assert validate_geometry(scene([item], openings=[patio_door])).valid
    another_door = opening("another_door", kind="door", offset=2.7, width=0.8, bottom=0, height=2.1)
    report = validate_geometry(scene([item], openings=[patio_door, another_door]))
    assert codes(report) == {"wall_opening_overlap"}
    assert report.errors[0].entity_ids == ("curtain_1", "another_door")


def test_curtain_is_still_solid_for_object_collisions():
    clock = obj("clock_1", kind="clock", placement="wall", wall="north", x=1.2, z=0.025, y=1.0, width=0.3, depth=0.05, height=0.3)
    report = validate_geometry(scene([curtain(), clock], openings=[opening(offset=1.4, width=1.2)]))
    assert codes(report) == {"object_overlap"}
    assert report.errors[0].entity_ids == ("clock_1", "curtain_1")


def test_rug_overlap_test_uses_rotated_footprints():
    a = obj("rug_a", kind="rug", width=2.0, depth=0.2, height=0.01, rotation=pi / 4)
    b = obj("rug_b", kind="rug", width=2.0, depth=0.2, height=0.01, rotation=3 * pi / 4)
    assert rectangles_overlap(object_bounds(a).footprint, object_bounds(b).footprint)
    assert codes(validate_geometry(scene([a, b]))) == {"rug_overlap"}
