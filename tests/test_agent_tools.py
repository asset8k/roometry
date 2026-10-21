"""Real SDK, uploads, graph, scene services and edits over mocked HTTP only."""

import json
from io import BytesIO
from math import pi

import anyio
import httpx
import pytest
from PIL import Image

from roometry.agents.checkpointing import RunUnavailable
from roometry.agents.state import RunLimits
from roometry.agents.tools import ARGUMENT_MODELS, tool_definitions
from roometry.scenes.validation import validate_geometry
from roometry.uploads.session import COOKIE_NAME, owner_scope
from tests import test_vision_api
from tests.test_scene_proposals import draft_data, output_limited_scene, scene_action

anyio_backend = test_vision_api.anyio_backend
isolated_environment = test_vision_api.isolated_environment
harness = test_vision_api.harness
pytestmark = pytest.mark.anyio


def decision(name, arguments=None, *, raw=None, extra_call=False, status="completed", refusal=False):
    async def action(request, payload):
        assert payload["tool_choice"] == "required"
        assert payload["parallel_tool_calls"] is False and payload["store"] is False
        assert payload["truncation"] == "disabled"
        assert all(tool["strict"] for tool in payload["tools"])
        call = {"id": "fc_test", "type": "function_call", "call_id": "call_test", "name": name,
                "arguments": raw if raw is not None else json.dumps(arguments or {}), "status": "completed"}
        output = [call, {**call, "id": "fc_other", "call_id": "call_other"}] if extra_call else [call]
        if refusal:
            output = [{"id": "msg_test", "type": "message", "role": "assistant", "status": "completed",
                       "content": [{"type": "refusal", "refusal": "PRIVATE_REFUSAL"}]}]
        return httpx.Response(200, json={
            "id": "resp_decision", "object": "response", "created_at": 1792404000,
            "status": status, "model": payload["model"], "output": output,
            "usage": {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150,
                      "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 10}},
        })
    return action


async def photo(request, payload):
    return httpx.Response(200, json=test_vision_api.provider_response(payload))


async def prepared(harness, *, complete=True):
    app, client, _, _ = harness
    buffer = BytesIO()
    with Image.new("RGB", (256, 256), "green") as image:
        image.save(buffer, "PNG")
    dimensions = {"width_m": 4, "measurements_source": "user_confirmed"}
    if complete:
        dimensions.update(length_m=4, height_m=2.6)
    files = [("images", ("room.png", buffer.getvalue(), "image/png")), ("options", (None, json.dumps({
        "room_type": "bedroom", "goals": ["better_aesthetics"], "dimensions": dimensions,
    })))]
    response = await client.post("/api/rooms/inputs", files=files)
    assert response.status_code == 201, response.text
    owner = owner_scope(client.cookies[COOKIE_NAME])
    return app.state.agent_runs, owner, response.json()


def stages(actions, draft=None):
    actions.extend([decision("analyze_photos"), photo, decision("propose_scene"), scene_action(draft)])


def context(payload):
    return json.loads(payload["input"][0]["content"][0]["text"])


async def test_model_observes_collision_feedback_and_revises_without_changing_the_base(harness):
    app, _, calls, actions = harness
    runs, owner, uploaded = await prepared(harness)
    stages(actions)
    actions.extend([
        decision("move_object", {"object_id": "chair_a", "position": {"x": 3., "z": .6}}),
        decision("move_object", {"object_id": "chair_a", "position": {"x": 2.4, "z": 2.4}}),
        decision("finish"),
    ])
    initial = await runs.create(uploaded["input_id"], owner)
    result = await runs.execute(initial.run_id, owner)
    assert result.status == "completed", result.model_dump_json()
    assert result.steps_used == 5 and result.usage.model_calls == len(calls) == 7
    assert result.usage.tool_calls == 5 and result.usage.unknown_usage_calls == 0
    assert result.usage.input_tokens == 3000 and result.usage.output_tokens == 1750
    base = next(obj for obj in result.base_scene.objects if obj.id == "chair_a")
    working = next(obj for obj in result.working_scene.objects if obj.id == "chair_a")
    assert base.position.z == 2.0 and working.position.z == 2.4
    assert validate_geometry(result.working_scene).valid
    correction = context(calls[-2])
    assert correction["tool_feedback"][-1]["outcome"] == "rejected"
    assert any(error["code"] == "object_overlap" for error in correction["tool_feedback"][-1]["details"]["geometry"]["errors"])
    assert next(obj for obj in correction["current_scene"]["objects"] if obj["id"] == "chair_a")["position"]["z"] == 2
    assert [item.stage for item in app.state.analysis_service.usage_records] == ["analysis", "scene_proposal"]
    assert (await runs.execute(initial.run_id, owner)) == result and len(calls) == 7


async def test_catalog_read_rotate_and_validate_tools_use_the_existing_catalog_and_edit_rules(harness):
    _, _, calls, actions = harness
    runs, owner, uploaded = await prepared(harness)
    stages(actions)
    actions.extend([
        decision("search_catalog", {"object_type": "picture", "placement": "surface", "limit": 8}),
        decision("read_scene"), decision("rotate_object", {"object_id": "chair_a", "rotation_y_rad": pi / 2}),
        decision("validate_scene"), decision("finish"),
    ])
    initial = await runs.create(uploaded["input_id"], owner, limits=RunLimits(max_steps=10, max_model_calls=16))
    result = await runs.execute(initial.run_id, owner)
    assert result.status == "completed" and result.usage.tool_calls == 7
    catalog = next(item for item in result.tool_feedback if item.name == "search_catalog").details
    assert catalog["models"] and all("surface" in model["configurations"] for model in catalog["models"])
    assert "picture_triptych_01" not in {model["asset_id"] for model in catalog["models"]}
    assert next(obj for obj in result.working_scene.objects if obj.id == "chair_a").rotation_y_rad == pi / 2
    assert result.tool_feedback[-2].details["valid"] is True
    assert result.usage.model_calls == len(calls) == 9


@pytest.mark.parametrize("bad", ["unknown", "extra_authority", "duplicate_key", "nonfinite", "parallel", "wrong_phase"])
async def test_untrusted_function_calls_never_dispatch_unauthorized_tools(harness, bad):
    app, _, calls, actions = harness
    runs, owner, uploaded = await prepared(harness)
    if bad == "unknown":
        actions.append(decision("run_shell", {"cmd": "PRIVATE_COMMAND"}))
    elif bad == "extra_authority":
        actions.append(decision("analyze_photos", {"input_id": "foreign-room", "image_urls": ["https://untrusted.example"]}))
    elif bad == "duplicate_key":
        actions.append(decision("analyze_photos", raw='{"input_id":"foreign","input_id":"current"}'))
    elif bad == "nonfinite":
        actions.append(decision("analyze_photos", raw='{"x":NaN}'))
    elif bad == "parallel":
        actions.append(decision("analyze_photos", extra_call=True))
    else:
        actions.append(decision("move_object", {"object_id": "chair_a", "position": {"x": 2, "z": 2}}))
    actions.append(decision("finish"))
    initial = await runs.create(uploaded["input_id"], owner)
    result = await runs.execute(initial.run_id, owner)
    assert result.status == "incomplete" and result.analysis is None
    assert result.tool_feedback[0].outcome == "rejected"
    assert result.usage.model_calls == len(calls) == 2 and result.usage.tool_calls == 1
    assert not app.state.analysis_service.usage_records
    assert "PRIVATE_COMMAND" not in result.model_dump_json()


@pytest.mark.parametrize("failure", ["refusal", "incomplete", "timeout", "rate_limit"])
async def test_decision_failures_stop_with_actual_usage_and_no_automatic_retry(harness, failure):
    _, _, calls, actions = harness
    runs, owner, uploaded = await prepared(harness)
    if failure in {"refusal", "incomplete"}:
        actions.append(decision("finish", refusal=failure == "refusal", status="incomplete" if failure == "incomplete" else "completed"))
    else:
        async def failed(request, payload):
            if failure == "timeout":
                raise httpx.ReadTimeout("PRIVATE_TIMEOUT", request=request)
            return httpx.Response(429, json={"error": {"message": "PRIVATE_RATE_LIMIT"}})
        actions.append(failed)
    initial = await runs.create(uploaded["input_id"], owner)
    result = await runs.execute(initial.run_id, owner)
    assert result.status == "failed" and result.last_error == "decision_failed"
    assert result.usage.model_calls == len(calls) == 1 and result.usage.tool_calls == 0
    assert result.usage.unknown_usage_calls == int(failure in {"timeout", "rate_limit"})
    assert "PRIVATE" not in result.model_dump_json()


async def test_analysis_retry_and_scene_repair_are_counted_inside_the_same_run(harness):
    _, _, calls, actions = harness
    runs, owner, uploaded = await prepared(harness)

    async def overloaded(request, payload):
        return httpx.Response(503, json={"error": {"message": "PRIVATE_OVERLOAD"}})

    bad = draft_data()
    bad["objects"][0]["position"]["x"] = -10
    actions.extend([decision("analyze_photos"), overloaded, photo, decision("propose_scene"), scene_action(bad), scene_action(), decision("finish")])
    initial = await runs.create(uploaded["input_id"], owner)
    result = await runs.execute(initial.run_id, owner)
    assert result.status == "completed" and result.usage.model_calls == len(calls) == 7
    assert result.usage.unknown_usage_calls == 1
    assert result.usage.tool_calls == 3


async def test_model_budget_denies_a_nested_repair_and_keeps_the_valid_partial(harness):
    _, _, calls, actions = harness
    runs, owner, uploaded = await prepared(harness)
    bad = draft_data()
    bad["objects"][0]["position"]["x"] = -10
    stages(actions, bad)
    initial = await runs.create(uploaded["input_id"], owner, limits=RunLimits(max_model_calls=4))
    result = await runs.execute(initial.run_id, owner)
    assert result.status == "budget_exhausted" and len(calls) == result.usage.model_calls == 4
    assert result.proposal.status == "partial" and len(result.working_scene.objects) == 4
    assert result.proposal.unresolved_entities[0].entity_id == "bed_observed"
    assert validate_geometry(result.working_scene).valid


async def test_analysis_budget_cannot_be_expanded_by_a_hidden_retry(harness):
    _, _, calls, actions = harness
    runs, owner, uploaded = await prepared(harness)

    async def overloaded(request, payload):
        return httpx.Response(503, json={"error": {"message": "PRIVATE_OVERLOAD"}})

    actions.extend([decision("analyze_photos"), overloaded])
    initial = await runs.create(uploaded["input_id"], owner, limits=RunLimits(max_model_calls=2))
    result = await runs.execute(initial.run_id, owner)
    assert result.status == "budget_exhausted" and len(calls) == result.usage.model_calls == 2
    assert result.analysis is None and result.usage.unknown_usage_calls == 1


async def test_missing_dimensions_pause_without_repeating_analysis(harness):
    _, _, calls, actions = harness
    runs, owner, uploaded = await prepared(harness, complete=False)
    actions.extend([decision("analyze_photos"), photo, decision("propose_scene")])
    initial = await runs.create(uploaded["input_id"], owner)
    result = await runs.execute(initial.run_id, owner)
    assert result.status == "awaiting_answer" and result.proposal.status == "needs_dimensions"
    assert result.pending_question.dimension_axes == ["length_m", "height_m"]
    assert result.analysis is not None and result.working_scene is None
    assert result.usage.model_calls == len(calls) == 3


async def test_partial_proposals_remain_partial_after_agent_finishes(harness):
    _, _, calls, actions = harness
    runs, owner, uploaded = await prepared(harness)
    bad = draft_data()
    bad["objects"][0]["position"]["x"] = -10
    stages(actions, bad)
    actions.extend([output_limited_scene(), decision("finish")])
    initial = await runs.create(uploaded["input_id"], owner)
    result = await runs.execute(initial.run_id, owner)
    assert result.status == "partial" and result.proposal.unresolved_entities
    assert result.usage.model_calls == len(calls) == 6


async def test_disabled_configuration_and_request_size_fail_before_a_paid_call(harness):
    app, _, calls, _ = harness
    runs, owner, uploaded = await prepared(harness)
    app.state.settings.analysis_enabled = False
    initial = await runs.create(uploaded["input_id"], owner)
    assert (await runs.execute(initial.run_id, owner)).status == "failed"
    assert not calls
    app.state.settings.analysis_enabled = True
    app.state.settings.agent_max_request_bytes = 4096
    other = await runs.create(uploaded["input_id"], owner)
    assert (await runs.execute(other.run_id, owner)).tool_feedback[-1].details["code"] == "request_too_large"
    assert not calls


async def test_revocation_during_a_decision_prevents_its_tool_execution(harness):
    _, client, calls, actions = harness
    runs, owner, uploaded = await prepared(harness)

    async def revoked(request, payload):
        assert (await client.delete(f"/api/rooms/inputs/{uploaded['input_id']}")).status_code == 204
        return await decision("analyze_photos")(request, payload)

    actions.append(revoked)
    initial = await runs.create(uploaded["input_id"], owner)
    with pytest.raises(RunUnavailable):
        await runs.execute(initial.run_id, owner)
    assert len(calls) == 1


@pytest.mark.parametrize("abort", [False, True])
async def test_inflight_cancellation_retains_usage_and_never_replays_the_decision(harness, abort):
    _, _, calls, actions = harness
    runs, owner, uploaded = await prepared(harness)
    entered, release = anyio.Event(), anyio.Event()

    async def waiting(request, payload):
        entered.set()
        await release.wait()
        return await decision("analyze_photos")(request, payload)

    actions.append(waiting)
    initial = await runs.create(uploaded["input_id"], owner)
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(runs.execute, initial.run_id, owner)
        await entered.wait()
        if abort:
            tasks.cancel_scope.cancel()
        else:
            await runs.cancel(initial.run_id, owner)
            release.set()
    result = await runs.execute(initial.run_id, owner)
    assert result.status == "cancelled" and result.analysis is None
    assert result.usage.model_calls == len(calls) == 1
    assert result.usage.unknown_usage_calls == int(abort)
    assert result.usage.input_tokens == (0 if abort else 120)
    assert result.usage.tool_calls == 0 and result.pending_tool is None


async def test_step_timeout_keeps_the_inflight_call_reserved(harness):
    _, _, calls, actions = harness
    runs, owner, uploaded = await prepared(harness)

    async def waiting(request, payload):
        await anyio.sleep_forever()

    actions.append(waiting)
    initial = await runs.create(uploaded["input_id"], owner, limits=RunLimits(step_timeout_seconds=.1))
    result = await runs.execute(initial.run_id, owner)
    assert result.status == "failed" and result.last_error == "step_timeout"
    assert result.usage.model_calls == len(calls) == 1
    assert result.usage.unknown_usage_calls == 1
    assert (await runs.execute(initial.run_id, owner)) == result


async def test_concurrent_runs_keep_provider_usage_in_their_own_context(harness):
    _, _, calls, actions = harness
    runs, owner, uploaded = await prepared(harness)
    both_entered = anyio.Event()
    entered = 0
    results = []

    async def waiting(request, payload):
        nonlocal entered
        entered += 1
        if entered == 2:
            both_entered.set()
        await both_entered.wait()
        return await decision("finish")(request, payload)

    actions.extend([waiting, waiting])
    first = await runs.create(uploaded["input_id"], owner)
    second = await runs.create(uploaded["input_id"], owner)

    async def execute(run_id):
        results.append(await runs.execute(run_id, owner))

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(execute, first.run_id)
        tasks.start_soon(execute, second.run_id)
    assert len(results) == len(calls) == 2
    assert all(result.usage.model_calls == result.usage.tool_calls == 1 for result in results)
    assert all(result.usage.input_tokens == 120 and result.usage.output_tokens == 30 for result in results)


async def test_support_edits_carry_dependents_and_fixed_objects_are_rejected(harness):
    _, _, calls, actions = harness
    runs, owner, uploaded = await prepared(harness)

    async def fixed_bed(request, payload):
        body = test_vision_api.provider_response(payload)
        part = body["output"][0]["content"][0]
        observations = json.loads(part["text"])
        observations["objects"][0]["appears_fixed"] = True
        part["text"] = json.dumps(observations)
        return httpx.Response(200, json=body)

    stages(actions)
    actions[1] = fixed_bed
    actions.extend([
        decision("move_object", {"object_id": "bed_observed", "position": {"x": .8, "z": 2.4}}),
        decision("move_object", {"object_id": "desk_observed", "position": {"x": 3., "z": .9}}),
        decision("finish"),
    ])
    initial = await runs.create(uploaded["input_id"], owner)
    result = await runs.execute(initial.run_id, owner)
    assert result.status == "completed" and len(calls) == 7
    rejected = next(item for item in result.tool_feedback if item.outcome == "rejected")
    assert rejected.details["edit_errors"][0]["code"] == "object_fixed"
    base = {obj.id: obj for obj in result.base_scene.objects}
    current = {obj.id: obj for obj in result.working_scene.objects}
    assert base["bed_observed"] == current["bed_observed"]
    assert current["computer_observed"].position.z - base["computer_observed"].position.z == pytest.approx(.3)
    assert current["desk_observed"].position.z == .9
    assert validate_geometry(result.working_scene).valid


async def test_tool_schemas_are_strict_and_derived_from_current_state(harness):
    _, _, _, _ = harness
    runs, owner, uploaded = await prepared(harness)
    state = await runs.create(uploaded["input_id"], owner)
    definitions = tool_definitions(state)
    assert {tool["name"] for tool in definitions} == {"analyze_photos", "search_catalog", "finish"}

    def check(schema):
        if schema.get("type") == "object":
            assert schema["additionalProperties"] is False
            assert set(schema.get("required", [])) == set(schema.get("properties", {}))
        for value in schema.values():
            if isinstance(value, dict):
                check(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        check(item)

    for arguments in ARGUMENT_MODELS.values():
        check(arguments.model_json_schema())
