"""Current-scene authorization and bounded refinement with mocked SDK responses."""
from uuid import uuid4

import pytest
from pydantic import ValidationError

from roometry.optimization.context import InvalidSceneContext
from roometry.optimization.schemas import StartOptimizationRequest
from roometry.scenes.hashing import scene_hash
from roometry.scenes.edit_schemas import MoveObject, FloorPosition
from roometry.scenes.mutations import apply_edit
from tests import test_vision_api as api
from tests.test_optimization_runs import reconstructed
from tests.test_optimization_execution import provider_action, proposal, review, change

anyio_backend = api.anyio_backend
isolated_environment = api.isolated_environment
harness = api.harness
pytestmark = pytest.mark.anyio


def request(scene, parent=None, text='Use warmer upholstery, keeping the current furniture arrangement.'):
    return {'request_id': str(uuid4()), 'focus': 'aesthetics', 'instruction': text,
            'context': {'scene': scene.model_dump(mode='json'), 'parent_run_id': parent}}


async def test_current_edits_and_additional_locks_seed_an_immutable_snapshot_without_analysis(harness):
    app, client, calls, _ = harness
    _, _, source = await reconstructed(harness)
    scene = source.working_scene.model_copy(deep=True)
    desk = next(o for o in scene.objects if o.id == 'desk_observed')
    scene = apply_edit(scene, MoveObject(operation='move', object_id=desk.id, position=FloorPosition(x=2.8, z=.6)))
    next(o for o in scene.objects if o.id == 'bed_observed').locked = True
    count = len(calls)
    response = await client.post(f'/api/agent-runs/{source.run_id}/optimizations', json=request(scene))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['source_scene'] == scene.model_dump(mode='json')
    assert result['source_scene_hash'] == scene_hash(scene)
    assert result['baseline_assessment'] is None
    assert result['max_candidates'] == 2 and result['limits']['max_model_calls'] == 4
    assert next(o for o in result['source_scene']['objects'] if o['id'] == 'bed_observed')['locked']
    assert len(calls) == count
    assert app.state.optimization_runs._runs[result['run_id']].source.working_scene == source.working_scene


@pytest.mark.parametrize('tamper', ['room', 'asset', 'support', 'fixed', 'geometry', 'identity'])
async def test_forged_context_is_rejected_before_any_model_call(harness, tamper):
    _, client, calls, _ = harness
    _, _, source = await reconstructed(harness)
    body = request(source.working_scene)
    scene = body['context']['scene']
    if tamper == 'room': scene['room']['width_m'] += 1
    if tamper == 'asset': scene['objects'][0]['asset_id'] = 'untrusted_asset'
    if tamper == 'support': next(o for o in scene['objects'] if o['id'] == 'computer_observed')['position']['x'] += .3
    if tamper == 'fixed': scene['objects'][0]['fixed'] = not scene['objects'][0]['fixed']
    if tamper == 'geometry': scene['objects'][0]['position']['x'] = -90
    if tamper == 'identity': scene['objects'].pop()
    count = len(calls)
    response = await client.post(f'/api/agent-runs/{source.run_id}/optimizations', json=body)
    assert response.status_code in {409, 422}, response.text
    assert len(calls) == count


async def test_refinement_is_reviewed_once_and_can_chain_from_retained_candidate(harness):
    app, client, calls, actions = harness
    _, owner, source = await reconstructed(harness)
    url = f'/api/agent-runs/{source.run_id}/optimizations'
    body = request(source.working_scene)
    prepared = (await client.post(url, json=body)).json()
    count = len(calls)
    actions.extend([provider_action(proposal()), review()])
    result = (await client.post(f"{url}/{prepared['run_id']}/execute", json={})).json()
    assert result['status'] == 'completed', result
    assert len(calls) == count + 2
    for payload in calls[count:]:
        assert 'Use warmer upholstery' in str(payload)
    assert (await client.post(f"{url}/{prepared['run_id']}/execute", json={})).json() == result
    assert len(calls) == count + 2
    current = await app.state.optimization_runs.read(result['run_id'], owner)
    followup = await client.post(url, json=request(current.result_scene, current.run_id, 'Turn the chair toward the desk.'))
    assert followup.status_code == 200, followup.text
    assert followup.json()['source_scene'] == result['result_scene']
    assert followup.json()['parent_run_id'] == current.run_id
    assert len(calls) == count + 2


async def test_new_snapshot_locks_are_enforced_during_candidate_validation(harness):
    app, client, calls, actions = harness
    _, _, source = await reconstructed(harness)
    current = source.working_scene.model_copy(deep=True)
    next(o for o in current.objects if o.id == 'bed_observed').locked = True
    url = f'/api/agent-runs/{source.run_id}/optimizations'
    prepared = (await client.post(url, json=request(current))).json()
    actions.extend([provider_action(proposal()), provider_action(proposal([]))])
    count = len(calls)
    result = (await client.post(f"{url}/{prepared['run_id']}/execute", json={})).json()
    assert result['status'] == 'no_improvement' and result['result_scene'] is None
    assert 'unlocked' in result['attempts'][0]['issues'][0]
    assert len(calls) == count + 2


async def test_specific_refinement_does_not_require_a_generic_focus_score_gain(harness):
    _, client, calls, actions = harness
    _, _, source = await reconstructed(harness)
    body = request(source.working_scene)
    body['focus'] = 'workspace'  # Upholstery intent may leave this numerical score unchanged.
    url = f'/api/agent-runs/{source.run_id}/optimizations'
    child = (await client.post(url, json=body)).json()
    actions.extend([provider_action(proposal()), review()])
    count = len(calls)
    result = (await client.post(f"{url}/{child['run_id']}/execute", json={})).json()
    assert result['status'] == 'completed', result
    assert result['attempts'][0]['before_score'] == result['attempts'][0]['after_score']
    assert len(calls) == count + 2


async def test_disabled_product_quota_does_not_retain_the_old_five_chat_limit(harness):
    app, _, calls, _ = harness
    _, owner, source = await reconstructed(harness)
    service = app.state.optimization_runs
    body = StartOptimizationRequest.model_validate(request(source.working_scene))
    first = await service.prepare(source.run_id, owner, body)
    for _ in range(6):
        await service.prepare(source.run_id, owner, StartOptimizationRequest.model_validate(request(source.working_scene)))
    assert (await service.prepare(source.run_id, owner, body)).run_id == first.run_id
    assert len(service._runs) == 7


async def test_foreign_or_unretained_parent_cannot_seed_refinement(harness):
    app, client, calls, _ = harness
    _, _, source = await reconstructed(harness)
    url = f'/api/agent-runs/{source.run_id}/optimizations'
    parent = (await client.post(url, json=request(source.working_scene))).json()
    count = len(calls)
    assert (await client.post(url, json=request(source.working_scene, parent['run_id']))).status_code == 409
    app.state.optimization_runs._runs[parent['run_id']].owner = 'different-owner'
    assert (await client.post(url, json=request(source.working_scene, parent['run_id']))).status_code == 404
    assert len(calls) == count


def test_refinement_needs_a_nonempty_instruction_and_snapshot():
    with pytest.raises(ValidationError):
        StartOptimizationRequest(request_id=str(uuid4()), focus='aesthetics', instruction=' ')
    with pytest.raises(ValidationError):
        StartOptimizationRequest(request_id=str(uuid4()), focus='aesthetics', instruction='Move the desk')


async def test_scene_requests_have_a_bounded_body_allowance(harness):
    _, client, _, _ = harness
    _, _, source = await reconstructed(harness)
    # The ordinary run mutation limit remains small; only snapshot preparation is larger.
    response = await client.post(f'/api/agent-runs/{source.run_id}/optimizations', content=b' ' * 524289,
                                headers={'content-type': 'application/json'})
    assert response.status_code == 413


async def test_existing_lock_cannot_be_removed_or_recolored(harness):
    from roometry.optimization.context import validate_context
    _, _, source = await reconstructed(harness)
    base = source.working_scene.model_copy(deep=True)
    base.objects[0].locked = True
    for kind in ('unlock', 'recolor'):
        candidate = base.model_copy(deep=True)
        if kind == 'unlock': candidate.objects[0].locked = False
        else: candidate.objects[0].color = '#123456'
        with pytest.raises(InvalidSceneContext, match='locked'):
            validate_context(base, candidate)
