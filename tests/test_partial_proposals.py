"""Partial reconstruction preserves evidence, dependencies and paid-call limits."""

from copy import deepcopy

import anyio
import httpx
import pytest
from pydantic import ValidationError

from roometry.scenes.schemas import RoomScene
from roometry.scenes.validation import validate_geometry
from roometry.vision.partial import assemble_partial
from roometry.vision import partial as partial_module
from roometry.vision import service as service_module
from roometry.vision.errors import AnalysisFailure
from roometry.vision.proposal_schemas import SceneDraft, SceneProposalResponse
from tests import test_vision_api
from tests.test_catalog_assembly import single_object_case
from tests.test_scene_proposals import (
    analyzed, body, confirmed, curtained_door_case, draft_data, measurements,
    output_limited_scene, scene_action, source,
)

anyio_backend = test_vision_api.anyio_backend
isolated_environment = test_vision_api.isolated_environment
harness = test_vision_api.harness


def select(analysis, data):
    draft = SceneDraft.model_validate(data)
    original_analysis, original_draft = analysis.model_dump(), draft.model_dump()
    scene, report, issues, unresolved = assemble_partial(analysis, measurements(analysis), draft)
    assert analysis.model_dump() == original_analysis
    assert draft.model_dump() == original_draft
    if scene is not None:
        assert report is not None and report.valid and validate_geometry(scene).valid
        retained = {item.id for item in [*scene.objects, *scene.openings]}
        omitted = {item.entity_id for item in unresolved}
        assert not retained & omitted
        assert retained | omitted == {item.id for item in [*analysis.observations.objects, *analysis.observations.openings]}
        assert scene.room.width_m == scene.room.length_m == 4
        assert scene.room.height_m == 2.6 and scene.room.measurements_source == "user_confirmed"
    return scene, report, issues, {item.entity_id: item for item in unresolved}


@pytest.mark.parametrize("defect", ["outside", "asset", "dimension", "unknown", "placement", "missing"])
def test_one_unresolved_entity_does_not_discard_the_rest(defect):
    analysis, data = source(), draft_data()
    bed = data["objects"][0]
    if defect == "outside":
        bed["position"]["x"] = -10
    elif defect == "asset":
        bed["asset_id"] = "chair_standard_01"
    elif defect == "dimension":
        bed["dimensions_m"]["width"] = .3
    elif defect == "unknown":
        analysis.observations.objects[0].type = None
    elif defect == "placement":
        analysis.observations.objects[0].placement = "ceiling"
    else:
        data["objects"].pop(0)
    scene, _, issues, unresolved = select(analysis, data)
    assert scene is not None and issues and set(unresolved) == {"bed_observed"}
    assert len(scene.objects) == 4 and len(scene.openings) == 2
    assert scene.objects[0].dimensions_m.model_dump() == data["objects"][0 if defect == "missing" else 1]["dimensions_m"]
    assert any("Partial reconstruction" in note for note in scene.warnings)


@pytest.mark.parametrize("defect", ["missing", "asset", "outside"])
def test_deferred_support_also_defers_its_child_without_changing_the_relationship(defect):
    analysis, data = source(), draft_data()
    if defect == "missing":
        data["objects"].pop(1)
    elif defect == "asset":
        data["objects"][1]["asset_id"] = "chair_standard_01"
    else:
        data["objects"][1]["position"]["x"] = -10
    scene, _, _, unresolved = select(analysis, data)
    assert scene is not None
    assert set(unresolved) == {"desk_observed", "computer_observed"}
    assert unresolved["computer_observed"].reason.code == "dependency_unresolved"
    assert analysis.observations.objects[2].support_object_id == "desk_observed"


def test_bad_child_is_deferred_but_its_valid_support_stays():
    analysis, data = source(), draft_data()
    data["objects"][2]["support_object_id"] = "bed_observed"
    scene, _, _, unresolved = select(analysis, data)
    assert scene and set(unresolved) == {"computer_observed"}
    assert "desk_observed" in {obj.id for obj in scene.objects}


@pytest.mark.parametrize("reverse", [False, True])
def test_conflict_selection_is_stable_and_keeps_the_stronger_observation(reverse):
    analysis, data = source(), draft_data()
    analysis.observations.objects[3].confidence = "high"
    analysis.observations.objects[4].confidence = "low"
    data["objects"][4]["position"] = deepcopy(data["objects"][3]["position"])
    if reverse:
        analysis.observations.objects.reverse()
        data["objects"].reverse()
    scene, _, _, unresolved = select(analysis, data)
    assert scene and set(unresolved) == {"chair_b"}
    assert "chair_a" in {obj.id for obj in scene.objects}
    assert "chair_a" in unresolved["chair_b"].reason.entity_ids


def test_bad_opening_does_not_discard_furniture():
    analysis, data = source(), draft_data()
    data["openings"][0]["offset_m"] = 20
    scene, _, _, unresolved = select(analysis, data)
    assert scene and len(scene.objects) == 5
    assert set(unresolved) == {"window_observed"}
    assert unresolved["window_observed"].kind == "opening"


def test_deferred_opening_also_defers_its_covering_and_does_not_return_a_bare_room():
    analysis, data = curtained_door_case()
    data["openings"][0]["offset_m"] = 20
    scene, _, issues, unresolved = select(analysis, data)
    assert scene is None and issues
    assert set(unresolved) == {"door_observed", "curtains_observed"}
    assert unresolved["curtains_observed"].reason.code == "dependency_unresolved"


@pytest.mark.parametrize("defect", ["wall_mapping", "invented", "duplicate", "room_shape"])
def test_global_or_ambiguous_failures_do_not_masquerade_as_partial(defect):
    analysis, data = source(), draft_data()
    if defect == "wall_mapping":
        data["wall_assignments"] = []
    elif defect == "invented":
        data["objects"][0]["id"] = "invented_bed"
    elif defect == "duplicate":
        data["objects"].append(deepcopy(data["objects"][0]))
    else:
        analysis.observations.room_shape = "non_rectangular"
    scene, _, issues, _ = select(analysis, data)
    assert scene is None and issues


def test_unresolved_list_covers_all_200_observations_even_when_summary_is_capped():
    analysis, data = single_object_case()
    template = analysis.observations.objects[0]
    analysis.observations.objects.extend([
        template.model_copy(update={"id": f"unknown_{i}", "type": None}) for i in range(199)
    ])
    scene, _, issues, unresolved = select(analysis, data)
    assert scene and len(scene.objects) == 1
    assert len(issues) == 50 and len(unresolved) == 199
    assert all(item.reason.code == "unknown_object" for item in unresolved.values())


def test_200_colliding_objects_are_selected_in_a_batch_and_the_subset_is_revalidated(monkeypatch):
    analysis, data = single_object_case("chair")
    template, pose = analysis.observations.objects[0], data["objects"][0]
    analysis.observations.objects = [template.model_copy(update={"id": f"chair_{i:03d}"}) for i in range(200)]
    data["objects"] = [{**pose, "id": obj.id} for obj in analysis.observations.objects]
    calls = []
    original = partial_module.assemble

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(partial_module, "assemble", counted)
    scene, _, _, unresolved = select(analysis, data)
    assert scene and [obj.id for obj in scene.objects] == ["chair_000"]
    assert len(unresolved) == 199 and len(calls) == 2


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["unknown_object", "unsupported_placement"])
async def test_api_can_build_the_rest_when_one_observation_has_no_model(harness, kind):
    app, client, calls, actions = harness
    analysis = await analyzed(harness)
    observations = app.state.analysis_service._states[analysis["input_id"]].result.observations
    if kind == "unknown_object":
        observations.objects[0].type = None
    else:
        observations.objects[0].placement = "ceiling"
    data = draft_data()
    data["objects"].pop(0)
    actions.extend([scene_action(data), scene_action(data)])
    response = await client.post("/api/rooms/propose-scene", json=body(analysis, dimensions=confirmed(length_m=4, height_m=2.6)))
    assert response.status_code == 200 and response.json()["status"] == "partial", response.text
    assert response.json()["unresolved_entities"][0]["reason"]["code"] == kind
    assert len(calls) == 3


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["json", "timeout", "rate_limit", "refusal", "output_limit", "worse"])
async def test_failed_or_worse_repair_preserves_best_partial_and_cached_observations(harness, failure):
    app, client, calls, actions = harness
    analysis = await analyzed(harness)
    state = app.state.analysis_service._states[analysis["input_id"]]
    original = state.result.model_dump()
    bad = draft_data()
    bad["objects"][0]["position"]["x"] = -10
    actions.append(scene_action(bad))

    async def failed(request, payload):
        if failure == "timeout":
            raise httpx.ReadTimeout("PRIVATE_PROVIDER_DETAILS", request=request)
        if failure == "rate_limit":
            return httpx.Response(429, json={"error": {"message": "PRIVATE_PROVIDER_DETAILS"}})
        data = (await scene_action()(request, payload)).json()
        data["output"][0]["content"] = [{"type": "refusal", "refusal": "PRIVATE_PROVIDER_DETAILS"}]
        return httpx.Response(200, json=data)

    if failure == "json":
        actions.append(scene_action(text="PRIVATE_INVALID_JSON"))
    elif failure == "output_limit":
        actions.append(output_limited_scene())
    elif failure == "worse":
        worse = deepcopy(bad)
        worse["objects"][1]["position"]["x"] = -10
        actions.append(scene_action(worse))
    else:
        actions.append(failed)
    req = body(analysis, dimensions=confirmed(length_m=4, height_m=2.6))
    response = await client.post("/api/rooms/propose-scene", json=req)
    result = response.json()
    assert response.status_code == 200 and result["status"] == "partial", response.text
    assert len(result["scene"]["objects"]) == 4
    assert [item["entity_id"] for item in result["unresolved_entities"]] == ["bed_observed"]
    assert result["validation"]["valid"] and "PRIVATE" not in response.text
    assert state.result.model_dump() == original
    assert state.scene_attempts == 2 and len(calls) == 3
    assert (await client.post("/api/rooms/propose-scene", json=req)).json() == result
    assert len(calls) == 3
    parsed = SceneProposalResponse.model_validate_json(response.content)
    for patch in ({"status": "ready"}, {"unresolved_entities": []}, {"scene": None}, {"issues": []}):
        with pytest.raises(ValidationError):
            SceneProposalResponse.model_validate({**parsed.model_dump(), **patch})
    with pytest.raises(ValidationError):
        SceneProposalResponse.model_validate({**parsed.model_dump(), "unresolved_entities": [*parsed.unresolved_entities] * 2})


@pytest.mark.anyio
async def test_revocation_during_repair_also_revokes_the_earlier_partial(harness):
    app, client, calls, actions = harness
    analysis = await analyzed(harness)
    state = app.state.analysis_service._states[analysis["input_id"]]
    bad = draft_data()
    bad["objects"][0]["position"]["x"] = -10
    actions.append(scene_action(bad))

    async def revoked(request, payload):
        await anyio.to_thread.run_sync(app.state.upload_service.storage.delete, analysis["input_id"], state.owner)
        return await scene_action()(request, payload)

    actions.append(revoked)
    response = await client.post("/api/rooms/propose-scene", json=body(analysis, dimensions=confirmed(length_m=4, height_m=2.6)))
    assert response.status_code == 404 and len(calls) == 3
    assert state.proposal is None


@pytest.mark.anyio
async def test_repair_request_limit_preserves_partial_without_an_extra_paid_call(harness, monkeypatch):
    app, client, calls, actions = harness
    analysis = await analyzed(harness)
    bad = draft_data()
    bad["objects"][0]["position"]["x"] = -10
    actions.append(scene_action(bad))
    original = service_module.build_proposal_request

    def limited(settings, analysis, measurements, draft, issues, images=None, *, operation="scene_proposal"):
        if draft is not None:
            raise AnalysisFailure(413, "request_too_large", "The repair request exceeds its input limit.")
        return original(settings, analysis, measurements, draft, issues, images, operation=operation)

    monkeypatch.setattr(service_module, "build_proposal_request", limited)
    req = body(analysis, dimensions=confirmed(length_m=4, height_m=2.6))
    response = await client.post("/api/rooms/propose-scene", json=req)
    assert response.status_code == 200 and response.json()["status"] == "partial", response.text
    assert response.json()["issues"][0]["code"] == "repair_failed"
    assert len(calls) == 2
    assert app.state.analysis_service._states[analysis["input_id"]].scene_attempts == 1
    assert (await client.post("/api/rooms/propose-scene", json=req)).json() == response.json()
    assert len(calls) == 2


@pytest.mark.parametrize('reverse', [False, True])
def test_conflict_keeps_a_high_confidence_sofa_over_a_side_table_regardless_of_ids(reverse):
    analysis, data = single_object_case('sofa')
    sofa = analysis.observations.objects[0]
    sofa.id, sofa.confidence, sofa.appears_fixed = 'z_sofa', 'high', False
    side = sofa.model_copy(update={'id': 'a_side_table', 'type': 'side_table'})
    analysis.observations.objects.append(side)
    data['objects'][0]['id'] = sofa.id
    data['objects'].append({**deepcopy(data['objects'][0]), 'id': side.id})
    if reverse:
        analysis.observations.objects.reverse()
        data['objects'].reverse()
    scene, _, _, unresolved = select(analysis, data)
    assert scene and [obj.id for obj in scene.objects] == ['z_sofa']
    assert set(unresolved) == {'a_side_table'}


@pytest.mark.anyio
async def test_repair_cannot_trade_valid_unaffected_chairs_for_a_restored_sofa(harness):
    app, client, _, actions = harness
    response = await analyzed(harness)
    cached = app.state.analysis_service._states[response['input_id']].result
    analysis, data = single_object_case('sofa')
    sofa = analysis.observations.objects[0]
    sofa.id, sofa.confidence, sofa.appears_fixed = 'sofa', 'high', False
    analysis.observations.image_ids = cached.observations.image_ids
    sofa.source_image_ids = cached.observations.image_ids
    data['objects'][0]['id'] = sofa.id
    for i, x in enumerate((.5, 3.5)):
        chair = sofa.model_copy(update={'id': f'chair_{i}', 'type': 'chair'})
        analysis.observations.objects.append(chair)
        data['objects'].append({**deepcopy(data['objects'][0]), 'id': chair.id, 'position': {'x': x, 'y': 0, 'z': .5}})
    cached.observations = analysis.observations
    first, second = deepcopy(data), deepcopy(data)
    first['objects'][0]['position']['x'] = -10
    for pose in second['objects'][1:]:
        pose['position']['x'] = -10
    actions.extend([scene_action(first), scene_action(second)])
    proposal = await client.post('/api/rooms/propose-scene', json=body(response, dimensions=confirmed(length_m=4, height_m=2.6)))
    assert proposal.status_code == 200, proposal.text
    result = proposal.json()
    assert result['status'] == 'partial' and result['validation']['valid']
    assert [obj['id'] for obj in result['scene']['objects']] == ['chair_0', 'chair_1']
    assert any(item['code'] == 'repair_failed' for item in result['issues'])
    assert {item['entity_id'] for item in result['unresolved_entities']} == {'sofa'}
