"""Door access, exact quarter-disk sweeps, uncertainty and warning-only behavior."""

from copy import deepcopy
from math import pi, sqrt

import pytest
from pydantic import ValidationError

from roometry.scenes.door_clearance import (
    DoorClearancePolicy, _sweep_overlaps, check_door_clearance,
)
from roometry.scenes.edit_schemas import PreviewEditRequest
from roometry.scenes.geometry import Rectangle, object_bounds, rectangles_overlap
from roometry.scenes.schemas import Opening
from roometry.scenes.service import preview_edit
from roometry.scenes.validation import validate_geometry
from tests import test_scene_api
from tests.test_geometry import obj, opening, room, scene

anyio_backend = test_scene_api.anyio_backend
client = test_scene_api.client


def door(wall="north", hinge="min_offset", swing="inward", **changes):
    data = dict(id="door_1", type="door", wall=wall, offset_m=1., width_m=1.,
                height_m=2.1, sill_height_m=0., swing=swing, hinge=hinge,
                provenance="user_added", measurements_source="estimated")
    data.update(changes)
    return Opening.model_validate(data)


def world(wall, along, inside):
    # Independent wall-coordinate fixtures, in a deliberately non-square room.
    return {"north": (along, inside), "south": (along, 6.-inside),
            "west": (inside, along), "east": (4.-inside, along)}[wall]


def blocker(wall="north", along=1.5, inside=.35, **changes):
    x, z = world(wall, along, inside)
    return obj("blocker", kind="storage_box", x=x, z=z,
               width=.2, depth=.2, height=.4, **changes)


def case(objects=None, entry=None, dimensions=None):
    return scene(objects, openings=[entry or door()], dimensions=dimensions or room(4., 6.))


@pytest.mark.parametrize("wall", ["north", "south", "west", "east"])
@pytest.mark.parametrize("hinge", ["min_offset", "max_offset"])
def test_all_walls_and_hinges_find_furniture_in_access_and_inward_swing(wall, hinge):
    original = case([blocker(wall)], door(wall, hinge))
    before = deepcopy(original)
    report = validate_geometry(original)
    check = report.door_clearance.doors[0]
    assert report.valid and not report.errors  # Poor existing access stays viewable.
    assert check.access_status == check.swing_status == "blocked"
    assert check.access_object_ids == check.swing_object_ids == ("blocker",)
    assert {i.code for i in report.warnings} >= {"door_access_blocked", "door_swing_blocked"}
    assert original == before


@pytest.mark.parametrize("wall", ["north", "south", "west", "east"])
def test_hinge_end_changes_the_sweep_not_just_its_enclosing_square(wall):
    item = blocker(wall, along=1.12, inside=.9)
    left = check_door_clearance(case([item], door(wall, "min_offset")))
    right = check_door_clearance(case([item], door(wall, "max_offset")))
    assert left.doors[0].swing_status == "blocked"
    assert right.doors[0].swing_status == "clear"
    assert left.doors[0].access_status == right.doors[0].access_status == "clear"


def test_clear_empty_room_and_non_door_openings():
    result = check_door_clearance(case())
    assert result.doors[0].access_status == result.doors[0].swing_status == "clear"
    assert not result.issues
    for candidate in [scene(), scene(openings=[opening()])]:
        assert not check_door_clearance(candidate).doors
        assert not check_door_clearance(candidate).issues


@pytest.mark.parametrize("swing,hinge", [("unknown","unknown"),("unknown","min_offset"),("inward","unknown")])
@pytest.mark.parametrize("blocked", [False,True])
def test_unknown_swing_or_hinge_remains_unassessed_but_access_is_checked(swing, hinge, blocked):
    result = check_door_clearance(case([blocker()] if blocked else [], door(swing=swing, hinge=hinge)))
    assert result.doors[0].access_status == ("blocked" if blocked else "clear")
    assert result.doors[0].swing_status == "unknown"
    assert not result.doors[0].swing_object_ids
    assert {i.code for i in result.issues} == ({"door_access_blocked", "door_swing_unknown"} if blocked else {"door_swing_unknown"})


@pytest.mark.parametrize("swing,hinge,status,code", [
    ("outward","min_offset","outside_room","door_swing_outside_room"),
    ("outward","unknown","outside_room","door_swing_outside_room"),
    ("none",None,"not_applicable",None),
])
def test_outward_and_non_swinging_doors_do_not_invent_an_inward_sweep(swing,hinge,status,code):
    result = check_door_clearance(case([blocker()], door(swing=swing, hinge=hinge)))
    assert result.doors[0].access_status == "blocked"
    assert result.doors[0].swing_status == status
    assert not result.doors[0].swing_object_ids
    assert (code in {i.code for i in result.issues}) if code else len(result.issues) == 1


def test_zone_corner_outside_the_sweep_is_not_a_swing_collision():
    # This box is in the sweep's 1 x 1 envelope but outside its circular arc.
    result = check_door_clearance(case([blocker(along=1.9, inside=.9)]))
    assert result.doors[0].swing_status == result.doors[0].access_status == "clear"


@pytest.mark.parametrize("delta,expected", [(0.,False),(-.0001,True),(.0001,False)])
def test_rotated_component_tangency_uses_the_arc_with_distance_tolerance(delta,expected):
    r = 1.1 + delta
    unit = 1 / sqrt(2)
    rectangle = Rectangle((r*unit,r*unit),((unit,unit),(-unit,unit)),(.1,.1))
    assert _sweep_overlaps(rectangle,(0.,0.),(1.,0.),(0.,1.),1.) is expected


@pytest.mark.parametrize("center", [(-.2,.4),(.4,-.2),(1.1,.4),(.4,1.1)])
def test_sweep_rejects_components_outside_the_quadrant_or_radius(center):
    rectangle = Rectangle(center,((1.,0.),(0.,1.)),(.1,.1))
    assert not _sweep_overlaps(rectangle,(0.,0.),(1.,0.),(0.,1.),1.)


def test_component_crossing_the_hinge_and_quadrant_is_detected():
    rectangle = Rectangle((0.,0.),((1.,0.),(0.,1.)),(.1,.1))
    assert _sweep_overlaps(rectangle,(0.,0.),(1.,0.),(0.,1.),1.)


def test_rugs_and_parts_above_the_door_head_do_not_block_door_clearance():
    rug = obj("rug",kind="rug",x=1.5,z=.5,width=1.,depth=.8,height=.01)
    high = obj("shelf",kind="wall_shelf",placement="wall",wall="west",x=.1,z=.4,
               y=2.2,width=.4,depth=.2,height=.2,rotation=pi/2)
    result = check_door_clearance(case([rug,high]))
    assert result.doors[0].access_status == result.doors[0].swing_status == "clear"


def test_component_gaps_are_empty_instead_of_using_the_whole_furniture_box():
    table = obj("table",kind="table",x=1.5,z=.5,width=2.,depth=1.,height=1.)
    entry = door(offset_m=1.3,width_m=.4,height_m=.6)
    zone = Rectangle((1.5,.4),((1.,0.),(0.,1.)),(.2,.4))
    assert rectangles_overlap(object_bounds(table).footprint,zone)
    result = check_door_clearance(case([table],entry))
    # A synthetic low opening under the top with the legs outside its width.
    assert result.doors[0].access_status == result.doors[0].swing_status == "clear"


def test_access_depth_is_configurable_and_distinct_from_the_door_sweep():
    original = case([blocker(inside=1.1)])
    shallow = check_door_clearance(original)
    deep = check_door_clearance(original,DoorClearancePolicy(access_depth_m=1.3))
    assert shallow.doors[0].access_status == "clear"
    assert deep.doors[0].access_status == "blocked"
    assert deep.doors[0].swing_status == "clear"


def test_floor_gap_is_explicit_and_does_not_hide_normal_low_obstacles():
    item = obj("low_box",kind="storage_box",x=1.5,z=.3,width=.2,depth=.2,height=.015)
    assert check_door_clearance(case([item])).doors[0].access_status == "clear"
    assert check_door_clearance(case([item]),DoorClearancePolicy(floor_gap_m=0.)).doors[0].access_status == "blocked"


def test_a_room_boundary_that_cannot_fit_the_zone_or_sweep_is_not_reported_clear():
    result = check_door_clearance(case(dimensions=room(4.,.7)))
    assert result.doors[0].access_status == result.doors[0].swing_status == "blocked"
    assert all(not c.access_object_ids and not c.swing_object_ids for c in result.doors)
    assert all("room" in issue.message for issue in result.issues)


def test_invalid_opening_is_unassessed_and_remains_a_separate_geometry_error():
    original = case(entry=door(offset_m=3.5))
    report = validate_geometry(original)
    assert not report.valid and report.errors[0].code == "opening_outside_wall"
    assert report.door_clearance.doors[0].access_status == "unassessed"
    assert report.door_clearance.doors[0].swing_status == "unassessed"
    assert report.door_clearance.issues[0].code == "door_clearance_unassessed"


def test_reports_have_deterministic_order_and_one_finding_per_door_rule():
    original = case([blocker(),obj("another",kind="storage_box",x=1.5,z=.35,width=.1,depth=.1,height=.2)])
    a = check_door_clearance(original)
    original.objects.reverse()
    assert check_door_clearance(original) == a
    assert a.doors[0].access_object_ids == ("another","blocker")
    assert len(a.issues) == 2


def test_multiple_doors_report_their_own_statuses_and_blocker_ids():
    original = case([blocker()])
    original.openings.insert(0,door(wall="west",id="door_2",offset_m=3.))
    result = check_door_clearance(original)
    assert [d.door_id for d in result.doors] == ["door_1","door_2"]
    assert result.doors[0].access_object_ids == ("blocker",)
    assert result.doors[1].access_status == result.doors[1].swing_status == "clear"
    assert all(issue.entity_ids[0] == "door_1" for issue in result.issues)


@pytest.mark.parametrize("patch", [{"access_depth_m":0.},{"access_depth_m":float("nan")},
    {"access_depth_m":float("inf")},{"access_depth_m":6.},{"floor_gap_m":-.01},{"floor_gap_m":.2}])
def test_invalid_clearance_policies_are_rejected(patch):
    with pytest.raises(ValidationError):
        DoorClearancePolicy(**patch)


def test_warning_only_base_can_be_edited_to_clear_the_door_without_changing_the_original():
    original = case([blocker()])
    before = deepcopy(original)
    result = preview_edit(PreviewEditRequest.model_validate({"scene":original.model_dump(),
        "edit":{"operation":"move","object_id":"blocker","position":{"x":3.,"z":3.}}}))
    assert result.accepted and result.base_validation.valid and result.candidate_validation.valid
    assert result.base_validation.door_clearance.doors[0].access_status == "blocked"
    assert result.candidate_validation.door_clearance.doors[0].access_status == "clear"
    assert result.candidate_validation.door_clearance.doors[0].swing_status == "clear"
    assert result.scene.openings == original.openings and original == before


@pytest.mark.anyio
async def test_validate_api_exposes_advisory_findings_and_configurable_policy(client):
    original = case([blocker(inside=1.1)],door(swing="unknown",hinge="unknown"))
    response = await client.post("/api/scenes/validate",json={"scene":original.model_dump(mode="json"),"door_policy":{"access_depth_m":1.3}})
    assert response.status_code == 200,response.text
    data = response.json()
    assert data["valid"] and not data["errors"]
    assert data["door_clearance"]["policy_version"] == "door-clearance-v1"
    assert data["door_clearance"]["policy"]["access_depth_m"] == 1.3
    assert data["door_clearance"]["doors"][0]["access_status"] == "blocked"
    assert data["door_clearance"]["doors"][0]["swing_status"] == "unknown"
    assert {i["code"] for i in data["warnings"]} >= {"door_access_blocked","door_swing_unknown"}
    bad = await client.post("/api/scenes/validate",json={"scene":original.model_dump(mode="json"),"door_policy":{"access_depth_m":-1}})
    assert bad.status_code == 422
