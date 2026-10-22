"""Relative clearance protection in previews and trusted graph state."""

from copy import deepcopy
from math import pi

import pytest

from roometry.agents.nodes import check_step
from roometry.agents.state import RunStep
from roometry.scenes.clearance_acceptance import clearance_regressions
from roometry.scenes.edit_schemas import PreviewEditRequest
from roometry.scenes.service import preview_edit
from roometry.scenes.validation import validate_geometry
from tests.test_door_clearance import case, blocker, door
from tests.test_geometry import obj
from tests.test_walking_space import case as walking_case, barrier


def move(scene, id, x, z):
    return preview_edit(PreviewEditRequest.model_validate({"scene": scene.model_dump(),
        "edit": {"operation": "move", "object_id": id, "position": {"x": x, "z": z}}}))


def test_new_door_blocker_is_rejected_even_with_valid_component_geometry():
    original = case([blocker(along=3., inside=3.)])
    saved = deepcopy(original)
    result = move(original, "blocker", 1.5, .35)
    assert not result.accepted and result.scene == original == saved
    assert not result.changed_object_ids
    assert result.base_validation.valid and result.candidate_validation.valid
    assert all(e.code == "clearance_regression" for e in result.edit_errors)
    assert any("door access" in e.message and "blocker" in e.entity_ids for e in result.edit_errors)


def test_new_swing_blocker_outside_access_zone_is_rejected():
    result = move(case([blocker(along=3., inside=3.)]), "blocker", 1.12, .9)
    assert not result.accepted
    assert result.candidate_validation.door_clearance.doors[0].access_status == "clear"
    assert any("swing" in e.message for e in result.edit_errors)


def test_an_additional_blocker_cannot_hide_behind_existing_blocked_status():
    original = case([blocker(), obj("second",kind="storage_box",x=3.,z=3.,width=.2,depth=.2,height=.4)])
    result = move(original, "second", 1.8, .35)
    assert not result.accepted
    assert result.base_validation.door_clearance.doors[0].access_status == "blocked"
    assert any("second" in e.entity_ids for e in result.edit_errors)


def test_existing_poor_clearance_allows_noop_and_correction_unknown_swing_stays_unknown():
    original = case([blocker()], door(swing="unknown",hinge="unknown"))
    assert move(original,"blocker",1.5,.35).accepted
    result = move(original,"blocker",3.,3.)
    assert result.accepted
    assert result.candidate_validation.door_clearance.doors[0].swing_status == "unknown"
    assert original.objects[0].position.z == .35


@pytest.mark.parametrize("gap,expected", [(.0,"blocked"),(.7,"narrow")])
def test_rotating_a_clear_longitudinal_barrier_to_disconnect_or_narrow_routes_rolls_back(gap,expected):
    furniture = barrier(gap,z=3.)
    furniture.rotation_y_rad = pi/2
    original = walking_case([furniture])
    result = preview_edit(PreviewEditRequest.model_validate({"scene": original.model_dump(),
        "edit":{"operation":"rotate","object_id":"barrier","rotation_y_rad":0.}}))
    assert result.base_validation.walking_space.status == "clear"
    assert result.candidate_validation.walking_space.status == expected
    assert result.candidate_validation.valid and not result.accepted
    assert result.scene == original


def test_lost_entrance_pair_is_explicit_even_when_room_already_has_a_warning():
    original = walking_case([barrier(.7,z=3.)], [door(swing="unknown",hinge="unknown"),door(id="second",wall="south")])
    candidate = deepcopy(original)
    candidate.objects[0].dimensions_m.width = 4.
    candidate.objects[0].position.x = 2.
    errors = clearance_regressions(validate_geometry(original), validate_geometry(candidate))
    assert any("disconnects previously connected" in e.message and "second" in e.entity_ids for e in errors)


def test_known_routes_becoming_unassessed_cannot_bypass_acceptance():
    report = validate_geometry(case())
    unknown = report.walking_space.model_copy(update={"status":"unassessed"})
    assert any("rechecked" in e.message for e in clearance_regressions(report, report.model_copy(update={"walking_space":unknown})))
    assert not clearance_regressions(report,report)


@pytest.mark.anyio
async def test_graph_boundary_rejects_a_direct_adapter_clearance_bypass():
    from tests.test_agent_reliability import populated
    state = await populated()
    original = case([blocker(along=3.,inside=3.)])
    candidate = deepcopy(original)
    candidate.objects[0].position.x, candidate.objects[0].position.z = 1.5,.35
    state = state.model_copy(update={"analysis":None,"working_scene":original})
    with pytest.raises(ValueError,match="worsen clearance"):
        check_step(state,RunStep(action="continue",working_scene=candidate))


def test_clearance_rejection_rolls_back_supporting_furniture_and_every_child(bedroom_scene):
    original = deepcopy(bedroom_scene)
    result = move(original,"desk_1",.85,2.)
    assert not result.accepted and result.candidate_validation.valid
    assert result.scene == bedroom_scene
    assert not result.changed_object_ids
    assert any(e.code == "clearance_regression" for e in result.edit_errors)


def test_tiny_isolated_sampling_patches_do_not_block_an_otherwise_clear_edit():
    report = validate_geometry(case())
    current = report.walking_space.model_copy(update={"disconnected_center_area_m2":.1})
    assert current.significant_region_count == 1
    assert not clearance_regressions(report,report.model_copy(update={"walking_space":current}))
