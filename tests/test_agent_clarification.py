"""Actual LangGraph interrupts and HTTP resume with mocked provider requests."""

import json
from datetime import timedelta
from uuid import uuid4

import anyio
import httpx
import pytest

from roometry.agents.graph import build_graph
from roometry.agents.state import ClarificationAnswer, RunLimits
from roometry.uploads.session import COOKIE_NAME
from roometry.uploads.schemas import InputDimensions
from roometry.vision.proposal_schemas import SceneProposalRequest
from tests import test_vision_api
from tests.test_agent_tools import context, decision, photo, prepared, stages
from tests.test_scene_proposals import scene_action

anyio_backend = test_vision_api.anyio_backend
isolated_environment = test_vision_api.isolated_environment
harness = test_vision_api.harness
pytestmark = pytest.mark.anyio


async def paused_dimensions(harness):
    _, client, _, actions = harness
    runs, owner, uploaded = await prepared(harness, complete=False)
    actions.extend([decision("analyze_photos"), photo, decision("propose_scene")])
    request = {"input_id": uploaded["input_id"], "request_id": str(uuid4())}
    response = await client.post("/api/agent-runs", json=request)
    assert response.status_code == 200, response.text
    state = response.json()
    assert state["status"] == "awaiting_answer", state
    return runs, owner, request, state


def measurements(question_id="q1"):
    return {"question_id": question_id, "choice_id": "enter_measurements",
            "dimensions": {"length_m": 4, "height_m": 2.6}}


async def test_real_interrupt_resumes_with_measurements_and_never_replays_analysis(harness):
    app, client, calls, actions = harness
    runs, owner, request, paused = await paused_dimensions(harness)
    run_id = paused["run_id"]
    entry = runs.checkpoints.get(run_id, owner, runs.now())
    snapshot = await build_graph(entry.saver).aget_state(runs._config(entry))
    assert snapshot.next == ("clarify",)
    assert len(snapshot.interrupts) == 1
    assert snapshot.interrupts[0].value == paused["pending_question"]
    assert len(paused["pending_question"]["choices"]) == 3 and len(calls) == 3
    for _ in range(2):
        assert (await client.get(f"/api/agent-runs/{run_id}")).json() == paused
        assert (await runs.execute(run_id, owner)).status == "awaiting_answer"
        assert (await client.post("/api/agent-runs", json=request)).json() == paused
    assert len(calls) == 3
    actions.extend([decision("propose_scene"), scene_action(), decision("finish")])
    response = await client.post(f"/api/agent-runs/{run_id}/answer", json=measurements())
    assert response.status_code == 200, response.text
    complete = response.json()
    assert complete["status"] == "completed" and complete["pending_question"] is None
    assert complete["analysis"] == paused["analysis"]
    assert complete["input_options"] == paused["input_options"]
    assert complete["confirmed_dimensions"] == {"width_m": 4., "length_m": 4., "height_m": 2.6, "measurements_source": "user_confirmed"}
    assert complete["proposal"]["estimated_axes"] == []
    assert complete["working_scene"]["room"]["length_m"] == 4
    assert complete["clarification_answers"] == {"q1": "enter_measurements"}
    assert context(calls[3])["confirmed_dimensions"]["length_m"] == 4
    assert complete["usage"]["model_calls"] == len(calls) == 6
    assert [record.stage for record in app.state.analysis_service.usage_records] == ["analysis", "scene_proposal"]
    for _ in range(2):
        assert (await client.post(f"/api/agent-runs/{run_id}/answer", json=measurements())).json() == complete
        assert (await client.post("/api/agent-runs", json=request)).json() == complete
    assert len(calls) == 6
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("defect", ["choice", "question", "no_new_values", "confirmed_override", "extra_fields", "nan", "wrong_type", "wrong_choice_measurements"])
async def test_invalid_answers_leave_the_same_interrupt_unconsumed(harness, defect):
    _, client, calls, _ = harness
    _, _, _, paused = await paused_dimensions(harness)
    body = measurements()
    if defect == "choice":
        body["choice_id"] = "invented"
    elif defect == "question":
        body["question_id"] = "q2"
    elif defect == "no_new_values":
        body["dimensions"] = {"width_m": 4}
    elif defect == "confirmed_override":
        body["dimensions"]["width_m"] = 5
    elif defect == "extra_fields":
        body["owner"] = "PRIVATE_FOREIGN_OWNER"
    elif defect == "nan":
        body["dimensions"]["length_m"] = float("nan")
    elif defect == "wrong_type":
        body["dimensions"]["length_m"] = "4"
    else:
        body["choice_id"] = "leave_unknown"
    response = await client.post(f"/api/agent-runs/{paused['run_id']}/answer", content=json.dumps(body), headers={"content-type": "application/json"})
    assert response.status_code == 422, response.text
    assert "PRIVATE" not in response.text
    current = (await client.get(f"/api/agent-runs/{paused['run_id']}")).json()
    assert current == paused and len(calls) == 3


@pytest.mark.parametrize("choice,status", [("leave_unknown", "incomplete"), ("cancel_run", "cancelled")])
async def test_unknown_or_cancel_answers_stop_without_manufacturing_measurements(harness, choice, status):
    _, client, calls, _ = harness
    _, _, _, paused = await paused_dimensions(harness)
    body = {"question_id": "q1", "choice_id": choice}
    response = await client.post(f"/api/agent-runs/{paused['run_id']}/answer", json=body)
    assert response.status_code == 200, response.text
    state = response.json()
    assert state["status"] == status and state["working_scene"] is None
    assert state["confirmed_dimensions"] == paused["confirmed_dimensions"]
    assert state["pending_question"] is None and len(calls) == 3
    assert (await client.post(f"/api/agent-runs/{paused['run_id']}/answer", json=body)).json() == state
    conflict = await client.post(f"/api/agent-runs/{paused['run_id']}/answer", json=measurements())
    assert conflict.status_code == 409 and len(calls) == 3


@pytest.mark.parametrize("choice,locked", [("keep_fixed", True), ("not_sure", True), ("allow_movement", False)])
async def test_mobility_answer_changes_an_enforced_constraint_and_preserves_base(harness, choice, locked):
    _, client, calls, actions = harness
    runs, owner, uploaded = await prepared(harness)
    stages(actions)
    actions.append(decision("request_clarification", {"kind": "object_mobility", "object_id": "chair_a"}))
    initial = await runs.create(uploaded["input_id"], owner)
    paused = await runs.execute(initial.run_id, owner)
    assert paused.status == "awaiting_answer" and paused.pending_question.entity_ids == ["chair_a"]
    actions.extend([decision("move_object", {"object_id": "chair_a", "position": {"x": 2.4, "z": 2.4}}), decision("finish")])
    response = await client.post(f"/api/agent-runs/{initial.run_id}/answer", json={"question_id": "q1", "choice_id": choice})
    assert response.status_code == 200, response.text
    state = response.json()
    assert state["status"] == "completed" and state["object_mobility"] == {"chair_a": not locked}
    chair = next(obj for obj in state["working_scene"]["objects"] if obj["id"] == "chair_a")
    assert chair["locked"] is locked and chair["position"]["z"] == (2 if locked else 2.4)
    if locked:
        assert state["tool_feedback"][-2]["details"]["edit_errors"][0]["code"] == "object_locked"
    assert state["base_scene"] == paused.base_scene.model_dump(mode="json")
    assert state["analysis"] == paused.analysis.model_dump(mode="json")
    assert context(calls[-2])["object_mobility"] == {"chair_a": not locked}
    assert len(calls) == state["usage"]["model_calls"] == 7


async def test_at_most_three_questions_and_accepted_answers_do_not_reset_budgets(harness):
    _, _, calls, actions = harness
    runs, owner, uploaded = await prepared(harness)
    stages(actions)
    actions.append(decision("request_clarification", {"kind": "object_mobility", "object_id": "chair_a"}))
    initial = await runs.create(uploaded["input_id"], owner, limits=RunLimits(max_steps=16, max_model_calls=16))
    state = await runs.execute(initial.run_id, owner)
    for index, next_object in enumerate(["chair_b", "desk_observed", None], start=1):
        if next_object:
            actions.append(decision("request_clarification", {"kind": "object_mobility", "object_id": next_object}))
        else:
            actions.extend([decision("request_clarification", {"kind": "object_mobility", "object_id": "bed_observed"}), decision("finish")])
        state = await runs.answer(initial.run_id, owner, ClarificationAnswer(question_id=f"q{index}", choice_id="allow_movement"))
        if next_object:
            assert state.status == "awaiting_answer" and state.pending_question.id == f"q{index+1}"
    assert state.status == "completed" and len(state.questions) == len(state.submitted_answers) == 3
    assert "request_clarification" not in {tool["name"] for tool in calls[-2]["tools"]}
    assert state.tool_feedback[-2].outcome == "rejected"
    assert state.usage.model_calls == len(calls) == 9


async def test_cancel_a_waiting_run_does_not_reenter_interrupt_or_provider(harness):
    _, client, calls, _ = harness
    runs, owner, _, paused = await paused_dimensions(harness)
    path = f"/api/agent-runs/{paused['run_id']}"
    response = await client.post(path + "/cancel", json={})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled" and response.json()["pending_question"] is None
    assert (await client.post(path + "/answer", json=measurements())).status_code == 409
    assert (await runs.execute(paused["run_id"], owner)).status == "cancelled"
    entry = runs.checkpoints.get(paused["run_id"], owner, runs.now())
    assert not (await build_graph(entry.saver).aget_state(runs._config(entry))).next
    assert len(calls) == 3


@pytest.mark.parametrize("revoke", ["expiry", "delete"])
async def test_expired_and_revoked_questions_cannot_resume(harness, revoke):
    _, client, calls, _ = harness
    runs, _, request, paused = await paused_dimensions(harness)
    if revoke == "expiry":
        later = runs.now() + timedelta(hours=2)
        runs.now = lambda: later
    else:
        assert (await client.delete(f"/api/rooms/inputs/{request['input_id']}")).status_code == 204
    path = f"/api/agent-runs/{paused['run_id']}"
    for operation in (client.get(path), client.post(path + "/answer", json=measurements()), client.post(path + "/cancel", json={})):
        response = await operation
        assert response.status_code == 404 and response.headers["cache-control"] == "no-store"
    assert len(calls) == 3 and not runs.checkpoints._runs


async def test_foreign_sessions_and_origins_cannot_read_answer_or_cancel(harness):
    _, client, calls, _ = harness
    _, _, request, paused = await paused_dimensions(harness)
    path = f"/api/agent-runs/{paused['run_id']}"
    foreign = {"cookie": f"{COOKIE_NAME}={'A'*43}"}
    assert (await client.get(path, headers=foreign)).status_code == 404
    assert (await client.post(path + "/answer", json=measurements(), headers=foreign)).status_code == 404
    assert (await client.post(path + "/cancel", json={}, headers=foreign)).status_code == 404
    assert (await client.post("/api/agent-runs", json=request, headers=foreign)).status_code == 404
    for suffix, body in [("/answer", measurements()), ("/cancel", {})]:
        assert (await client.post(path + suffix, json=body, headers={"origin": "https://foreign.example"})).status_code == 403
    assert (await client.post("/api/agent-runs", json=request, headers={"origin": "null"})).status_code == 403
    assert (await client.get(path)).json() == paused and len(calls) == 3


async def test_duplicate_answers_while_resuming_return_state_without_another_paid_call(harness):
    _, client, calls, actions = harness
    _, _, _, paused = await paused_dimensions(harness)
    entered, release = anyio.Event(), anyio.Event()
    responses = []

    async def waiting(request, payload):
        entered.set()
        await release.wait()
        return await decision("propose_scene")(request, payload)

    actions.extend([waiting, scene_action(), decision("finish")])
    path = f"/api/agent-runs/{paused['run_id']}/answer"

    async def submit():
        responses.append(await client.post(path, json=measurements()))

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(submit)
        await entered.wait()
        duplicate = await client.post(path, json=measurements())
        assert duplicate.status_code == 200 and duplicate.json()["status"] == "running"
        other = measurements()
        other["dimensions"]["length_m"] = 5
        assert (await client.post(path, json=other)).status_code == 409
        assert len(calls) == 4
        release.set()
    assert responses[0].status_code == 200 and responses[0].json()["status"] == "completed"
    assert len(calls) == 6


async def test_provider_failure_after_answer_is_not_replayed_by_duplicate_submission(harness):
    _, client, calls, actions = harness
    _, _, _, paused = await paused_dimensions(harness)

    async def failed(request, payload):
        raise httpx.ReadTimeout("PRIVATE_PROVIDER_DETAILS", request=request)

    actions.append(failed)
    path = f"/api/agent-runs/{paused['run_id']}/answer"
    response = await client.post(path, json=measurements())
    assert response.status_code == 200 and response.json()["status"] == "failed"
    assert response.json()["analysis"] == paused["analysis"]
    assert response.json()["usage"]["model_calls"] == len(calls) == 4
    assert "PRIVATE" not in response.text
    assert (await client.post(path, json=measurements())).json() == response.json()
    assert len(calls) == 4


@pytest.mark.parametrize("body,status", [pytest.param('{"question_id":"q1","question_id":"q2","choice_id":"leave_unknown"}', 422, id="duplicate-fields"), pytest.param('{"x":"' + 'x'*20000 + '"}', 413, id="oversized-body")])
async def test_agent_json_is_bounded_and_duplicate_fields_are_rejected(harness, body, status):
    _, client, calls, _ = harness
    _, _, _, paused = await paused_dimensions(harness)
    response = await client.post(f"/api/agent-runs/{paused['run_id']}/answer", content=body, headers={"content-type": "application/json"})
    assert response.status_code == status and len(calls) == 3


async def test_concurrent_retried_starts_share_one_run_and_one_decision(harness):
    _, client, calls, actions = harness
    runs, owner, uploaded = await prepared(harness)
    entered, release = anyio.Event(), anyio.Event()
    responses = []

    async def waiting(request, payload):
        entered.set()
        await release.wait()
        return await decision("finish")(request, payload)

    actions.append(waiting)
    body = {"input_id": uploaded["input_id"], "request_id": str(uuid4())}

    async def start():
        responses.append(await client.post("/api/agent-runs", json=body))

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(start)
        await entered.wait()
        duplicate = await client.post("/api/agent-runs", json=body)
        assert duplicate.status_code == 200 and duplicate.json()["status"] == "running"
        conflict = await client.post("/api/agent-runs", json={**body, "input_id": str(uuid4())})
        assert conflict.status_code == 409 and len(calls) == 1
        release.set()
    assert duplicate.json()["run_id"] == responses[0].json()["run_id"]
    assert len(runs.checkpoints._runs) == len(calls) == 1


@pytest.mark.parametrize("abort", [False, True])
async def test_cancellation_during_answer_processing_does_not_replay_the_accepted_answer(harness, abort):
    _, client, calls, actions = harness
    runs, owner, _, paused = await paused_dimensions(harness)
    entered, release = anyio.Event(), anyio.Event()
    responses = []

    async def waiting(request, payload):
        entered.set()
        await release.wait()
        return await decision("propose_scene")(request, payload)

    actions.append(waiting)
    path = f"/api/agent-runs/{paused['run_id']}"

    async def submit():
        responses.append(await client.post(path + "/answer", json=measurements()))

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(submit)
        await entered.wait()
        if abort:
            tasks.cancel_scope.cancel()
        else:
            assert (await client.post(path + "/cancel", json={})).status_code == 200
            release.set()
    state = await runs.read(paused["run_id"], owner)
    assert state.status == "cancelled" and state.pending_question is None
    assert state.clarification_answers == {"q1": "enter_measurements"}
    assert state.confirmed_dimensions.length_m == 4
    assert state.usage.model_calls == len(calls) == 4
    assert state.usage.unknown_usage_calls == int(abort)
    assert (await client.post(path + "/answer", json=measurements())).json() == state.model_dump(mode="json")
    assert len(calls) == 4


async def test_confirmations_from_another_flow_are_not_inherited_by_an_agent_run(harness):
    app, _, calls, actions = harness
    runs, owner, request, paused = await paused_dimensions(harness)
    actions.append(scene_action())
    legacy = await app.state.analysis_service.propose(SceneProposalRequest(
        analysis_id=paused["analysis"]["analysis_id"],
        dimensions=InputDimensions(width_m=4, length_m=4, height_m=2.6, measurements_source="user_confirmed"),
    ), owner)
    assert legacy.status == "ready"
    actions.extend([decision("analyze_photos"), decision("propose_scene")])
    initial = await runs.create(request["input_id"], owner)
    state = await runs.execute(initial.run_id, owner)
    assert state.status == "awaiting_answer"
    assert state.pending_question.dimension_axes == ["length_m", "height_m"]
    assert state.analysis.analysis_id == paused["analysis"]["analysis_id"]
    assert state.usage.model_calls == 2 and len(calls) == 6


async def test_same_fact_is_not_asked_twice_and_fixed_objects_cannot_be_unlocked(harness):
    _, _, calls, actions = harness
    runs, owner, uploaded = await prepared(harness)
    stages(actions)
    actions.append(decision("request_clarification", {"kind": "object_mobility", "object_id": "chair_a"}))
    initial = await runs.create(uploaded["input_id"], owner, limits=RunLimits(max_model_calls=16))
    assert (await runs.execute(initial.run_id, owner)).status == "awaiting_answer"
    actions.extend([
        decision("request_clarification", {"kind": "object_mobility", "object_id": "chair_a"}),
        decision("request_clarification", {"kind": "room_dimensions", "object_id": None}),
        decision("finish"),
    ])
    state = await runs.answer(initial.run_id, owner, ClarificationAnswer(question_id="q1", choice_id="keep_fixed"))
    assert state.status == "completed" and len(state.questions) == 1
    assert all(item.outcome == "rejected" for item in state.tool_feedback[-3:-1])
    assert next(obj for obj in state.working_scene.objects if obj.id == "chair_a").locked
    assert len(calls) == 8


async def test_questions_are_not_asked_when_no_work_budget_remains(harness):
    _, _, calls, actions = harness
    runs, owner, uploaded = await prepared(harness)
    stages(actions)
    actions.append(decision("request_clarification", {"kind": "object_mobility", "object_id": "chair_a"}))
    initial = await runs.create(uploaded["input_id"], owner, limits=RunLimits(max_model_calls=5))
    state = await runs.execute(initial.run_id, owner)
    assert state.status == "budget_exhausted" and state.questions == []
    assert state.pending_question is None and len(calls) == 5


async def test_partial_measurement_answers_only_ask_about_the_remaining_unknown_axes(harness):
    _, client, calls, actions = harness
    _, _, _, paused = await paused_dimensions(harness)
    path = f"/api/agent-runs/{paused['run_id']}/answer"
    actions.append(decision("propose_scene"))
    response = await client.post(path, json={"question_id": "q1", "choice_id": "enter_measurements", "dimensions": {"length_m": 4}})
    assert response.status_code == 200, response.text
    state = response.json()
    assert state["status"] == "awaiting_answer" and state["pending_question"]["id"] == "q2"
    assert state["pending_question"]["dimension_axes"] == ["height_m"]
    assert state["confirmed_dimensions"]["length_m"] == 4
    assert state["confirmed_dimensions"]["height_m"] is None
    assert len(calls) == 4
    actions.extend([decision("propose_scene"), scene_action(), decision("finish")])
    complete = await client.post(path, json={"question_id": "q2", "choice_id": "enter_measurements", "dimensions": {"height_m": 2.6}})
    assert complete.status_code == 200 and complete.json()["status"] == "completed"
    assert complete.json()["analysis"] == paused["analysis"]
    assert len(calls) == 7


async def test_legacy_room_cookie_is_migrated_to_the_shared_api_scope(harness):
    _, client, calls, _ = harness
    runs, owner, _ = await prepared(harness)
    current = next(cookie for cookie in client.cookies.jar if cookie.name == COOKIE_NAME)
    client.cookies.delete(COOKIE_NAME, domain=current.domain, path="/api")
    client.cookies.set(COOKIE_NAME, current.value, domain=current.domain, path="/api/rooms")
    _, migrated_owner, uploaded = await prepared(harness)
    assert owner == migrated_owner
    assert [cookie.path for cookie in client.cookies.jar if cookie.name == COOKIE_NAME] == ["/api"]
    assert (await runs.create(uploaded["input_id"], migrated_owner)).input_id == uploaded["input_id"]
    assert not calls


async def test_default_run_timeout_leaves_room_for_the_scene_repair_deadline(harness):
    app, _, calls, _ = harness
    runs, owner, uploaded = await prepared(harness)
    state = await runs.create(uploaded["input_id"], owner)
    assert state.limits.step_timeout_seconds > 2 * app.state.settings.analysis_timeout_seconds
    assert not calls


async def test_request_id_discovery_is_read_only_owned_and_does_not_resume_a_question(harness):
    _, client, calls, _ = harness
    request_id = str(uuid4())
    assert (await client.get('/api/agent-runs', params={'request_id': request_id})).status_code == 404
    assert not calls
    _, _, request, paused = await paused_dimensions(harness)
    for _ in range(2):
        response = await client.get('/api/agent-runs', params={'request_id': request['request_id']})
        assert response.status_code == 200 and response.json() == paused
        assert response.headers['cache-control'] == 'no-store'
    foreign = {'cookie': f'{COOKIE_NAME}={"A"*43}'}
    assert (await client.get('/api/agent-runs', params={'request_id': request['request_id']}, headers=foreign)).status_code == 404
    assert len(calls) == 3
