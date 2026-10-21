"""Physical regressions for catalog gaps, supports, transforms and manual edits."""

from copy import deepcopy
from itertools import product
from math import cos, pi, sin

import pytest

from roometry.scenes.assets import ASSET_REGISTRY, Placement
from roometry.scenes.catalog import ASSET_CATALOG, dimensions_for
from roometry.scenes.collision import component_bounds, contact_bounds
from roometry.scenes.edit_schemas import PreviewEditRequest
from roometry.scenes.geometry import TOLERANCE_M, boxes_overlap, object_bounds, rectangle_contains
from roometry.scenes.schemas import SceneObject
from roometry.scenes.service import preview_edit
from roometry.scenes.validation import validate_geometry
from tests.test_geometry import codes, scene
from roometry.vision.proposal_schemas import SceneDraft
from roometry.vision.proposals import assemble
from roometry.vision.schemas import RoomAnalysisResponse
from tests.test_catalog_assembly import single_object_case
from tests.test_scene_proposals import measurements
from tests import test_vision_api
from tests.test_scene_proposals import analyzed, body, confirmed, scene_action

anyio_backend = test_vision_api.anyio_backend
isolated_environment = test_vision_api.isolated_environment
harness = test_vision_api.harness


def model(asset_id, object_id="item", *, placement=None, x=2., y=0., z=2., angle=0.,
          width=None, height=None, depth=None, support=None, surface=None):
    asset = ASSET_REGISTRY[asset_id]
    placement = placement or sorted(asset.placements)[0]
    w, h, d = dimensions_for(asset_id, placement)
    return SceneObject.model_validate({
        "id": object_id, "type": asset.object_type, "asset_id": asset_id,
        "position": {"x": x, "y": y, "z": z}, "rotation_y_rad": angle,
        "dimensions_m": {"width": w if width is None else width,
                         "height": h if height is None else height,
                         "depth": d if depth is None else depth},
        "placement": placement, "color": "#808080", "fixed": False, "locked": False,
        "support_object_id": support or ("host" if placement == "surface" else None),
        "support_surface_id": surface, "wall": "north" if placement == "wall" else None,
        "opening_id": "opening" if asset.object_type == "window_covering" else None,
        "provenance": "user_added", "measurements_source": "catalog",
    })


def rotate_pair(objects, angle, center=(2., 2.)):
    result = deepcopy(objects)
    for obj in result:
        x, z = obj.position.x - center[0], obj.position.z - center[1]
        obj.position.x = center[0] + cos(angle) * x + sin(angle) * z
        obj.position.z = center[1] - sin(angle) * x + cos(angle) * z
        obj.rotation_y_rad += angle
    return result


@pytest.mark.parametrize("angle", [0., pi / 4, pi / 2])
def test_partly_tucked_chair_passes_but_a_back_or_leg_in_the_table_fails(angle):
    table = model("table_standard_01", "table")
    chair = model("chair_standard_01", "chair", z=1.4)
    assert boxes_overlap(object_bounds(table), object_bounds(chair))
    objects = rotate_pair([table, chair], angle)
    original = deepcopy(objects)
    assert validate_geometry(scene(objects)).valid
    assert objects == original
    # Move the back through the tabletop, without changing dimensions.
    chair.position.z = 1.8
    assert "object_overlap" in codes(validate_geometry(scene(rotate_pair([table, chair], angle))))
    # A chair at the end leg intersects actual timber below the tabletop.
    chair.position.x, chair.position.z = 2.72, 1.58
    assert "object_overlap" in codes(validate_geometry(scene(rotate_pair([table, chair], angle))))


@pytest.mark.parametrize("side,sign", [("left", 1), ("right", -1)])
@pytest.mark.parametrize("angle", [0., pi / 4, pi / 2])
def test_sectional_empty_corner_stays_empty_and_chaise_is_still_solid(side, sign, angle):
    sofa = model(f"sofa_sectional_{side}_01", "sofa")
    stool = model("stool_standard_01", "stool", x=2 + sign * .7, z=2.45)
    assert boxes_overlap(object_bounds(sofa), object_bounds(stool))
    assert validate_geometry(scene(rotate_pair([sofa, stool], angle))).valid
    stool.position.x = 2 - sign * .7
    assert "object_overlap" in codes(validate_geometry(scene(rotate_pair([sofa, stool], angle))))


@pytest.mark.parametrize("angle", [0., pi / 4, pi / 2])
def test_internal_shelf_uses_named_board_and_catches_the_next_board_and_back(angle):
    shelf = model("shelf_standard_01", "shelf")
    books = model("book_stack_standard_01", "books", placement="surface",
                  y=.35 * 1.8, support="shelf", surface="shelf_1")
    original = scene(rotate_pair([shelf, books], angle))
    assert validate_geometry(original).valid
    books.dimensions_m.height = .60
    report = validate_geometry(scene(rotate_pair([shelf, books], angle)))
    assert "object_overlap" in codes(report) and "surface_support" not in codes(report)
    books.dimensions_m.height = .14
    books.position.z -= .15
    assert "object_overlap" in codes(validate_geometry(scene(rotate_pair([shelf, books], angle))))
    assert validate_geometry(original).valid


def test_null_selection_remains_top_only_and_a_wrong_named_board_does_not_pass():
    shelf = model("shelf_standard_01", "shelf")
    books = model("book_stack_standard_01", "books", placement="surface",
                  y=.35 * 1.8, support="shelf", surface="shelf_1")
    assert validate_geometry(scene([shelf, books])).valid
    for selected in (None, "shelf_2"):
        books.support_surface_id = selected
        assert "surface_support" in codes(validate_geometry(scene([shelf, books])))


def test_console_center_shelf_supports_a_console_without_exempting_its_host():
    stand = model("tv_stand_standard_01", "stand")
    console = model("game_console_standard_01", "console", placement="surface",
                    y=.5575 * .55, support="stand", surface="center_shelf")
    assert validate_geometry(scene([stand, console])).valid
    console.dimensions_m.height = .30
    assert "object_overlap" in codes(validate_geometry(scene([stand, console])))


@pytest.mark.parametrize("angle", [0., pi / 4, pi / 2])
def test_sink_ledge_is_a_surface_but_the_hole_and_faucet_height_are_not(angle):
    sink = model("kitchen_counter_sink_01", "sink")
    soap = model("household_accessory_soap_dispenser_01", "soap", placement="surface",
                 x=2 - .39 * 1.2, y=.74 * 1.24, support="sink", surface="deck_left")
    assert validate_geometry(scene(rotate_pair([sink, soap], angle))).valid
    soap.position.x = 2.
    assert "surface_support" in codes(validate_geometry(scene(rotate_pair([sink, soap], angle))))
    soap.position.x = 2 - .39 * 1.2
    soap.position.y = 1.24
    assert "surface_support" in codes(validate_geometry(scene(rotate_pair([sink, soap], angle))))


def test_plant_can_overhang_a_small_top_only_when_its_solid_base_fits():
    desk = model("desk_standard_01", "desk", width=.30, depth=.17)
    plant = model("plant_standard_01", "plant", placement="surface", y=.75,
                  support="desk", surface="tabletop")
    assert plant.dimensions_m.depth > desk.dimensions_m.depth
    assert validate_geometry(scene([desk, plant])).valid
    desk.dimensions_m.depth = .10
    assert "surface_support" in codes(validate_geometry(scene([desk, plant])))


def test_manual_edit_uses_the_same_component_rules_and_rolls_back_an_intersection():
    table = model("table_standard_01", "table")
    chair = model("chair_standard_01", "chair", z=1.4)
    original = scene([table, chair])
    accepted = preview_edit(PreviewEditRequest.model_validate({
        "scene": original, "edit": {"operation": "move", "object_id": "chair", "position": {"x": 2.1, "z": 1.4}},
    }))
    assert accepted.accepted
    rejected = preview_edit(PreviewEditRequest.model_validate({
        "scene": original, "edit": {"operation": "move", "object_id": "chair", "position": {"x": 2., "z": 1.8}},
    }))
    assert not rejected.accepted and rejected.scene == original
    assert rejected.candidate_validation is not None
    assert "object_overlap" in codes(rejected.candidate_validation)


def test_moving_a_shelf_carries_its_lower_shelf_item_at_the_same_height():
    shelf = model("shelf_standard_01", "shelf")
    books = model("book_stack_standard_01", "books", placement="surface",
                  y=.35 * 1.8, support="shelf", surface="shelf_1")
    base = scene([shelf, books])
    result = preview_edit(PreviewEditRequest.model_validate({
        "scene": base, "edit": {"operation": "rotate", "object_id": "shelf", "rotation_y_rad": pi / 2},
    }))
    assert result.accepted
    assert result.scene.objects[1].position.y == books.position.y
    assert result.scene.objects[1].support_surface_id == "shelf_1"
    assert base.objects[0].rotation_y_rad == 0


def test_reconstruction_keeps_portable_decor_in_a_valid_sectional_corner():
    analysis, data = single_object_case()
    raw = analysis.model_dump()
    observed = raw["observations"]["objects"][0]
    raw["observations"]["objects"] = [
        {**observed, "id": "sofa"},
        {**observed, "id": "plant", "type": "plant", "appears_fixed": False},
    ]
    analysis = RoomAnalysisResponse.model_validate(raw)
    source = data["objects"][0]
    data["objects"] = [
        {**source, "id": "sofa", "asset_id": "sofa_sectional_left_01"},
        {**source, "id": "plant", "asset_id": "plant_standard_01",
         "position": {"x": 2.7, "y": 0., "z": 2.45},
         "dimensions_m": {"width": .4, "height": .6, "depth": .4}},
    ]
    result, report, issues = assemble(analysis, measurements(analysis), SceneDraft.model_validate(data))
    assert result is not None and report is not None and report.valid and not issues
    assert result.objects[1].position.model_dump() == data["objects"][1]["position"]


def test_reconstruction_does_not_move_or_shrink_a_supported_overhanging_plant():
    from tests.test_scene_proposals import framed_picture_case

    analysis, data = framed_picture_case()
    host, child = analysis.observations.objects
    host.type, host.estimated_dimensions_m = "desk", None
    child.type = "plant"
    data["objects"][0]["dimensions_m"] = {"width": .30, "depth": .17, "height": .75}
    data["objects"][1]["dimensions_m"] = None
    result, report, issues = assemble(analysis, measurements(analysis), SceneDraft.model_validate(data))
    assert result is not None and report is not None and report.valid and not issues
    assert result.objects[1].dimensions_m.depth == .24
    assert result.objects[1].position.x == data["objects"][1]["position"]["x"]
    assert result.objects[1].position.z == data["objects"][1]["position"]["z"]


@pytest.mark.anyio
async def test_photo_to_scene_api_accepts_a_partly_tucked_chair_without_a_repair(harness, monkeypatch):
    _, client, calls, actions = harness
    analysis, data = single_object_case("table")
    observation = analysis.observations.objects[0].model_dump()
    raw = analysis.observations.model_dump()
    raw["objects"] = [
        {**observation, "id": "table"},
        {**observation, "id": "chair", "type": "chair"},
    ]
    monkeypatch.setattr(test_vision_api, "SOURCE", raw)
    uploaded = await analyzed(harness)
    draft = data["objects"][0]
    data["objects"] = [
        {**draft, "id": "table"},
        {**draft, "id": "chair", "position": {"x": 2., "y": 0., "z": 1.4}},
    ]
    actions.append(scene_action(data))
    response = await client.post("/api/rooms/propose-scene", json=body(uploaded, dimensions=confirmed(length_m=4, height_m=2.6)))
    assert response.status_code == 200 and response.json()["status"] == "ready", response.text
    assert response.json()["validation"]["policy_version"] == "components-v1"
    assert len(calls) == 2  # One mocked photo analysis and one scene request.


@pytest.mark.parametrize("asset", ASSET_CATALOG.values(), ids=lambda a: a.asset_id)
def test_all_100_models_use_only_active_solids_and_enclose_renderer_transform_corners(asset):
    for placement in ASSET_REGISTRY[asset.asset_id].placements:
        obj = model(asset.asset_id, placement=placement, angle=.63,
                    width=1.31, height=.93, depth=.57)
        before = obj.model_dump()
        components = {part.name: part.bounds for part in component_bounds(obj)}
        outer = object_bounds(obj)
        # Whole-object boxes are a broad phase: they must enclose every solid.
        for bounds in components.values():
            assert rectangle_contains(outer.footprint, bounds.footprint)
            assert bounds.vertical[0] >= outer.vertical[0] - TOLERANCE_M
            assert bounds.vertical[1] <= outer.vertical[1] + TOLERANCE_M
        active = [p for p in asset.parts if p.collision and (not p.placements or placement in p.placements)]
        assert set(components) == {p.name for p in active}
        for part in active:
            box = components[part.name]
            for signs in product((-1, 1), repeat=3):
                x, y, z = (sign * size / 2 for sign, size in zip(signs, part.size))
                rx, ry, rz = part.rotation
                # Independent vertex transform: Z, then Y, then X; parent scale/yaw last.
                x, y = cos(rz) * x - sin(rz) * y, sin(rz) * x + cos(rz) * y
                x, z = cos(ry) * x + sin(ry) * z, -sin(ry) * x + cos(ry) * z
                y, z = cos(rx) * y - sin(rx) * z, sin(rx) * y + cos(rx) * z
                x, y, z = ((v + at) * size for v, at, size in zip((x, y, z), part.at, (1.31, .93, .57)))
                px = obj.position.x + cos(.63) * x + sin(.63) * z
                pz = obj.position.z - sin(.63) * x + cos(.63) * z
                for axis, half in zip(box.footprint.axes, box.footprint.half_extents):
                    assert abs((px - box.footprint.center[0]) * axis[0] + (pz - box.footprint.center[1]) * axis[1]) <= half + 1e-8
                assert box.vertical[0] - 1e-8 <= obj.position.y + y <= box.vertical[1] + 1e-8
        if placement == "surface":
            assert contact_bounds(obj) is not None
        assert obj.model_dump() == before
