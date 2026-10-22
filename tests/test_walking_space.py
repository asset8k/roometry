"""Walking routes at two widths, continuous edges, real door seeds and limits."""

from copy import deepcopy
from math import cos, pi, sin, sqrt

import pytest
from pydantic import ValidationError

from roometry.scenes import navigation, walking_space
from roometry.scenes.edit_schemas import PreviewEditRequest
from roometry.scenes.geometry import Rectangle
from roometry.scenes.navigation import build_navigation_grid, point_rectangle_distance, segment_rectangle_distance
from roometry.scenes.service import preview_edit
from roometry.scenes.validation import validate_geometry
from roometry.scenes.walking_space import WalkingPolicy, check_walking_space
from tests import test_scene_api
from tests.test_door_clearance import door
from tests.test_geometry import obj, opening, room, scene

anyio_backend = test_scene_api.anyio_backend
client = test_scene_api.client


def case(objects=None, entrances=None, width=4., length=6.):
    return scene(objects, openings=entrances if entrances is not None else [door()], dimensions=room(width,length))


def barrier(gap=0., z=2.):
    # The box's full-width lid is a solid part in the standing-height band.
    return obj("barrier",kind="storage_box",x=(4.-gap)/2,z=z,width=4.-gap,depth=.3,height=1.)


@pytest.mark.parametrize("wall", ["north","south","west","east"])
def test_known_door_on_each_wall_connects_to_empty_main_region(wall):
    original = case(entrances=[door(wall=wall,swing="unknown",hinge="unknown")])
    before = deepcopy(original)
    result = check_walking_space(original)
    assert result.status == "clear" and not result.issues
    assert result.entrances[0].status == "clear" and result.entrances[0].main_region_reachable
    assert result.significant_region_count == 1
    assert result.grid_cells == 2400 and result.grid_step_x_m == result.grid_step_z_m == .1
    assert 0 < result.walkable_center_area_m2 < 24
    assert original == before


def test_two_entrances_share_an_internal_route():
    result = check_walking_space(case(entrances=[door(),door(id="door_2",wall="south")]))
    assert result.status == "clear"
    assert result.entrances[0].reachable_door_ids == ("door_2",)
    assert result.entrances[1].reachable_door_ids == ("door_1",)
    assert not any(c.unreachable_door_ids for c in result.entrances)


def test_full_barrier_disconnects_an_entrance_from_the_larger_room_region():
    result = check_walking_space(case([barrier()]))
    assert result.status == "blocked" and result.significant_region_count == 2
    assert result.entrances[0].status == "blocked"
    assert result.entrances[0].main_region_reachable is False
    assert result.disconnected_center_area_m2 > 0
    assert {i.code for i in result.issues} >= {"entrance_route_blocked","walking_space_disconnected"}


def test_disconnected_entrances_are_reported_once_per_pair_with_stable_ids():
    result = check_walking_space(case([barrier()], [door(id="door_2",wall="south"),door()]))
    pairs = [i for i in result.issues if i.code == "entrances_disconnected"]
    assert len(pairs) == 1 and pairs[0].entity_ids == ("door_1","door_2")
    assert result.entrances[0].unreachable_door_ids == ("door_2",)
    assert result.entrances[1].unreachable_door_ids == ("door_1",)


@pytest.mark.parametrize("gap,status", [(.5,"blocked"),(.7,"narrow"),(1.2,"clear")])
def test_passage_width_distinguishes_blocked_narrow_and_clear(gap,status):
    result = check_walking_space(case([barrier(gap)]))
    assert result.status == status,result
    assert result.entrances[0].status == status
    if status == "narrow":
        assert result.entrances[0].main_region_reachable
        assert any(i.code == "narrow_passage" for i in result.issues)


def test_narrow_connection_to_another_major_area_is_found_even_if_entry_is_in_largest_area():
    result = check_walking_space(case([barrier(.7,z=4.)]))
    assert result.entrances[0].main_region_reachable
    assert result.status == result.entrances[0].status == "narrow"
    assert any(i.code == "narrow_passage" and not i.entity_ids for i in result.issues)


def test_configurable_widths_change_the_same_sampled_layout_without_moving_objects():
    original = case([barrier(.7)])
    before = deepcopy(original)
    assert check_walking_space(original).status == "narrow"
    assert check_walking_space(original,WalkingPolicy(minimum_width_m=.75,preferred_width_m=.9)).status == "blocked"
    assert check_walking_space(original,WalkingPolicy(minimum_width_m=.5,preferred_width_m=.6)).status == "clear"
    assert original == before


def test_entry_connector_does_not_teleport_over_a_thin_obstacle_at_the_doorstep():
    strip = obj("strip",kind="storage_box",x=1.5,z=.08,width=1.,depth=.01,height=.5)
    result = check_walking_space(case([strip]))
    assert result.status == result.entrances[0].status == "blocked"


@pytest.mark.parametrize("wall", ["north","south","west","east"])
def test_exact_minimum_door_width_between_grid_columns_is_not_falsely_blocked(wall):
    result = check_walking_space(case(entrances=[door(wall=wall,offset_m=1.013,width_m=.6)]))
    assert result.entrances[0].main_region_reachable
    assert result.entrances[0].status == "narrow"  # The 0.8 m preference cannot fit this doorway.


@pytest.mark.parametrize("changes", [{"width_m":.5},{"height_m":1.7}])
def test_door_must_fit_the_configured_probe(changes):
    result = check_walking_space(case(entrances=[door(**changes)]))
    assert result.entrances[0].status == "blocked"
    assert "modeled doorway" in result.issues[0].message


def test_walking_probe_height_is_configurable():
    original = case(entrances=[door(height_m=1.7)])
    assert check_walking_space(original).status == "blocked"
    assert check_walking_space(original,WalkingPolicy(probe_height_m=1.6)).status == "clear"


def test_rugs_and_objects_above_probe_height_do_not_cut_walking_routes():
    rug = obj("rug",kind="rug",x=2.,z=3.,width=4.,depth=6.,height=.01)
    shelf = obj("shelf",kind="wall_shelf",placement="wall",wall="west",x=.2,z=3.,
                width=6.,depth=.4,height=.2,y=2.,rotation=pi/2)
    assert check_walking_space(case([rug,shelf])).status == "clear"


def test_tabletop_cannot_be_walked_under_just_because_its_legs_leave_a_gap():
    table = obj("table",kind="table",x=2.,z=2.,width=4.,depth=1.,height=.75)
    result = check_walking_space(case([table]))
    assert result.status == "blocked" and result.significant_region_count == 2


def test_cell_edges_cannot_jump_through_a_barrier_thinner_than_the_grid():
    rectangle = Rectangle((.5,.5),((1.,0.),(0.,1.)),(.005,.5))
    grid = build_navigation_grid(1.,1.,.5,(rectangle,),.1)
    assert all(d >= .1 for d in grid.nodes)  # Distances are capped at the largest probe radius.
    labels,sizes = grid.regions(.1)
    assert len(sizes) == 2
    assert labels[0] != labels[1] and labels[2] != labels[3]


def test_diagonal_corner_cutting_does_not_connect_opposite_sides_of_a_rotated_obstacle():
    unit = 1/sqrt(2)
    rectangle = Rectangle((.5,.5),((unit,unit),(-unit,unit)),(1.,.005))
    grid = build_navigation_grid(1.,1.,.5,(rectangle,),.1)
    labels,sizes = grid.regions(.1)
    assert len(sizes) == 2 and labels[1] != labels[2]
    assert labels[0] == labels[3] == -1


@pytest.mark.parametrize("angle", [0.,.4,pi/2])
def test_distance_checks_preserve_rotation_and_continuous_segment_clearance(angle):
    c,s = cos(angle),sin(angle)
    def rotate(x,z):
        return (c*x-s*z,s*x+c*z)
    rectangle = Rectangle((0.,0.),((c,s),(-s,c)),(.1,.3))
    assert point_rectangle_distance(rotate(.5,0.),rectangle) == pytest.approx(.4)
    assert segment_rectangle_distance(rotate(-1.,0.),rotate(1.,0.),rectangle) == 0.
    assert segment_rectangle_distance(rotate(-1.,1.),rotate(1.,1.),rectangle) == pytest.approx(.7)


def test_missing_or_invalid_entrances_are_unassessed_instead_of_successful():
    for original in [case(entrances=[]),case(entrances=[opening()]),case(entrances=[door(offset_m=3.5)])]:
        result = check_walking_space(original)
        assert result.status == "unassessed" and result.grid_cells == 0
        assert result.walkable_center_area_m2 is None
        assert all(e.main_region_reachable is None for e in result.entrances)


def test_invalid_door_does_not_prevent_assessment_of_another_known_entrance():
    result = check_walking_space(case(entrances=[door(),door(id="door_bad",offset_m=3.5)]))
    assert result.status == "unassessed"
    assert result.entrances[0].status == "clear"
    assert result.entrances[1].status == "unassessed"
    assert "door_bad" not in result.entrances[0].unreachable_door_ids


def test_no_clear_floor_is_reported_blocked_without_dividing_by_zero():
    whole_room = obj("obstacle",kind="storage_box",x=2.,z=3.,width=4.,depth=6.,height=1.)
    result = check_walking_space(case([whole_room]))
    assert result.status == "blocked" and result.walkable_center_area_m2 == 0
    assert result.main_region_area_m2 == 0


def test_grid_cell_cap_is_enforced_and_effective_resolution_is_reported():
    result = check_walking_space(case(width=30.,length=30.),WalkingPolicy(cell_size_m=.05))
    assert result.grid_cells <= navigation.MAX_GRID_CELLS
    assert result.grid_step_x_m > .05 and result.grid_step_z_m > .05
    assert result.status == "clear"


@pytest.mark.parametrize("limit", ["raster","entrance"])
def test_work_limits_return_unassessed_without_using_an_incomplete_grid(monkeypatch,limit):
    if limit == "raster":
        monkeypatch.setattr(navigation,"MAX_RASTER_TESTS",0)
    else:
        monkeypatch.setattr(walking_space,"MAX_ENTRANCE_TESTS",0)
    result = check_walking_space(case([obj("box",kind="storage_box",x=3.,z=3.,width=.2,depth=.2,height=.5)]))
    assert result.status == "unassessed"
    assert all(e.status == "unassessed" and e.main_region_reachable is None for e in result.entrances)
    assert any("limit" in i.message for i in result.issues)


@pytest.mark.parametrize("patch", [{"minimum_width_m":float("nan")},{"minimum_width_m":float("inf")},
    {"minimum_width_m":0.},{"preferred_width_m":.5},{"cell_size_m":.001},
    {"probe_height_m":0.},{"floor_gap_m":-.01},{"significant_region_area_m2":0.}])
def test_invalid_policy_thresholds_are_rejected(patch):
    with pytest.raises(ValidationError):
        WalkingPolicy(**patch)


def test_repeated_checks_and_input_reordering_keep_reports_stable():
    original = case([barrier(.7),obj("box",kind="storage_box",x=3.8,z=4.,width=.2,depth=.2,height=.3)])
    first = check_walking_space(original)
    original.objects.reverse()
    assert check_walking_space(original) == first


def test_warning_only_base_remains_viewable_and_a_corrective_edit_recomputes_routes():
    original = case([barrier()])
    before = deepcopy(original)
    report = validate_geometry(original)
    assert report.valid and report.walking_space.status == "blocked"
    assert any(i.code == "entrance_route_blocked" for i in report.warnings)
    # Rotating this room-width box into the room's long axis opens side routes.
    original.objects[0].position.z = 3.
    result = preview_edit(PreviewEditRequest.model_validate({"scene":original.model_dump(),
        "edit":{"operation":"rotate","object_id":"barrier","rotation_y_rad":pi/2}}))
    assert result.accepted and result.base_validation.valid and result.candidate_validation.valid
    assert result.base_validation.walking_space.status == "blocked"
    assert result.candidate_validation.walking_space.status == "clear"
    assert before.objects[0].rotation_y_rad == original.objects[0].rotation_y_rad == 0.


@pytest.mark.anyio
async def test_validation_api_returns_advisory_routes_with_custom_widths(client):
    original = case([barrier(.7)])
    response = await client.post("/api/scenes/validate",json={"scene":original.model_dump(mode="json"),
        "walking_policy":{"minimum_width_m":.75,"preferred_width_m":.9}})
    assert response.status_code == 200,response.text
    data = response.json()
    assert data["valid"] and not data["errors"]
    assert data["walking_space"]["status"] == "blocked"
    assert data["walking_space"]["policy"]["minimum_width_m"] == .75
    assert data["door_clearance"] is not None
    bad = await client.post("/api/scenes/validate",json={"scene":original.model_dump(mode="json"),
        "walking_policy":{"minimum_width_m":1.,"preferred_width_m":.8}})
    assert bad.status_code == 422
