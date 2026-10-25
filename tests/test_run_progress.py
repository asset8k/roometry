"""Actual graph transitions and private read-only SSE; every provider is mocked."""
import json
from uuid import uuid4

import anyio
import httpx
import pytest

from roometry.agents.router import RunFailure
from roometry.progress import ProgressLog, progress_response
from roometry.uploads.session import COOKIE_NAME
from tests import test_vision_api as api
from tests.test_agent_graph import Room, OWNER, OTHER_ID, pipeline
from tests.test_optimization_runs import reconstructed, request as optimization_request
from tests.test_optimization_execution import prepared_execution, provider_action, proposal, review

pytestmark = pytest.mark.anyio
anyio_backend = api.anyio_backend
isolated_environment = api.isolated_environment
harness = api.harness


class Connected:
    async def is_disconnected(self): return False


def messages(text):
    found = []
    for chunk in text.split('\n\n'):
        fields = dict(line.split(': ', 1) for line in chunk.splitlines() if ': ' in line)
        if fields.get('event') in {'snapshot', 'progress'}:
            found.append((fields['event'], fields['id'], json.loads(fields['data'])))
    return found


async def collect(response):
    return ''.join([chunk async for chunk in response.body_iterator])


async def test_subscription_observes_inflight_work_and_disconnect_does_not_cancel_or_reexecute():
    room = Room()
    adapter, calls = pipeline(room)
    entered, release = anyio.Event(), anyio.Event()
    async def blocked(state, context):
        entered.set()
        await release.wait()
        return await adapter(state, context)
    runs = room.runs(blocked)
    initial = await runs.create(room.prepared.input_id, OWNER)
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(runs.execute, initial.run_id, OWNER)
        await entered.wait()
        _, log = await runs.observe(initial.run_id, OWNER)
        response = progress_response(Connected(), log, lambda: runs.read(initial.run_id, OWNER), recheck_seconds=.001)
        iterator = response.body_iterator
        assert 'retry:' in await anext(iterator)
        current = messages(await anext(iterator))[0][2]
        assert current['status'] == 'running' and not current['closed']
        assert not calls and log.subscribers == 1
        await iterator.aclose()
        assert log.subscribers == 0
        entry = runs.checkpoints.get(initial.run_id, OWNER, room.now)
        assert entry.busy and not entry.cancel_requested
        release.set()
    count = len(calls)
    state, log = await runs.observe(initial.run_id, OWNER)
    assert state.status == 'completed'
    for _ in range(2):
        text = await collect(progress_response(Connected(), log, lambda: runs.read(initial.run_id, OWNER)))
        assert messages(text)[-1][2]['closed']
    assert len(calls) == count and log.subscribers == 0


async def test_log_replays_ordered_ids_and_resyncs_missing_or_foreign_cursors():
    room = Room()
    runs = room.runs(pipeline(room)[0])
    initial = await runs.create(room.prepared.input_id, OWNER)
    log = ProgressLog(capacity=3)
    for step in range(7): log.publish(initial.model_copy(update={'status':'running','steps_used':step}))
    assert len(log.events) == 3 and [e.sequence for e in log.events] == [5, 6, 7]
    assert [e.sequence for e in log.replay(f'{initial.run_id}:5')] == [6, 7]
    for cursor in [None, 'invalid', f'{OTHER_ID}:5', f'{initial.run_id}:1', f'{initial.run_id}:9999999999999999999999']:
        assert log.replay(cursor) == []
    # A cursor miss does not expose old data or replay graph work: always reconcile.
    room.available = False
    async def revoked(): return await runs.read(initial.run_id, OWNER)
    text = await collect(progress_response(Connected(), log, revoked, cursor=f'{initial.run_id}:1', recheck_seconds=.001))
    assert messages(text)[0][0] == 'snapshot'
    assert 'event: unavailable' in text and log.subscribers == 0


async def test_keepalive_and_connection_capacity_are_bounded_without_execution():
    room = Room()
    async def forbidden(*args): raise AssertionError('SSE must never execute')
    runs = room.runs(forbidden)
    initial = await runs.create(room.prepared.input_id, OWNER)
    _, log = await runs.observe(initial.run_id, OWNER)
    read = lambda: runs.read(initial.run_id, OWNER)
    responses = [progress_response(Connected(), log, read) for _ in range(4)]
    with pytest.raises(RunFailure) as error: progress_response(Connected(), log, read)
    assert error.value.status == 429
    for response in responses: await response.background()
    assert log.subscribers == 0
    text = await collect(progress_response(Connected(), log, read, recheck_seconds=.001, heartbeat_seconds=.001, lifetime_seconds=.006))
    assert 'event: heartbeat' in text and log.subscribers == 0


async def test_http_progress_is_owned_ordered_private_and_never_adds_provider_calls(harness):
    app, client, calls, _ = harness
    _, owner, source = await reconstructed(harness)
    count = len(calls)
    url = f'/api/agent-runs/{source.run_id}/events'
    state, log = await app.state.agent_runs.observe(source.run_id, owner)
    cursor = log.events[-2].cursor
    response = await client.get(url, headers={'Last-Event-ID':cursor})
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/event-stream')
    assert response.headers['cache-control'] == 'no-store, no-transform'
    assert response.headers['x-accel-buffering'] == 'no'
    events = messages(response.text)
    assert events[-1][0] == 'snapshot' and events[-1][2]['closed']
    assert [e[2]['sequence'] for e in events] == sorted(e[2]['sequence'] for e in events)
    allowed = {'version','run_id','sequence','kind','stage','status','step','candidate','closed'}
    assert all(set(event) == allowed for _, _, event in events)
    for private in ['arguments', 'input_options', 'tool_feedback', 'PRIVATE_', 'image_ids', 'analysis_id']:
        assert private not in response.text
    assert len(calls) == count and await app.state.agent_runs.read(source.run_id, owner) == state
    assert (await client.get(url, headers={'Last-Event-ID':'x'*81})).status_code == 422
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as foreign:
        assert (await foreign.get(url)).status_code == 404
    app.state.agent_runs.now = lambda: source.expires_at
    assert (await client.get(url)).status_code == 404
    assert len(calls) == count


async def test_improvement_events_reject_wrong_parent_and_stream_cancelled_without_executing(harness):
    app, client, calls, _ = harness
    _, _, source = await reconstructed(harness)
    base = f'/api/agent-runs/{source.run_id}/optimizations'
    child = (await client.post(base, json=optimization_request())).json()
    count = len(calls)
    wrong = f'/api/agent-runs/{OTHER_ID}/optimizations/{child["run_id"]}/events'
    assert (await client.get(wrong)).status_code == 404
    await client.post(f'{base}/{child["run_id"]}/cancel', json={})
    response = await client.get(f'{base}/{child["run_id"]}/events')
    assert response.status_code == 200
    event = messages(response.text)[-1][2]
    assert event['kind'] == 'optimization' and event['status'] == 'cancelled' and event['closed']
    client.cookies.set(COOKIE_NAME, 'b'*43)
    assert (await client.get(f'{base}/{child["run_id"]}/events')).status_code == 404
    assert len(calls) == count


async def test_real_optimization_graph_emits_each_stage_and_retry_only_replays_progress(harness):
    _, client, calls, actions = harness
    service, owner, source, child = await prepared_execution(harness)
    actions.extend([provider_action(proposal()), review()])
    result = await service.execute(child.run_id, owner)
    assert result.status == 'completed'
    _, log = await service.observe(child.run_id, owner)
    stages = [event.stage for event in log.events]
    assert all(stage in stages for stage in ['prepared','proposing','checking','reviewing','finished'])
    count = len(calls)
    url = f'/api/agent-runs/{source.run_id}/optimizations/{child.run_id}/events'
    for _ in range(2):
        response = await client.get(url, headers={'Last-Event-ID': log.events[0].cursor})
        assert messages(response.text)[-1][2]['status'] == 'completed'
    assert len(calls) == count
