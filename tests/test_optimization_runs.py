"""User-triggered improvement preparation against owned reconstructed snapshots."""

from datetime import timedelta
from uuid import uuid4

import anyio
import httpx
import pytest

from roometry.agents.checkpointing import RunUnavailable
from roometry.optimization.runs import OptimizationRuns
from roometry.optimization.schemas import StartOptimizationRequest
from tests import test_vision_api as api
from tests.test_agent_tools import decision, prepared, stages

anyio_backend = api.anyio_backend
isolated_environment = api.isolated_environment
harness = api.harness
pytestmark = pytest.mark.anyio


async def reconstructed(harness):
    _, _, _, actions = harness
    runs, owner, upload = await prepared(harness)
    stages(actions)
    actions.append(decision('finish'))
    state = await runs.start(upload['input_id'], owner, str(uuid4()))
    assert state.status == 'completed'
    return runs, owner, state


def request(focus='openness', **preferences):
    return {'request_id': str(uuid4()), 'focus': focus, 'preferences': preferences}


async def test_http_prepares_a_separate_snapshot_without_changing_original_or_making_paid_calls(harness):
    app, client, calls, _ = harness
    runs, owner, source = await reconstructed(harness)
    count = len(calls)
    body = request(budget={'amount': 0, 'currency': 'USD'}, style='  Warm minimal  ', constraints=' Keep the desk near the window. ')
    url = f'/api/agent-runs/{source.run_id}/optimizations'
    response = await client.post(url, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['run_id'] != source.run_id and result['source_run_id'] == source.run_id
    assert result['status'] == 'prepared' and result['focus'] == 'openness'
    assert result['source_scene'] == source.working_scene.model_dump(mode='json')
    assert result['preferences'] == {'budget': {'amount': 0., 'currency': 'USD'}, 'style': 'Warm minimal', 'constraints': 'Keep the desk near the window.'}
    assert result['expires_at'] == source.model_dump(mode='json')['expires_at']
    private = app.state.optimization_runs._runs[result['run_id']]
    assert private.source.analysis == source.analysis and private.source.analysis is not source.analysis
    assert private.source.confirmed_dimensions == source.confirmed_dimensions
    assert private.source.reconstruction_constraints == source.reconstruction_constraints
    assert (await runs.read(source.run_id, owner)) == source and len(calls) == count
    assert response.headers['cache-control'] == 'no-store'
    assert (await client.post(url, json=body)).json() == result
    assert (await client.get(f"{url}/{result['run_id']}")).json() == result
    # Response copies cannot mutate the protected source snapshot.
    returned = await app.state.optimization_runs.read(result['run_id'], owner)
    returned.source_scene.objects[0].position.x += 1
    assert (await app.state.optimization_runs.read(result['run_id'], owner)).source_scene == source.working_scene
    assert len(calls) == count


@pytest.mark.parametrize('focus', ['openness', 'storage', 'workspace', 'aesthetics'])
async def test_each_focus_can_be_chosen_without_a_recommendation_or_optional_preferences(harness, focus):
    app, _, calls, _ = harness
    _, owner, source = await reconstructed(harness)
    before = len(calls)
    result = await app.state.optimization_runs.prepare(source.run_id, owner, StartOptimizationRequest.model_validate(request(focus)))
    assert result.focus == focus and result.baseline_assessment is None
    assert result.preferences.budget is result.preferences.style is result.preferences.constraints is None
    assert len(calls) == before


async def test_simultaneous_duplicate_submissions_create_one_request_and_conflicts_do_not_overwrite_it(harness):
    app, client, _, _ = harness
    _, owner, source = await reconstructed(harness)
    body = request()
    service = app.state.optimization_runs
    found = []
    async def submit(): found.append(await service.prepare(source.run_id, owner, StartOptimizationRequest.model_validate(body)))
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(submit); tasks.start_soon(submit)
    assert found[0] == found[1] and len(service._runs) == 1
    conflict = await client.post(f'/api/agent-runs/{source.run_id}/optimizations', json={**body, 'focus': 'storage'})
    assert conflict.status_code == 409 and conflict.json()['detail'][0]['code'] == 'optimization_conflict'
    assert (await service.read(found[0].run_id, owner)) == found[0]


async def test_cancellation_is_idempotent_and_same_start_does_not_recreate_cancelled_request(harness):
    _, client, calls, _ = harness
    _, _, source = await reconstructed(harness)
    before = len(calls)
    url = f'/api/agent-runs/{source.run_id}/optimizations'
    body = request()
    initial = (await client.post(url, json=body)).json()
    cancel_url = f"{url}/{initial['run_id']}/cancel"
    cancelled = await client.post(cancel_url, json={})
    assert cancelled.status_code == 200 and cancelled.json()['status'] == 'cancelled'
    assert (await client.post(cancel_url, json={})).json() == cancelled.json()
    assert (await client.post(url, json=body)).json() == cancelled.json()
    assert len(calls) == before


async def test_foreign_session_wrong_parent_and_revoked_input_cannot_access_improvements(harness):
    app, client, _, _ = harness
    _, owner, source = await reconstructed(harness)
    url = f'/api/agent-runs/{source.run_id}/optimizations'
    initial = (await client.post(url, json=request())).json()
    path = f"{url}/{initial['run_id']}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as stranger:
        for method, target, body in [('POST', url, request()), ('GET', path, None), ('POST', f'{path}/cancel', {})]:
            response = await stranger.request(method, target, **({'json': body} if body is not None else {}))
            assert response.status_code == 404
    wrong = path.replace(source.run_id, str(uuid4()))
    assert (await client.post(f'{wrong}/cancel', json={})).status_code == 404
    assert (await client.get(wrong)).status_code == 404
    assert (await client.get(path)).json()['status'] == 'prepared'
    await client.delete(f'/api/rooms/inputs/{source.input_id}')
    assert (await client.get(path)).status_code == 404
    with pytest.raises(RunUnavailable): await app.state.optimization_runs.read(initial['run_id'], owner)


async def test_expiry_and_bounded_capacity(harness):
    runs, owner, source = await reconstructed(harness)
    service = OptimizationRuns(runs, max_runs=1)
    body = StartOptimizationRequest.model_validate(request())
    original = await service.prepare(source.run_id, owner, body)
    from roometry.agents.checkpointing import RunCapacityReached
    with pytest.raises(RunCapacityReached):
        await service.prepare(source.run_id, owner, StartOptimizationRequest.model_validate(request('storage')))
    runs.now = lambda: source.expires_at + timedelta(seconds=1)
    with pytest.raises(RunUnavailable): await service.read(original.run_id, owner)
    assert not service._runs


async def test_incomplete_baseline_requires_restoration_but_keeps_chat_available_without_new_analysis():
    from tests.test_agent_graph import OWNER, Room, pipeline
    from roometry.assessment.metrics import measure_room
    room = Room()
    advance, calls = pipeline(room, partial=True)
    async def assess(state, context): return measure_room(state.working_scene, incomplete_inventory=True)
    runs = room.runs(advance, assess_room=assess)
    source = await runs.create(room.prepared.input_id, OWNER)
    source = await runs.execute(source.run_id, OWNER)
    assert source.status == 'partial'
    service = OptimizationRuns(runs)
    from roometry.optimization.runs import ReconstructionRequired
    with pytest.raises(ReconstructionRequired, match='Bed'):
        await service.prepare(source.run_id, OWNER, StartOptimizationRequest.model_validate(request('workspace')))
    assert not service._runs
    child = await service.prepare(source.run_id, OWNER, StartOptimizationRequest(
        request_id=str(uuid4()),focus='workspace',instruction='Restore the missing bed using an approximate model.',
        context={'scene':source.working_scene}))
    assert child.source_status == 'partial'
    assert child.source_scene == source.working_scene
    assert child.baseline_assessment == source.room_assessment
    assert child.source_scene_hash == source.room_assessment.scene_hash
    assert len(calls) == 2


@pytest.mark.parametrize('defect', ['focus', 'budget', 'currency', 'style', 'extra_scene', 'origin'])
async def test_invalid_or_cross_origin_requests_do_not_create_runs(harness, defect):
    app, client, _, _ = harness
    _, _, source = await reconstructed(harness)
    body, headers = request(), {}
    if defect == 'focus': body['focus'] = 'invented'
    elif defect == 'budget': body['preferences'] = {'budget': {'amount': -1, 'currency': 'USD'}}
    elif defect == 'currency': body['preferences'] = {'budget': {'amount': 10, 'currency': 'bad currency'}}
    elif defect == 'style': body['preferences'] = {'style': 'x' * 101}
    elif defect == 'extra_scene': body['source_scene'] = source.working_scene.model_dump(mode='json')
    else: headers['Origin'] = 'https://untrusted.example'
    response = await client.post(f'/api/agent-runs/{source.run_id}/optimizations', json=body, headers=headers)
    assert response.status_code == (403 if defect == 'origin' else 422), response.text
    assert not app.state.optimization_runs._runs


async def test_unfinished_reconstruction_cannot_seed_an_improvement(harness):
    app, client, _, _ = harness
    runs, owner, uploaded = await prepared(harness)
    initial = await runs.create(uploaded['input_id'], owner)
    response = await client.post(f'/api/agent-runs/{initial.run_id}/optimizations', json=request())
    assert response.status_code == 409 and response.json()['detail'][0]['code'] == 'reconstruction_required'
    assert not app.state.optimization_runs._runs
