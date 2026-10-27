"""Catalog selection/defaults at the scene-provider trust boundary; no live calls."""

import json

import pytest
from pydantic import ValidationError

from roometry.config import Settings
from roometry.scenes.assets import ASSET_REGISTRY
from roometry.scenes.catalog import ASSET_CATALOG, candidates, dimensions_for, object_surfaces
from roometry.scenes.schemas import RoomScene
from roometry.vision.attachments import anchor_placements
from roometry.vision.catalog import bind_catalog
from roometry.vision.proposal_request import build_proposal_request
from roometry.vision.proposal_schemas import SceneDraft
from roometry.vision.proposals import assemble, preflight
from roometry.vision.schemas import EstimatedObjectDimensions, RoomAnalysisResponse
from tests import test_vision_api
from tests.test_scene_attachments import shelf_case
from tests.test_scene_proposals import analyzed, body, confirmed, draft_data, measurements, scene_action, source

anyio_backend = test_vision_api.anyio_backend
isolated_environment = test_vision_api.isolated_environment
harness = test_vision_api.harness


def single_object_case(object_type="sofa", placement="floor"):
    analysis = source()
    raw = analysis.model_dump()
    obs = raw["observations"]
    obs["objects"] = [obs["objects"][0]]
    obs["objects"][0].update(type=object_type, placement=placement, wall_id=None,
                             support_object_id=None, estimated_dimensions_m=None)
    obs.update(walls=[], openings=[], possible_duplicates=[])
    analysis = RoomAnalysisResponse.model_validate(raw)
    data = draft_data()
    data.update(wall_assignments=[], openings=[])
    data["objects"] = [data["objects"][0]]
    data["objects"][0].update(asset_id=None, dimensions_m=None, placement=placement,
                              position={"x": 2, "y": 0, "z": 2})
    return analysis, data


@pytest.mark.parametrize("asset", ASSET_CATALOG.values(), ids=lambda asset: asset.asset_id)
def test_all_models_are_bindable_in_each_of_their_approved_configurations(asset):
    for placement in ASSET_REGISTRY[asset.asset_id].placements:
        analysis, data = single_object_case(asset.object_type, placement)
        data["objects"][0]["asset_id"] = asset.asset_id
        draft = SceneDraft.model_validate(data)
        original = draft.model_dump()
        bound, issues = bind_catalog(analysis, draft)
        assert bound is not None and not issues
        obj = bound.objects[0]
        assert obj.asset_id == asset.asset_id
        assert (obj.dimensions_m.width, obj.dimensions_m.height, obj.dimensions_m.depth) == dimensions_for(asset.asset_id, placement)
        assert obj.id == analysis.observations.objects[0].id
        assert draft.model_dump() == original


def test_catalog_options_filter_by_observed_type_and_placement():
    assert set(ASSET_CATALOG) == set(ASSET_REGISTRY)
    assert {a.asset_id for a in candidates("picture", "wall")} == {
        "picture_standard_01", "picture_square_01", "picture_rectangular_01", "picture_triptych_01",
    }
    assert "picture_triptych_01" not in {a.asset_id for a in candidates("picture", "surface")}
    assert not candidates("picture", "floor")


def test_unknown_axes_use_catalog_defaults_without_overwriting_recorded_axes():
    analysis, data = single_object_case()
    analysis.observations.objects[0].estimated_dimensions_m = EstimatedObjectDimensions(width=2.5, depth=None, height=None)
    data["objects"][0]["asset_id"] = "sofa_sectional_left_01"
    original = analysis.model_dump()
    scene, report, issues = assemble(analysis, measurements(analysis), SceneDraft.model_validate(data))
    assert scene is not None and report is not None and report.valid and not issues
    obj = scene.objects[0]
    assert obj.dimensions_m.model_dump() == {"width": 2.5, "depth": 1.65, "height": .88}
    assert obj.measurements_source == "estimated"
    assert scene.room.width_m == scene.room.length_m == 4
    assert scene.room.height_m == 2.6 and scene.room.measurements_source == "user_confirmed"
    assert any("catalog defaults" in note for note in scene.assumptions)
    assert analysis.model_dump() == original


def test_explicit_geometry_estimates_are_kept_but_cannot_change_recorded_axes():
    analysis, data = single_object_case()
    analysis.observations.objects[0].estimated_dimensions_m = EstimatedObjectDimensions(width=2.5, depth=None, height=None)
    obj = data["objects"][0]
    obj.update(asset_id="sofa_sectional_left_01", dimensions_m={"width": 2.5, "height": .9, "depth": 1.5})
    scene, _, issues = assemble(analysis, measurements(analysis), SceneDraft.model_validate(data))
    assert scene is not None and not issues
    assert scene.objects[0].dimensions_m.depth == 1.5
    obj["dimensions_m"]["width"] = 2.0
    scene, report, issues = assemble(analysis, measurements(analysis), SceneDraft.model_validate(data))
    assert scene is report is None
    assert issues[0].code == "dimension_mismatch"


@pytest.mark.parametrize("asset_id,placement,code", [
    ("invented_sofa", "floor", "asset_mismatch"),
    ("bed_standard_01", "floor", "asset_mismatch"),
    ("sofa_sectional_left_01", "wall", "unsupported_placement"),
])
def test_provider_cannot_invent_models_change_categories_or_choose_invalid_placements(asset_id, placement, code):
    analysis, data = single_object_case()
    data["objects"][0].update(asset_id=asset_id, placement=placement)
    scene, report, issues = assemble(analysis, measurements(analysis), SceneDraft.model_validate(data))
    assert scene is report is None and issues[0].code == code


def test_preflight_considers_variants_with_different_placement_permissions():
    analysis, data = single_object_case("household_accessory", "floor")
    assert not preflight(analysis)
    scene, report, issues = assemble(analysis, measurements(analysis), SceneDraft.model_validate(data))
    assert scene is not None and report is not None and report.valid and not issues
    assert scene.objects[0].asset_id == "household_accessory_toilet_brush_01"
    assert scene.objects[0].measurements_source == "catalog"


def test_selected_support_surface_is_preserved_and_default_top_uses_actual_area():
    analysis, data = shelf_case()
    draft = SceneDraft.model_validate(data)
    bound, issues = bind_catalog(analysis, draft)
    assert bound is not None and not issues
    assert bound.objects[1].support_surface_id == "shelf_1"
    objects = anchor_placements(analysis, measurements(analysis).dimensions, bound)
    assert objects[1].position.y == pytest.approx(1.1)
    payload = build_proposal_request(Settings(_env_file=None), analysis, measurements(analysis), draft, [])
    context = json.loads(payload["input"][0]["content"][0]["text"])
    constraint = context["attachment_constraints"][0]
    assert constraint["support_surface_id"] == "shelf_1"
    assert constraint["available_surface_m"]["width"] < objects[0].dimensions_m.width
    assert context["previous_draft"]["objects"][0]["asset_id"] == "wall_shelf_standard_01"


def test_internal_shelf_rejects_an_item_taller_than_the_available_gap():
    analysis, data = shelf_case()
    data["objects"][1]["support_surface_id"] = "shelf_0"
    draft = SceneDraft.model_validate(data)
    bound, issues = bind_catalog(analysis, draft)
    assert bound is not None and not issues
    objects = anchor_placements(analysis, measurements(analysis).dimensions, bound)
    assert objects[1].support_surface_id == "shelf_0"
    assert objects[1].position.y == pytest.approx(1 + .04 * .1)
    scene, report, issues = assemble(analysis, measurements(analysis), draft)
    assert scene is None and report is not None and not report.valid and issues
    assert "object_overlap" in {error.code for error in report.errors}
    assert "surface_support" not in {error.code for error in report.errors}


def test_sink_ledges_exclude_the_basin_and_hob_cannot_supply_a_surface():
    areas = object_surfaces("kitchen_counter_sink_01", "floor")
    assert len(areas) == 4
    assert not any(a.x_min <= 0 <= a.x_max and a.z_min <= 0 <= a.z_max for a in areas)
    assert not object_surfaces("kitchen_counter_hob_01", "floor")
    analysis, data = shelf_case()
    data["objects"][1]["support_surface_id"] = "invented_tier"
    bound, issues = bind_catalog(analysis, SceneDraft.model_validate(data))
    assert bound is None and issues[0].code == "support_mismatch"


def test_scene_contract_retains_surface_selection_and_validates_it_against_the_host():
    analysis, data = shelf_case()
    scene, _, issues = assemble(analysis, measurements(analysis), SceneDraft.model_validate(data))
    assert scene is not None and not issues
    raw = scene.model_dump()
    assert raw["objects"][1]["support_surface_id"] == "shelf_1"
    raw["objects"][1]["support_surface_id"] = "invented_tier"
    with pytest.raises(ValidationError, match="unknown support surface"):
        RoomScene.model_validate(raw)
    raw["objects"][1]["support_surface_id"] = None
    assert RoomScene.model_validate(raw)  # Existing scenes remain readable.


def test_provider_context_has_only_relevant_catalog_choices_and_strict_nullable_fields():
    analysis, _ = single_object_case("plant", "surface")
    payload = build_proposal_request(Settings(_env_file=None), analysis, measurements(analysis), None, [])
    context = json.loads(payload["input"][0]["content"][0]["text"])
    options = context["asset_bindings"][0]["models"]
    assert {model["asset_id"] for model in options} == {
        "plant_standard_01", "plant_palm_01", "plant_monstera_01", "plant_trailing_surface_01",
    }
    assert all(model['placements'] == ['surface'] for model in options)
    options = [context['asset_models'][model['asset_id']] for model in options]
    assert all('surface' in model['configurations'] for model in options)
    assert options[0]["configurations"]["surface"]["default_dimensions_m"]["height"] == .36
    assert "parts" not in json.dumps(context)
    schema = payload["text"]["format"]["schema"]
    for model in [schema, *schema["$defs"].values()]:
        if model.get("type") == "object":
            assert set(model["required"]) == set(model["properties"])
            assert model["additionalProperties"] is False


@pytest.mark.anyio
async def test_api_uses_the_selected_catalog_variant_without_an_extra_provider_call(harness, monkeypatch):
    _, client, calls, actions = harness
    analysis, data = single_object_case()
    analysis.observations.objects[0].color = None
    monkeypatch.setattr(test_vision_api, "SOURCE", analysis.observations.model_dump())
    uploaded = await analyzed(harness)
    data["objects"][0]["asset_id"] = "sofa_sectional_left_01"
    actions.append(scene_action(data))
    response = await client.post("/api/rooms/propose-scene", json=body(uploaded, dimensions=confirmed(length_m=4, height_m=2.6)))
    assert response.status_code == 200 and response.json()["status"] == "ready", response.text
    scene = RoomScene.model_validate(response.json()["scene"])
    assert scene.objects[0].asset_id == "sofa_sectional_left_01"
    assert scene.objects[0].measurements_source == "catalog"
    assert scene.objects[0].dimensions_m.width == 2.8
    assert scene.objects[0].color == ASSET_CATALOG["sofa_sectional_left_01"].color
    assert len(calls) == 2  # One photo analysis and one scene request.
    assert scene.room.measurements_source == "user_confirmed"
