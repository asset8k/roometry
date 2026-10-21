"""Real LangGraph execution/checkpoints with deterministic adapters; no paid calls."""

from datetime import timedelta
from pathlib import Path

import anyio
import pytest
from pydantic import ValidationError

from roometry.agents.checkpointing import RunBusy, RunCapacityReached, RunUnavailable, TemporaryCheckpoints
from roometry.agents.graph import build_graph
from roometry.agents.nodes import RunContext
from roometry.agents.runs import AgentRuns
from roometry.agents.state import AgentState, ClarificationQuestion, RunLimits, RunStep, RunUsage, append_events
from roometry.uploads.errors import UploadFailure
from roometry.uploads.schemas import PreparedImage, PreparedRoomInput
from roometry.vision.proposal_schemas import SceneProposalResponse
from roometry.uploads.session import COOKIE_NAME, owner_scope
from tests import test_vision_api
from tests.test_scene_proposals import source

ROOT = Path(__file__).resolve().parents[1]
OWNER = "private-owner-scope"
OTHER_ID = "44444444-4444-4444-8444-444444444444"
pytestmark = pytest.mark.anyio
harness = test_vision_api.harness
isolated_environment = test_vision_api.isolated_environment


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Room:
    def __init__(self):
        self.analysis = source()
        self.analysis.input_options.dimensions.height_m = 2.6
        self.now = self.analysis.created_at
        self.available = True
        self.prepared = PreparedRoomInput(
            input_id=self.analysis.input_id, status="ready", options=self.analysis.input_options,
            expires_at=self.analysis.expires_at,
            images=[PreparedImage(
                image_id=image_id, original_media_type="image/jpeg", original_bytes=1000,
                original_width_px=512, original_height_px=512, prepared_media_type="image/jpeg",
                prepared_bytes=1000, prepared_width_px=512, prepared_height_px=512,
            ) for image_id in self.analysis.observations.image_ids],
        )

    def resolve(self, input_id, owner):
        if not self.available or input_id != self.prepared.input_id or owner != OWNER:
            raise UploadFailure(404, "input_not_found", "Unavailable.")
        return self.prepared.model_copy(deep=True)

    def proposal(self, partial=False):
        name = "room_scene_partial.json" if partial else "room_scene_proposal.json"
        return SceneProposalResponse.model_validate_json((ROOT / "data/fixtures" / name).read_text())

    def runs(self, adapter, **kwargs):
        return AgentRuns(self.resolve, adapter, now=lambda: self.now, **kwargs)


def pipeline(room, partial=False):
    calls = []

    async def advance(state, context):
        calls.append(state.steps_used)
        if state.analysis is None:
            return RunStep(action="continue", analysis=room.analysis, usage=RunUsage(model_calls=1))
        return RunStep(action="finish", proposal=room.proposal(partial), usage=RunUsage(model_calls=1, tool_calls=1))

    return advance, calls


@pytest.mark.parametrize("partial", [False, True])
async def test_mocked_run_routes_to_an_honest_complete_or_partial_result(partial):
    room = Room()
    advance, calls = pipeline(room, partial)
    runs = room.runs(advance)
    initial = await runs.create(room.prepared.input_id, OWNER, limits=RunLimits(max_steps=2))
    result = await runs.execute(initial.run_id, OWNER)
    assert result.status == ("partial" if partial else "completed")
    assert calls == [1, 2] and result.steps_used == 2
    assert result.usage == RunUsage(model_calls=2, tool_calls=1)
    assert result.analysis == room.analysis and result.working_scene == result.proposal.scene
    assert result.base_scene == result.working_scene
    assert result.base_scene is not result.working_scene
    assert result.pending_question is None and not result.clarification_answers
    assert (await runs.read(initial.run_id, OWNER)) == result
    assert (await runs.execute(initial.run_id, OWNER)) == result
    assert calls == [1, 2]  # Completed checkpoints never re-run the adapter.
    assert result.events[-1]["status"] == result.status


@pytest.mark.parametrize("budget", ["steps", "model", "tool"])
async def test_non_finishing_adapter_stops_at_the_configured_budget(budget):
    room, calls = Room(), []

    async def advance(state, context):
        calls.append(state.steps_used)
        return RunStep(action="continue", usage=RunUsage(model_calls=int(budget == "model"), tool_calls=int(budget == "tool")))

    runs = room.runs(advance)
    initial = await runs.create(room.prepared.input_id, OWNER, limits=RunLimits(max_steps=3, max_model_calls=2, max_tool_calls=2))
    result = await runs.execute(initial.run_id, OWNER)
    assert result.status == "budget_exhausted"
    assert len(calls) == (3 if budget == "steps" else 2)
    assert result.working_scene is None
    assert (await runs.execute(initial.run_id, OWNER)) == result


async def test_finish_without_a_valid_proposal_is_incomplete():
    room = Room()

    async def advance(state, context):
        return RunStep(action="finish")

    runs = room.runs(advance)
    initial = await runs.create(room.prepared.input_id, OWNER)
    result = await runs.execute(initial.run_id, OWNER)
    assert result.status == "incomplete" and result.working_scene is None


@pytest.mark.parametrize("failure,code", [(RuntimeError, "step_failed"), (ValueError, "invalid_step")])
async def test_step_failure_stops_and_never_exposes_private_exception_text(failure, code):
    room, calls = Room(), []

    async def advance(state, context):
        calls.append(1)
        raise failure("PRIVATE_PROVIDER_DETAILS")

    runs = room.runs(advance)
    initial = await runs.create(room.prepared.input_id, OWNER)
    result = await runs.execute(initial.run_id, OWNER)
    assert result.status == "failed" and result.last_error == code
    assert "PRIVATE_PROVIDER_DETAILS" not in result.model_dump_json()
    await runs.execute(initial.run_id, OWNER)
    assert len(calls) == 1


async def test_step_timeout_is_bounded_and_does_not_automatically_retry():
    room = Room()

    async def advance(state, context):
        await anyio.sleep(1)
        return RunStep(action="finish")

    runs = room.runs(advance)
    initial = await runs.create(room.prepared.input_id, OWNER, limits=RunLimits(step_timeout_seconds=.01))
    result = await runs.execute(initial.run_id, OWNER)
    assert result.status == "failed" and result.last_error == "step_timeout"
    assert result.steps_used == 1


@pytest.mark.parametrize("defect", ["foreign_analysis", "changed_observations", "foreign_proposal", "confirmed_size", "geometry", "missing_entity", "expiry", "invented_confirmation"])
async def test_adapter_results_preserve_input_identity_measurements_and_scene_validity(defect):
    room = Room()
    if defect == "invented_confirmation":
        room.analysis.input_options.dimensions.height_m = None
        room.prepared.options.dimensions.height_m = None

    async def advance(state, context):
        if state.analysis is None:
            analysis = room.analysis.model_copy(deep=True)
            if defect == "foreign_analysis":
                analysis.input_id = OTHER_ID
            if defect == "expiry":
                analysis.expires_at += timedelta(hours=1)
            return RunStep(action="continue", analysis=analysis)
        proposal = room.proposal()
        if defect == "changed_observations":
            changed = room.analysis.model_copy(deep=True)
            changed.observations.objects[0].label = "Invented new interpretation"
            return RunStep(action="finish", analysis=changed)
        if defect == "foreign_proposal":
            proposal.analysis_id = OTHER_ID
        if defect == "confirmed_size":
            proposal.dimensions.width_m = proposal.scene.room.width_m = 5
        if defect == "geometry":
            proposal.scene.objects[0].position.x = 20
        if defect == "missing_entity":
            proposal.scene.objects.pop(0)
        return RunStep(action="finish", proposal=proposal)

    runs = room.runs(advance)
    initial = await runs.create(room.prepared.input_id, OWNER)
    result = await runs.execute(initial.run_id, OWNER)
    assert result.status == "failed" and result.last_error == "invalid_step"
    assert result.working_scene is None


async def test_checkpoint_continuation_does_not_replay_a_completed_analysis_step():
    room = Room()
    advance, calls = pipeline(room)
    runs = room.runs(advance)
    initial = await runs.create(room.prepared.input_id, OWNER)
    entry = runs.checkpoints.get(initial.run_id, OWNER, room.now)

    async def authorize():
        await runs._owned(initial.run_id, OWNER)

    context = RunContext(advance, authorize, lambda: False, lambda: room.now)
    graph = build_graph(entry.saver, interrupt_after=["advance"])
    await graph.ainvoke(initial.model_dump(), runs._config(entry), context=context)
    checkpoint = await runs.read(initial.run_id, OWNER)
    assert checkpoint.analysis == room.analysis and checkpoint.steps_used == 1
    assert calls == [1]
    # Rebuild around the same saver and continue its pending edge, not fresh input.
    result = await runs.execute(initial.run_id, OWNER)
    assert result.status == "completed" and calls == [1, 2]


async def test_session_scope_applies_to_read_execute_cancel_and_unknown_ids():
    room = Room()
    advance, calls = pipeline(room)
    runs = room.runs(advance)
    initial = await runs.create(room.prepared.input_id, OWNER)
    for operation in (runs.read, runs.execute, runs.cancel):
        with pytest.raises(RunUnavailable):
            await operation(initial.run_id, "another-session")
        with pytest.raises(RunUnavailable):
            await operation(OTHER_ID, OWNER)
    assert not calls
    assert (await runs.execute(initial.run_id, OWNER)).status == "completed"


async def test_caller_mutations_never_change_checkpoint_state_or_original_observations():
    room = Room()
    advance, _ = pipeline(room)
    runs = room.runs(advance)
    initial = await runs.create(room.prepared.input_id, OWNER)
    initial.input_options.description = "PRIVATE_MUTATION"
    assert (await runs.read(initial.run_id, OWNER)).input_options == room.analysis.input_options
    result = await runs.execute(initial.run_id, OWNER)
    result.working_scene.objects.clear()
    result.analysis.observations.objects.clear()
    restored = await runs.read(initial.run_id, OWNER)
    assert len(restored.working_scene.objects) == len(restored.analysis.observations.objects) == 5
    assert OWNER not in restored.model_dump_json()
    assert "PRIVATE_MUTATION" not in restored.model_dump_json()


async def test_expiry_prunes_checkpoints_and_does_not_outlive_the_upload():
    room = Room()
    advance, _ = pipeline(room)
    runs = room.runs(advance, ttl_seconds=10, checkpoints=TemporaryCheckpoints(max_runs=1))
    initial = await runs.create(room.prepared.input_id, OWNER)
    assert initial.expires_at == room.now + timedelta(seconds=10)
    with pytest.raises(RunCapacityReached):
        await runs.create(room.prepared.input_id, OWNER)
    room.now += timedelta(seconds=11)
    await runs.cleanup()
    with pytest.raises(RunUnavailable):
        await runs.read(initial.run_id, OWNER)
    next_run = await runs.create(room.prepared.input_id, OWNER)
    assert next_run.run_id != initial.run_id
    room.prepared.expires_at = room.now + timedelta(seconds=2)
    other = room.runs(advance)
    assert (await other.create(room.prepared.input_id, OWNER)).expires_at == room.prepared.expires_at


async def test_cleanup_and_read_revoke_runs_when_the_underlying_upload_is_deleted():
    room = Room()
    advance, calls = pipeline(room)
    runs = room.runs(advance)
    initial = await runs.create(room.prepared.input_id, OWNER)
    room.available = False
    await runs.cleanup()
    assert not runs.checkpoints._runs
    with pytest.raises(RunUnavailable):
        await runs.execute(initial.run_id, OWNER)
    assert not calls


@pytest.mark.parametrize("action", ["cancel", "revoke", "busy"])
async def test_cancellation_revocation_and_duplicate_execution_while_a_step_is_running(action):
    room = Room()
    entered, release = anyio.Event(), anyio.Event()
    results = []

    async def advance(state, context):
        entered.set()
        await release.wait()
        return RunStep(action="finish", analysis=room.analysis, proposal=room.proposal())

    runs = room.runs(advance)
    initial = await runs.create(room.prepared.input_id, OWNER)

    async def execute():
        try:
            results.append(await runs.execute(initial.run_id, OWNER))
        except RunUnavailable:
            results.append(None)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(execute)
        await entered.wait()
        if action == "cancel":
            await runs.cancel(initial.run_id, OWNER)
        elif action == "revoke":
            room.available = False
            await runs.cleanup()
        else:
            with pytest.raises(RunBusy):
                await runs.execute(initial.run_id, OWNER)
        release.set()
    if action == "revoke":
        assert results == [None] and not runs.checkpoints._runs
    else:
        result = results[0]
        assert result.status == ("cancelled" if action == "cancel" else "completed")
        if action == "cancel":
            assert result.working_scene is None


async def test_cancel_before_execution_never_calls_the_adapter():
    room = Room()
    advance, calls = pipeline(room)
    runs = room.runs(advance)
    initial = await runs.create(room.prepared.input_id, OWNER)
    assert (await runs.cancel(initial.run_id, OWNER)).status == "cancelled"
    assert not calls


async def test_aborted_execution_is_not_silently_replayed_on_a_later_execute():
    room, calls = Room(), []
    entered = anyio.Event()

    async def advance(state, context):
        calls.append(1)
        entered.set()
        await anyio.sleep_forever()

    runs = room.runs(advance)
    initial = await runs.create(room.prepared.input_id, OWNER)
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(runs.execute, initial.run_id, OWNER)
        await entered.wait()
        tasks.cancel_scope.cancel()
    assert (await runs.execute(initial.run_id, OWNER)).status == "cancelled"
    assert calls == [1]


async def test_real_upload_resolver_enforces_owner_and_deletion_without_any_model_calls(harness):
    app, client, calls, _ = harness
    uploaded = await test_vision_api.upload(client)
    owner = owner_scope(client.cookies[COOKIE_NAME])

    async def advance(state, context):
        return RunStep(action="finish")

    runs = AgentRuns(app.state.upload_service.storage.resolve, advance)
    with pytest.raises(RunUnavailable):
        await runs.create(uploaded["input_id"], "wrong-session")
    initial = await runs.create(uploaded["input_id"], owner)
    assert initial.image_ids == [image["image_id"] for image in uploaded["images"]]
    assert (await client.delete(f"/api/rooms/inputs/{uploaded['input_id']}")).status_code == 204
    with pytest.raises(RunUnavailable):
        await runs.read(initial.run_id, owner)
    assert not calls


async def test_state_schema_excludes_runtime_objects_bytes_and_untrusted_limits():
    room = Room()
    advance, _ = pipeline(room)
    state = await room.runs(advance).create(room.prepared.input_id, OWNER)
    for patch in ({"image_bytes": b"private"}, {"owner": OWNER}, {"client": object()}, {"image_ids": ["data:image/jpeg;base64,private"]}):
        with pytest.raises(ValidationError):
            AgentState.model_validate({**state.model_dump(), **patch})
    for patch in ({"max_steps": 0}, {"step_timeout_seconds": float("inf")}, {"max_tool_calls": True}):
        with pytest.raises(ValidationError):
            RunLimits.model_validate(patch)
    with pytest.raises(ValidationError):
        RunStep.model_validate({"action": "finish", "steps_used": 0})
    with pytest.raises(ValidationError):
        ClarificationQuestion.model_validate({"id": "q1", "question": "Where is the TV?", "choices": [
            {"id": "left", "label": "Left wall"}, {"id": "left", "label": "Right wall"}, {"id": "unknown", "label": "Not sure"},
        ]})
    events = [{"step": i, "status": "running"} for i in range(64)]
    merged = append_events(events, [{"step": 64, "status": "completed"}])
    assert len(merged) == 64 and merged[0]["step"] == 1 and len(events) == 64
