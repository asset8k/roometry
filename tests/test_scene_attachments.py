"""Reconstruction regressions: attached items follow their host, with strict checks."""

import json
from copy import deepcopy
from math import cos, pi, sin

import pytest

from roometry.config import Settings
from roometry.scenes.geometry import WALL_ROTATIONS_RAD
from roometry.vision.attachments import anchor_placements
from roometry.vision.catalog import bind_catalog
from roometry.vision.proposal_request import build_proposal_request
from roometry.vision.proposal_schemas import SceneDraft
from roometry.vision.proposals import assemble, issue
from roometry.vision.schemas import RoomAnalysisResponse
from tests.test_scene_proposals import (
    curtained_door_case,
    framed_picture_case,
    measurements,
)


def shelf_case(wall="north"):
    analysis, data = framed_picture_case()
    host = analysis.observations.objects[0]
    host.type, host.placement, host.wall_id = "wall_shelf", "wall", "window_wall"
    host.estimated_dimensions_m = None
    analysis.observations.openings = []
    analysis.observations.walls = [analysis.observations.walls[0]]
    data["openings"] = []
    data["wall_assignments"] = [{"observation_id": "window_wall", "wall": wall}]
    angle = WALL_ROTATIONS_RAD[wall] + 0.12
    x, z = {"north": (2, 0.3), "south": (2, 3.7), "west": (0.3, 2), "east": (3.7, 2)}[
        wall
    ]
    support, child = data["objects"]
    support.update(
        placement="wall",
        wall=wall,
        rotation_y_rad=angle,
        position={"x": x, "y": 1, "z": z},
        dimensions_m={"width": 1.2, "depth": 0.4, "height": 0.1},
    )
    child.update(
        rotation_y_rad=angle,
        position={"x": x + 0.2 * cos(angle), "y": 1.12, "z": z - 0.2 * sin(angle)},
        dimensions_m={"width": 0.15, "depth": 0.1, "height": 0.2},
    )
    return analysis, data


@pytest.mark.parametrize("wall", ["north", "south", "west", "east"])
def test_surface_child_follows_host_translation_and_rotation_on_each_wall(wall):
    analysis, data = shelf_case(wall)
    draft = SceneDraft.model_validate(data)
    before = draft.model_dump()
    scene, report, issues = assemble(analysis, measurements(analysis), draft)
    assert scene is not None and report is not None and report.valid and not issues
    host, child = scene.objects
    angle = WALL_ROTATIONS_RAD[wall]
    assert child.position.x == pytest.approx(host.position.x + 0.2 * cos(angle))
    assert child.position.z == pytest.approx(host.position.z - 0.2 * sin(angle))
    assert child.position.y == pytest.approx(1.1)
    assert child.rotation_y_rad == pytest.approx(angle)
    assert draft.model_dump() == before
    assert child.dimensions_m.model_dump() == before["objects"][1]["dimensions_m"]


def test_multiple_small_items_are_separated_on_one_surface_without_dropping_them():
    analysis, data = shelf_case()
    template = analysis.observations.objects[1].model_dump()
    raw = analysis.model_dump()
    raw["observations"]["objects"] = [raw["observations"]["objects"][0]] + [
        {**template, "id": f"item_{index}"} for index in range(5)
    ]
    analysis = RoomAnalysisResponse.model_validate(raw)
    item = data["objects"][1]
    item.update(
        rotation_y_rad=0.12,
        position={"x": 2, "y": 1.1, "z": 0.3},
        dimensions_m={"width": 0.1, "depth": 0.08, "height": 0.2},
    )
    data["objects"] = [data["objects"][0]] + [
        {**deepcopy(item), "id": f"item_{index}"} for index in range(5)
    ]
    scene, report, issues = assemble(
        analysis, measurements(analysis), SceneDraft.model_validate(data)
    )
    assert scene is not None and report is not None and report.valid and not issues
    assert len(scene.objects) == 6
    assert (
        len(
            {
                (round(obj.position.x, 4), round(obj.position.z, 4))
                for obj in scene.objects[1:]
            }
        )
        == 5
    )


def test_console_item_can_fit_without_intersecting_nearby_floor_plant():
    analysis, data = framed_picture_case()
    raw = analysis.model_dump()
    raw["observations"]["objects"].append(
        {
            **raw["observations"]["objects"][0],
            "id": "floor_plant",
            "type": "plant",
            "estimated_dimensions_m": None,
        }
    )
    analysis = RoomAnalysisResponse.model_validate(raw)
    data["objects"][1]["position"]["x"] = 3.56
    data["objects"].append(
        {
            **deepcopy(data["objects"][0]),
            "id": "floor_plant",
            "position": {"x": 3.8, "y": 0, "z": 0.6},
            "dimensions_m": {"width": 0.3, "depth": 0.3, "height": 1.2},
        }
    )
    scene, report, issues = assemble(
        analysis, measurements(analysis), SceneDraft.model_validate(data)
    )
    assert scene is not None and report is not None and report.valid and not issues
    assert scene.objects[-1].position.x == 3.8


def test_wide_surface_display_is_aligned_to_fit_its_rotated_console():
    analysis, data = framed_picture_case()
    data["objects"][0]["rotation_y_rad"] = pi / 2
    data["objects"][1]["dimensions_m"].update(width=0.8, depth=0.1)
    scene, report, issues = assemble(
        analysis, measurements(analysis), SceneDraft.model_validate(data)
    )
    assert scene is not None and report is not None and report.valid and not issues
    assert scene.objects[1].rotation_y_rad == pytest.approx(pi / 2)
    assert scene.objects[1].dimensions_m.width == 0.8


@pytest.mark.parametrize("defect", ["oversized_item", "far_from_support"])
def test_unresolvable_supports_are_rejected_without_resizing_or_large_moves(defect):
    analysis, data = shelf_case()
    if defect == "oversized_item":
        data["objects"][1]["dimensions_m"]["width"] = 2
    else:
        data["objects"][1]["position"]["x"] = 10
    draft = SceneDraft.model_validate(data)
    before = draft.model_dump()
    scene, report, issues = assemble(analysis, measurements(analysis), draft)
    assert scene is None and report is not None and not report.valid and issues
    assert "surface_support" in {error.code for error in report.errors}
    assert draft.model_dump() == before


@pytest.mark.parametrize("oversized", [False, True])
def test_curtain_pose_can_fit_room_edges_but_size_is_never_shrunk(oversized):
    analysis, data = curtained_door_case()
    obj = data["objects"][0]
    if oversized:
        obj["dimensions_m"]["height"] = 3
    else:
        obj["position"].update(y=0.6, z=3.65)
    draft = SceneDraft.model_validate(data)
    scene, report, issues = assemble(analysis, measurements(analysis), draft)
    if oversized:
        assert scene is None and report is not None and not report.valid and issues
    else:
        assert scene is not None and report is not None and report.valid and not issues
        assert scene.objects[0].dimensions_m.model_dump() == obj["dimensions_m"]


def test_repair_receives_the_same_host_and_child_poses_that_assembly_checks():
    analysis, data = shelf_case()
    draft = SceneDraft.model_validate(data)
    resolved = measurements(analysis)
    payload = build_proposal_request(
        Settings(_env_file=None),
        analysis,
        resolved,
        draft,
        [issue("geometry", "An estimate needs review.")],
    )
    context = json.loads(payload["input"][0]["content"][0]["text"])
    host, child = context["previous_draft"]["objects"]
    assert host["position"]["z"] == pytest.approx(0.2)
    assert child["position"]["z"] == pytest.approx(0.2)
    assert child["position"]["x"] == pytest.approx(2.2)
    assert context["attachment_constraints"][0]["support_top_y_m"] == pytest.approx(1.1)
    normalized = SceneDraft.model_validate(context["previous_draft"])
    bound, issues = bind_catalog(analysis, normalized)
    assert bound is not None and not issues
    repeated = anchor_placements(analysis, resolved.dimensions, bound)
    for before, after in zip(normalized.objects, repeated):
        assert before.position.x == pytest.approx(after.position.x)
        assert before.position.z == pytest.approx(after.position.z)


@pytest.mark.parametrize("fixed", [False, True])
def test_small_furniture_boundary_correction_moves_children_and_preserves_fixtures(
    fixed,
):
    analysis, data = framed_picture_case()
    analysis.observations.objects[0].appears_fixed = fixed
    data["objects"][0]["position"]["x"] = 0.55
    data["objects"][1]["position"]["x"] = 0.55
    scene, report, issues = assemble(
        analysis, measurements(analysis), SceneDraft.model_validate(data)
    )
    if fixed:
        assert scene is None and report is not None and not report.valid and issues
    else:
        assert scene is not None and report is not None and report.valid and not issues
        assert scene.objects[0].position.x == pytest.approx(0.6)
        assert scene.objects[1].position.x == pytest.approx(0.6)


@pytest.mark.parametrize("fixed", [False, True])
def test_floor_decor_can_clear_furniture_without_moving_the_furniture(fixed):
    analysis, data = framed_picture_case()
    raw = analysis.model_dump()
    example = raw["observations"]["objects"][0]
    raw["observations"]["objects"] = [
        {**example, "id": "sofa", "type": "sofa", "estimated_dimensions_m": None},
        {
            **example,
            "id": "plant",
            "type": "plant",
            "estimated_dimensions_m": None,
            "appears_fixed": fixed,
        },
    ]
    analysis = RoomAnalysisResponse.model_validate(raw)
    base = data["objects"][0]
    data["objects"] = [
        {
            **deepcopy(base),
            "id": "sofa",
            "position": {"x": 3.45, "y": 0, "z": 1.25},
            "dimensions_m": {"width": 2.6, "depth": 1, "height": 0.85},
        },
        {
            **deepcopy(base),
            "id": "plant",
            "position": {"x": 4.35, "y": 0, "z": 1.35},
            "dimensions_m": {"width": 0.4, "depth": 0.4, "height": 1},
        },
    ]
    from roometry.vision.proposals import resolve_measurements

    dimensions = resolve_measurements(
        analysis, {"width_m": 5, "length_m": 7, "height_m": 3}, False
    )
    scene, report, issues = assemble(
        analysis, dimensions, SceneDraft.model_validate(data)
    )
    if fixed:
        assert scene is None and report is not None and not report.valid and issues
    else:
        assert scene is not None and report is not None and report.valid and not issues
        assert scene.objects[0].position.model_dump() == data["objects"][0]["position"]
        assert scene.objects[1].position.z == pytest.approx(1.95)
        assert (
            scene.objects[1].dimensions_m.model_dump()
            == data["objects"][1]["dimensions_m"]
        )
