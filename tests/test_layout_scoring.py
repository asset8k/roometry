"""Soft scores explain tradeoffs without overriding geometry or inventing taste."""

from copy import deepcopy
import json
from math import pi

import pytest
from pydantic import ValidationError

from roometry.scenes import scoring
from roometry.scenes.scoring import CriterionScore, LayoutScoreReport, LayoutScoringPolicy, score_layout
from tests import test_scene_api
from tests.test_component_geometry import model
from tests.test_geometry import obj, room, scene

anyio_backend = test_scene_api.anyio_backend
client = test_scene_api.client


def case(objects=None):
    return scene(objects, dimensions=room(6.,6.))


def desk(**changes):
    data = dict(object_id="desk",kind="desk",x=2.,z=2.,width=1.3,depth=.65,height=.75)
    data.update(changes)
    return obj(**data)


def metric(criterion,key):
    return next(m.value for m in criterion.metrics if m.key == key)


def test_empty_scene_has_independent_metrics_and_no_invented_overall_or_aesthetic_rating():
    report = score_layout(case())
    assert report.geometry.valid and report.geometry.walking_space.status == "unassessed"
    assert report.openness.score == 100 and report.storage.score == report.workspace.score == 0
    assert report.aesthetics.status == "unassessed" and report.aesthetics.score is None
    assert report.overall_score is None
    assert metric(report.openness,"floor_area") == metric(report.openness,"unoccupied_floor_area") == 36


def test_selected_goals_use_explicit_equal_weights_without_averaging_unselected_unknowns():
    original = case([desk(),obj("box",kind="storage_box",x=4.,z=4.,width=1.,depth=1.,height=1.)])
    report = score_layout(original,["more_storage","workspace"])
    assert report.selected_criteria == ("storage","workspace")
    assert report.storage.score == 50 and report.workspace.score == 100
    assert report.overall_score == 75
    assert score_layout(original,["more_open_space"]).overall_score == report.openness.score
    assert score_layout(original,["better_aesthetics"]).overall_score is None
    assert score_layout(original,["general_analysis","workspace"]).selected_criteria == ("openness","storage","workspace","aesthetics")
    assert score_layout(original,[]).requested_goals == ("general_analysis",)


def test_storage_volume_formula_is_monotonic_and_uses_an_echoed_reference_scale():
    box = obj("box",kind="storage_box",x=4.,z=4.,width=1.,depth=1.,height=1.)
    a = score_layout(case([box]),["more_storage"])
    b = score_layout(case([box]),["more_storage"],LayoutScoringPolicy(storage_reference_m3=2.))
    assert metric(a.storage,"nominal_storage_volume") == 1.
    assert a.storage.score == a.overall_score == 50
    assert b.storage.score == pytest.approx(100/3)
    larger = deepcopy(box)
    larger.dimensions_m.height = 2.
    assert score_layout(case([larger])).storage.score > a.storage.score
    assert a.storage.entity_ids == ("box",)
    assert "nominal outer volumes" in a.storage.explanation


def test_surface_storage_containers_are_not_counted_again_inside_their_storage_host():
    shelf = model("shelf_standard_01","shelf",x=2.,z=2.)
    box = model("storage_box_standard_01","box",placement="surface",x=2.,z=2.,y=shelf.dimensions_m.height,
                width=.2,depth=.2,height=.2,support="shelf")
    baseline = score_layout(case([shelf]))
    report = score_layout(case([shelf,box]))
    assert report.geometry.valid,report.geometry.errors
    assert report.storage.score == baseline.storage.score
    assert report.storage.entity_ids == ("shelf",)


def test_appliances_and_non_storage_furniture_do_not_inflate_the_storage_proxy():
    report = score_layout(case([desk(),obj("sofa",kind="sofa",x=4.,z=4.,width=1.5,depth=.8,height=.8)]))
    assert report.geometry.valid
    assert report.storage.score == 0 and not report.storage.entity_ids


def test_stacked_solid_projections_are_unioned_instead_of_double_counting_floor_area():
    host = desk()
    vase = obj("vase",kind="vase",placement="surface",support_id="desk",x=2.,z=2.,y=.75,
               width=.2,depth=.2,height=.2)
    baseline,stacked = score_layout(case([host])),score_layout(case([host,vase]))
    assert stacked.geometry.valid,stacked.geometry.errors
    assert stacked.openness.score == baseline.openness.score
    assert metric(stacked.openness,"unoccupied_floor_area") == metric(baseline.openness,"unoccupied_floor_area")


def test_sectional_open_corner_is_not_treated_as_solid_floor_occupancy():
    sofa = model("sofa_sectional_left_01","sofa",x=3.,z=3.)
    report = score_layout(case([sofa]))
    assert report.geometry.valid
    outer_area = sofa.dimensions_m.width * sofa.dimensions_m.depth
    assert metric(report.openness,"unoccupied_floor_area") > 36 - outer_area + .4


def test_rugs_and_parts_above_the_standing_band_do_not_reduce_openness():
    rug = obj("rug",kind="rug",x=3.,z=3.,width=6.,depth=6.,height=.01)
    shelf = obj("high_shelf",kind="wall_shelf",placement="wall",wall="west",x=.1,z=3.,y=2.,
                width=1.,depth=.2,height=.2,rotation=pi/2)
    report = score_layout(case([rug,shelf]))
    assert report.geometry.valid,report.geometry.errors
    assert report.openness.score == 100


def test_workstation_reports_real_catalog_top_dimensions_and_available_chair_space():
    report = score_layout(case([desk()]),["workspace"])
    station = report.workstations[0]
    assert station.worktop_width_m == pytest.approx(1.3 * .964)
    assert station.worktop_depth_m == pytest.approx(.65 * .964)
    assert station.worktop_height_m == .75
    assert station.approach_clear and station.approach_inside_room
    assert report.workspace.score == report.overall_score == 100


def test_a_smaller_worktop_is_scored_against_declared_minimum_dimensions():
    report = score_layout(case([desk(width=.8,depth=.4)]),["workspace"])
    station = report.workstations[0]
    assert report.workspace.score == pytest.approx(100 * (.8 * .964) * (.4 * .964 / .5))
    assert station.approach_clear


@pytest.mark.parametrize("height", [.4,1.3])
def test_unusable_worktop_height_is_not_rewarded_even_with_a_large_top(height):
    report = score_layout(case([desk(height=height)]),["workspace"])
    assert report.geometry.valid and report.workspace.score == 0
    assert report.workstations[0].approach_clear


def test_worktop_height_range_is_an_explicit_policy_setting():
    report = score_layout(case([desk(height=1.1)]),["workspace"],LayoutScoringPolicy(maximum_worktop_height_m=1.2))
    assert report.workspace.score == 100 and report.policy.maximum_worktop_height_m == 1.2


def test_desk_facing_a_wall_has_no_room_for_its_working_side():
    report = score_layout(case([desk(z=5.5)]),["workspace"])
    assert report.geometry.valid and report.workspace.score == 0
    assert not report.workstations[0].approach_inside_room


def test_obstructed_working_side_has_zero_workspace_score_and_names_the_blocker():
    box = obj("blocker",kind="storage_box",x=2.,z=3.,width=.2,depth=.2,height=.5)
    report = score_layout(case([desk(),box]),["workspace"])
    assert report.geometry.valid and report.workspace.score == 0
    assert report.workstations[0].blocking_object_ids == ("blocker",)


def test_desk_rotation_moves_its_working_side_consistently():
    box = obj("blocker",kind="storage_box",x=3.,z=2.,width=.2,depth=.2,height=.5)
    assert score_layout(case([desk(),box])).workspace.score == 100
    turned = score_layout(case([desk(rotation=pi/2),box]))
    assert turned.geometry.valid and turned.workspace.score == 0


@pytest.mark.parametrize("z", [2.45,2.725])
def test_one_fitting_chair_facing_the_desk_is_allowed_including_partial_tucking(z):
    chair = obj("chair",kind="chair",x=2.,z=z,width=.5,depth=.5,height=.9,rotation=pi)
    report = score_layout(case([desk(),chair]),["workspace"])
    assert report.geometry.valid,report.geometry.errors
    assert report.workspace.score == 100
    assert report.workstations[0].permitted_chair_id == "chair"
    assert not report.workstations[0].blocking_object_ids


def test_a_chair_facing_away_is_not_silently_exempted_as_a_working_seat():
    chair = obj("chair",kind="chair",x=2.,z=2.725,width=.5,depth=.5,height=.9)
    report = score_layout(case([desk(),chair]),["workspace"])
    assert report.geometry.valid and report.workspace.score == 0
    assert report.workstations[0].blocking_object_ids == ("chair",)


def test_only_one_chair_is_exempted_from_the_approach_zone():
    chairs = [obj(name,kind="chair",x=2.,z=z,width=.3,depth=.3,height=.8,rotation=pi)
              for name,z in [("chair_a",2.55),("chair_b",3.)]]
    report = score_layout(case([desk(),*chairs]))
    assert report.geometry.valid,report.geometry.errors
    assert report.workspace.score == 0
    assert report.workstations[0].permitted_chair_id == "chair_a"
    assert report.workstations[0].blocking_object_ids == ("chair_b",)


def test_non_desk_surfaces_are_not_invented_workstations_and_best_desk_is_used():
    table = obj("table",kind="table",x=4.,z=4.,width=1.,depth=1.,height=.75)
    assert score_layout(case([table])).workspace.score == 0
    report = score_layout(case([desk(),desk(object_id="small",x=4.,z=4.,width=.6,depth=.4)]))
    assert report.workspace.score == 100 and len(report.workstations) == 2


def test_failing_component_geometry_cannot_receive_an_optimization_score():
    original = case([desk(x=-1.)])
    report = score_layout(original,["more_open_space"])
    assert not report.geometry.valid and report.overall_score is None
    assert all(item.status == "unassessed" and item.score is None
               for item in [report.openness,report.storage,report.workspace,report.aesthetics])
    assert not report.workstations


def test_area_work_limit_does_not_turn_a_partial_projection_into_a_high_score(monkeypatch):
    monkeypatch.setattr(scoring,"MAX_PROJECTION_TESTS",0)
    report = score_layout(case([desk()]),["more_open_space"])
    assert report.openness.status == "unassessed" and report.overall_score is None
    assert report.workspace.score == 100
    assert "work limit" in report.openness.explanation


def test_projection_grid_is_bounded_and_reports_the_effective_resolution():
    report = score_layout(scene(dimensions=room(30.,30.)),["more_open_space"],LayoutScoringPolicy(cell_size_m=.05))
    assert metric(report.openness,"grid_cells") <= 40_000
    assert metric(report.openness,"grid_step_x") > .05
    assert report.openness.score == 100


def test_scores_are_pure_and_stable_under_scene_order_and_color_changes():
    original = case([desk(),obj("box",kind="storage_box",x=4.,z=4.,width=1.,depth=1.,height=1.)])
    before = deepcopy(original)
    first = score_layout(original,["more_open_space","more_storage","workspace"])
    assert original == before
    original.objects.reverse()
    original.objects[0].color = "#123456"
    assert score_layout(original,["more_open_space","more_storage","workspace"]) == first


@pytest.mark.parametrize("goals", [["workspace","workspace"],["made_up_goal"]])
def test_invalid_or_duplicate_goals_are_rejected(goals):
    with pytest.raises(ValidationError):
        score_layout(case(),goals)


@pytest.mark.parametrize("patch", [{"cell_size_m":.001},{"storage_reference_m3":0.},
    {"minimum_worktop_width_m":float("nan")},{"chair_zone_depth_m":float("inf")},
    {"maximum_worktop_height_m":.4},{"chair_tuck_allowance_m":-.1}])
def test_invalid_scoring_policies_are_rejected(patch):
    with pytest.raises(ValidationError):
        LayoutScoringPolicy(**patch)


def test_unassessed_criteria_cannot_claim_numeric_success():
    with pytest.raises(ValidationError):
        CriterionScore(status="unassessed",score=100.,explanation="No preference known.")


def test_score_report_round_trip_validates_aggregates_and_computed_geometry():
    report = score_layout(case([desk()]))
    assert LayoutScoreReport.model_validate_json(report.model_dump_json()) == report
    for patch in [{"overall_score":100.}, {"selected_criteria":["workspace"]},
                  {"geometry":{**report.geometry.model_dump(mode="json"),"valid":False}}]:
        with pytest.raises(ValidationError):
            LayoutScoreReport.model_validate_json(json.dumps({**report.model_dump(mode="json"),**patch}))


@pytest.mark.anyio
async def test_stateless_scoring_api_exposes_goals_policy_reasons_and_separate_validation(client):
    original = case([desk()])
    response = await client.post("/api/scenes/score",json={"scene":original.model_dump(mode="json"),"goals":["workspace"]})
    assert response.status_code == 200,response.text
    data = response.json()
    assert data["policy_version"] == "layout-score-v1" and data["geometry"]["valid"]
    assert data["overall_score"] == data["workspace"]["score"] == 100
    assert data["aesthetics"]["score"] is None
    bad = await client.post("/api/scenes/score",json={"scene":original.model_dump(mode="json"),"goals":["override_collisions"]})
    assert bad.status_code == 422
    invalid = case([desk(x=-1.)])
    result = (await client.post("/api/scenes/score",json={"scene":invalid.model_dump(mode="json")})).json()
    assert not result["geometry"]["valid"] and result["overall_score"] is None
