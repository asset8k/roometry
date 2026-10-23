"""Physical wall dimensions, provider grounding and checkpointed clarification."""

import json
from copy import deepcopy
from io import BytesIO
from math import pi
from uuid import uuid4

import httpx
import pytest
from PIL import Image
from pydantic import ValidationError

from roometry.config import Settings
from roometry.agents.clarification import answer_updates, question_for
from roometry.agents.state import ClarificationAnswer
from roometry.scenes.geometry import wall_span
from roometry.scenes.schemas import RoomScene
from roometry.uploads.schemas import InputDimensions
from roometry.uploads.session import COOKIE_NAME, owner_scope
from roometry.vision.proposal_request import build_proposal_request
from roometry.vision.proposal_schemas import SceneDraft
from roometry.vision.proposals import assemble, resolve_measurements
from roometry.vision.schemas import ReconstructionConstraints, RoomAnalysisResponse, WallAxisAnswer
from roometry.vision.wall_axes import resolve_wall_axes
from tests import test_vision_api
from tests.test_agent_tools import decision
from tests.test_reconstruction_quality import facing_case
from tests.test_scene_proposals import scene_action

anyio_backend = test_vision_api.anyio_backend
isolated_environment = test_vision_api.isolated_environment
harness = test_vision_api.harness


def axis_case():
    source, data = facing_case()
    raw = source.model_dump()
    obs = raw["observations"]
    obs["walls"][0]["label"] = "Window wall"
    obs["walls"].append({**deepcopy(obs["walls"][0]), "id": "tv_wall", "label": "TV wall"})
    obs["objects"].append({**deepcopy(obs["objects"][0]), "id": "television", "type": "tv", "label": "Television", "placement": "wall", "wall_id": "tv_wall", "appears_fixed": True})
    obs["relationships"][0]["target_id"] = "television"
    obs["relationships"].append({"id": "corner", "subject_id": "window_wall", "target_id": "tv_wall", "kind": "adjacent_wall", "confidence": "high", "source_image_ids": obs["image_ids"], "evidence": "The window and TV walls meet at a visible corner."})
    data["wall_assignments"].append({"observation_id": "tv_wall", "wall": "west"})
    data["objects"][0]["rotation_y_rad"] = 3 * pi / 2
    data["objects"].append({**deepcopy(data["objects"][0]), "id": "television", "asset_id": "tv_standard_01", "placement": "wall", "wall": "west", "position": {"x": .04, "y": 1., "z": 2.}, "rotation_y_rad": pi / 2, "dimensions_m": {"width": 1.4, "height": .9, "depth": .08}})
    return RoomAnalysisResponse.model_validate(raw), data


def sizes(width=4., length=6.):
    return InputDimensions(width_m=width, length_m=length, height_m=3., measurements_source="user_confirmed")


def confident(analysis, role="shorter"):
    raw = analysis.model_dump()
    raw["observations"]["walls"][0]["span"] = {"role": role, "confidence": "high", "source_image_ids": analysis.observations.walls[0].source_image_ids, "evidence": "Both wall spans are established by the visible room boundaries across views."}
    return RoomAnalysisResponse.model_validate(raw)


@pytest.mark.parametrize("width,length,axis", [(4.,6.,"width_m"), (6.,4.,"length_m")])
def test_span_evidence_binds_to_the_actual_shorter_dimension_without_sorting_measurements(width,length,axis):
    analysis, _ = axis_case()
    analysis = confident(analysis)
    original = analysis.model_dump()
    result = resolve_wall_axes(analysis, sizes(width,length), ReconstructionConstraints())
    assert result.status == "resolved"
    bindings = {item.observation_id:item for item in result.bindings}
    assert bindings["window_wall"].axis == axis and bindings["window_wall"].source == "photo"
    assert bindings["tv_wall"].axis != axis
    assert analysis.model_dump() == original


def test_windows_are_allowed_on_the_longer_wall():
    analysis, _ = axis_case()
    result = resolve_wall_axes(confident(analysis,"longer"), sizes(), ReconstructionConstraints())
    assert next(b for b in result.bindings if b.observation_id == "window_wall").axis == "length_m"


@pytest.mark.parametrize("width,length", [(4.,4.),(4.,4.03)])
def test_square_and_nearly_square_rooms_do_not_ask_an_axis_question(width,length):
    analysis, _ = axis_case()
    result = resolve_wall_axes(analysis, sizes(width,length), ReconstructionConstraints())
    assert result.status == "not_applicable" and not result.bindings


def test_ambiguous_spans_offer_identifiable_adjacent_walls():
    analysis, _ = axis_case()
    result = resolve_wall_axes(analysis, sizes(), ReconstructionConstraints())
    assert result.status == "needs_clarification"
    assert result.reference_span_m == 4 and result.candidate_wall_ids == ["window_wall","tv_wall"]


def test_user_answer_overrides_photo_hypotheses_and_preserves_observations():
    analysis, _ = axis_case()
    analysis = confident(analysis)
    old = analysis.model_dump()
    result = resolve_wall_axes(analysis,sizes(),ReconstructionConstraints(wall_axis=WallAxisAnswer(axis="width_m",wall_id="tv_wall")))
    assert {b.observation_id:b.axis for b in result.bindings} == {"window_wall":"length_m","tv_wall":"width_m"}
    assert all(b.source == "user" for b in result.bindings)
    assert analysis.model_dump() == old


def test_conflicting_photo_span_hypotheses_require_a_user_answer():
    analysis, _ = axis_case()
    raw = confident(analysis).model_dump()
    raw["observations"]["walls"][1]["span"] = deepcopy(raw["observations"]["walls"][0]["span"])
    analysis = RoomAnalysisResponse.model_validate(raw)
    assert resolve_wall_axes(analysis, sizes(), ReconstructionConstraints()).status == "needs_clarification"
    result = resolve_wall_axes(analysis, sizes(), ReconstructionConstraints(wall_axis=WallAxisAnswer(axis="width_m", wall_id="window_wall")))
    assert result.status == "resolved" and all(b.source == "user" for b in result.bindings)


def test_foreign_wall_or_span_evidence_is_rejected():
    analysis, _ = axis_case()
    with pytest.raises(ValueError):
        resolve_wall_axes(analysis,sizes(),ReconstructionConstraints(wall_axis=WallAxisAnswer(axis="width_m",wall_id="invented_wall")))
    raw = confident(analysis).model_dump()
    raw["observations"]["walls"][0]["span"]["source_image_ids"] = [str(uuid4())]
    with pytest.raises(ValidationError):
        RoomAnalysisResponse.model_validate(raw)


def test_wrong_axis_is_rejected_before_placing_furniture_and_is_excluded_from_provider_schema():
    analysis,data = axis_case()
    analysis.wall_axes = resolve_wall_axes(analysis,sizes(),ReconstructionConstraints(wall_axis=WallAxisAnswer(axis="width_m",wall_id="window_wall")))
    measurements = resolve_measurements(analysis,{"width_m":4.,"length_m":6.,"height_m":3.},False)
    scene,geometry,issues = assemble(analysis,measurements,SceneDraft.model_validate(data))
    assert scene is not None and geometry.valid and not issues
    assert wall_span(scene.room,scene.openings[0].wall) == 4
    bad = deepcopy(data)
    bad["wall_assignments"][0]["wall"] = "east"
    bad["wall_assignments"][1]["wall"] = "north"
    scene,geometry,issues = assemble(analysis,measurements,SceneDraft.model_validate(bad))
    assert scene is None and geometry is None
    assert all(item.code == "wall_mapping" for item in issues)
    payload = build_proposal_request(Settings(_env_file=None),analysis,measurements,None,[])
    variants = payload["text"]["format"]["schema"]["properties"]["wall_assignments"]["items"]["anyOf"]
    allowed = {v["properties"]["observation_id"]["enum"][0]: v["properties"]["wall"]["enum"] for v in variants}
    assert allowed == {"window_wall":["north","south"],"tv_wall":["west","east"]}
    restored, _, _ = assemble(analysis, measurements, SceneDraft.model_validate(data))
    assert restored is not None
    raw = restored.model_dump()
    raw["wall_mapping"][0]["wall"] = "east"
    with pytest.raises(ValidationError, match="resolved dimension axis"):
        RoomScene.model_validate(raw)


def photo_response(example):
    async def action(request,payload):
        response = test_vision_api.provider_response(payload)
        ids = json.loads(payload["input"][0]["content"][0]["text"])["image_ids"]
        obs = example.observations.model_dump(mode="json")
        obs["image_ids"] = ids
        for kind in ("walls","objects","openings","relationships"):
            for entity in obs[kind]:
                entity["source_image_ids"] = ids
                if entity.get("span"):
                    entity["span"]["source_image_ids"] = ids
        response["output"][0]["content"][0]["text"] = json.dumps(obs)
        return httpx.Response(200,json=response)
    return action


async def upload_room(harness):
    app,client,_,_ = harness
    stream = BytesIO()
    Image.new("RGB",(256,256),"green").save(stream,"PNG")
    result = await client.post("/api/rooms/inputs",files=[("images",("room.png",stream.getvalue(),"image/png")),("options",(None,json.dumps({"room_type":"living_room","goals":[],"dimensions":sizes().model_dump()})))])
    assert result.status_code == 201,result.text
    return app.state.agent_runs,owner_scope(client.cookies[COOKIE_NAME]),result.json()


@pytest.mark.anyio
async def test_single_identifiable_wall_asks_numeric_choices_and_second_answer_selects_long_span(harness):
    runs, owner, upload = await upload_room(harness)
    example, _ = facing_case()
    initial = await runs.create(upload["input_id"], owner)
    state = initial.model_copy(update={"analysis": example, "status": "running"})
    question = question_for(state, "room_wall_axis")
    assert len(question.entity_ids) == 1
    assert [c.label for c in question.choices][:2] == ["4 m", "6 m"]
    state = state.model_copy(update={"status": "awaiting_answer", "questions": [question], "pending_question": question})
    updates = answer_updates(state, ClarificationAnswer(question_id=question.id, choice_id="wall_second"))
    constraints = ReconstructionConstraints.model_validate(updates["reconstruction_constraints"])
    assert constraints.wall_axis.axis == "length_m"
    resolved = resolve_wall_axes(example, sizes(), constraints)
    assert resolved.bindings[0].axis == "length_m" and resolved.bindings[0].source == "user"
    assert state.confirmed_dimensions == sizes()


@pytest.mark.anyio
async def test_machine_style_wall_labels_are_readable_without_changing_answer_bindings(harness):
    runs, owner, upload = await upload_room(harness)
    example, _ = axis_case()
    example.observations.walls[0].label = 'window_wall'
    example.observations.walls[1].label = 'desk_side_marble_wall'
    initial = await runs.create(upload['input_id'], owner)
    state = initial.model_copy(update={'analysis': example, 'status': 'running'})
    question = question_for(state, 'room_wall_axis')
    assert [c.label for c in question.choices][:2] == ['Window wall', 'Desk side marble wall']
    assert question.entity_ids == ['window_wall', 'tv_wall']
    state = state.model_copy(update={'pending_question': question, 'questions': [question]})
    update = answer_updates(state, ClarificationAnswer(question_id=question.id, choice_id='wall_second'))
    assert update['reconstruction_constraints']['wall_axis']['wall_id'] == 'tv_wall'
    assert example.observations.walls[0].label == 'window_wall'  # Source evidence is immutable.


@pytest.mark.anyio
async def test_question_cap_does_not_create_a_fourth_question_or_claim_orientation_is_confirmed(harness):
    runs, owner, upload = await upload_room(harness)
    example, _ = axis_case()
    initial = await runs.create(upload["input_id"], owner)
    state = initial.model_copy(update={"analysis": example, "status": "running"})
    question = question_for(state, "room_wall_axis")
    state = state.model_copy(update={"questions": [question.model_copy(update={"id":f"q{i}"}) for i in range(1,4)]})
    with pytest.raises(ValueError, match="limit"):
        question_for(state, "room_wall_axis")
    result = resolve_wall_axes(example, sizes(), state.reconstruction_constraints, allow_ambiguous=True)
    assert result.status == "estimated" and not result.bindings and result.warnings


@pytest.mark.anyio
@pytest.mark.parametrize("choice",["wall_first","wall_second","not_sure"])
async def test_real_interrupt_binds_the_selected_wall_and_resumes_without_paid_analysis_replay(harness,choice):
    app,client,calls,actions = harness
    runs,owner,upload = await upload_room(harness)
    example,data = axis_case()
    actions.extend([decision("analyze_photos"),photo_response(example)])
    initial = await runs.create(upload["input_id"],owner)
    paused = await runs.execute(initial.run_id,owner)
    assert paused.status == "awaiting_answer",paused
    q = paused.pending_question
    assert q.kind == "room_wall_axis" and q.question == "Which wall measures 4 m?"
    assert [c.label for c in q.choices][:2] == ["Window wall","TV wall"]
    assert len(calls) == 2 and app.state.analysis_service._states[upload["input_id"]].scene_attempts == 0
    before = paused.analysis.model_dump()
    bad_answer = await client.post(f"/api/agent-runs/{initial.run_id}/answer",json={"question_id":q.id,"choice_id":"invented"})
    assert bad_answer.status_code == 422 and len(calls) == 2
    if choice == "wall_second":
        data["wall_assignments"] = [{"observation_id":"window_wall","wall":"east"},{"observation_id":"tv_wall","wall":"north"}]
        data["openings"][0]["wall"] = "east"
        data["objects"][0]["rotation_y_rad"] = pi
        data["objects"][1].update(wall="north",rotation_y_rad=0.,position={"x":2.,"y":1.,"z":.04})
    actions.extend([decision("propose_scene"),scene_action(data)])
    if choice == "not_sure":
        actions.append(decision("validate_scene"))
    actions.append(decision("finish"))
    answer = {"question_id":q.id,"choice_id":choice}
    response = await client.post(f"/api/agent-runs/{initial.run_id}/answer",json=answer)
    assert response.status_code == 200,response.text
    result = response.json()
    assert result["status"] == ("partial" if choice == "not_sure" else "completed"),result
    scene = result["working_scene"]
    assert scene is not None and scene["room"]["width_m"] == 4 and scene["room"]["length_m"] == 6
    assert result["analysis"] == paused.analysis.model_dump(mode="json") and paused.analysis.model_dump() == before
    expected_calls = 6 if choice == "not_sure" else 5
    assert len(calls) == expected_calls and len(result["questions"]) == 1
    if choice == "not_sure":
        assert result["proposal"]["wall_axes"]["status"] == "estimated"
        assert result["proposal"]["fidelity"]["recognizable"] is not True
        validation = next(f for f in result["tool_feedback"] if f["name"] == "validate_scene")
        assert validation["details"]["fidelity"]["recognizable"] is not True
    else:
        assert result["proposal"]["wall_axes"]["status"] == "resolved"
        assert scene["openings"][0]["wall"] in ({"north","south"} if choice == "wall_first" else {"east","west"})
        assert all(binding["source"] == "user" for binding in scene["wall_axis_bindings"])
    assert (await client.post(f"/api/agent-runs/{initial.run_id}/answer",json=answer)).json() == result
    assert len(calls) == expected_calls


@pytest.mark.anyio
async def test_api_requests_wall_assignment_without_spending_a_construction_call(harness):
    app,client,calls,actions = harness
    _,_,upload = await upload_room(harness)
    example,data = axis_case()
    actions.append(photo_response(example))
    analysis = (await client.post("/api/rooms/analyze",json={"input_id":upload["input_id"]})).json()
    response = await client.post("/api/rooms/propose-scene",json={"analysis_id":analysis["analysis_id"]})
    assert response.status_code == 200,response.text
    assert response.json()["status"] == "needs_wall_assignment" and response.json()["scene"] is None
    assert len(calls) == 1 and app.state.analysis_service._states[upload["input_id"]].scene_attempts == 0
    actions.append(scene_action(data))
    response = await client.post("/api/rooms/propose-scene",json={"analysis_id":analysis["analysis_id"],"constraints":{"wall_axis":{"axis":"width_m","wall_id":"window_wall"}}})
    assert response.status_code == 200 and response.json()["status"] == "ready",response.text
    assert len(calls) == 2
