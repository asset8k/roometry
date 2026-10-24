"""Actual bounded LangGraph + Responses SDK; all provider responses are mocked."""

from math import pi
from types import SimpleNamespace
from uuid import uuid4

import anyio
import httpx
import json
import pytest
from jsonschema import Draft202012Validator

from roometry.agents.state import RunLimits
from roometry.optimization.policy import evaluate
from roometry.optimization.runs import OptimizationRuns
from roometry.optimization.schemas import CandidateProposal, OptimizationRun, StartOptimizationRequest
from roometry.scenes.hashing import scene_hash
from roometry.scenes.validation import validate_geometry
from roometry.vision.schemas import ReconstructionConstraints
from tests import test_vision_api as api
from tests.test_optimization_runs import reconstructed, request
from tests.test_geometry import obj
from tests.test_layout_scoring import case

anyio_backend = api.anyio_backend
isolated_environment = api.isolated_environment
harness = api.harness
pytestmark = pytest.mark.anyio


def change(object_id='bed_observed', **values):
    return {'object_id': object_id, 'position': None, 'rotation_y_rad': None, 'color': None, 'material_colors': None, **values}


def proposal(changes=None, *, finish=True):
    return {'changes': changes if changes is not None else [change(color='#746A5B')],
            'summary': 'Coordinate the existing upholstery palette.', 'suggestions': [], 'finish': finish}


def provider_action(data, name='optimization_proposal', failure=None):
    async def action(request, payload):
        assert payload['model'] == 'gpt-6-luna' and payload['reasoning'] == {'effort': 'low'}
        assert payload['store'] is False and payload['tools'] == []
        assert payload['text']['format']['name'] == name
        if failure == 'timeout': raise httpx.ReadTimeout('PRIVATE_ERROR', request=request)
        if failure == 'provider': return httpx.Response(500, json={'error': {'message': 'PRIVATE_ERROR'}})
        if failure != 'invalid': Draft202012Validator(payload['text']['format']['schema']).validate(data)
        response = {'id': 'resp_opt', 'object': 'response', 'created_at': 1792404000, 'status': 'completed',
            'model': payload['model'], 'output': [{'id': 'msg_opt', 'type': 'message', 'status': 'completed', 'role': 'assistant',
                'content': [{'type': 'output_text', 'text': json.dumps(data), 'annotations': []}]}],
            'usage': {'input_tokens': 200, 'output_tokens': 100, 'total_tokens': 300}}
        if failure == 'incomplete': response.update(status='incomplete', incomplete_details={'reason': 'max_output_tokens'})
        if failure == 'refusal': response['output'][0]['content'] = [{'type': 'refusal', 'refusal': 'PRIVATE_ERROR'}]
        if name == 'optimization_review':
            images = [c for c in payload['input'][0]['content'] if c['type'] == 'input_image']
            assert len(images) == 6 and all(c['image_url'].startswith('data:image/png') for c in images)
        return httpx.Response(200, json=response)
    return action


def review(**updates):
    return provider_action({'preferred': 'candidate', 'confidence': 'medium', 'constraints_satisfied': True,
        'explanation': 'The coordinated materials improve cohesion without disrupting the furniture grouping.', 'aesthetics': None, **updates}, 'optimization_review')


async def prepared_execution(harness, **kwargs):
    app, _, _, _ = harness
    runs, owner, source = await reconstructed(harness)
    service = OptimizationRuns(runs, provider=app.state.optimization_runs.provider, **kwargs)
    app.state.optimization_runs = service
    child = await service.prepare(source.run_id, owner, StartOptimizationRequest.model_validate(request('aesthetics')))
    return service, owner, source, child


async def test_invalid_candidate_receives_feedback_then_valid_patch_is_reviewed_and_retained(harness):
    app, client, calls, actions = harness
    service, owner, source, child = await prepared_execution(harness)
    before = len(calls)
    actions.extend([provider_action(proposal([change(position={'x': 5., 'z': 2.8})], finish=False)),
                    provider_action(proposal()), review()])
    url = f'/api/agent-runs/{source.run_id}/optimizations/{child.run_id}/execute'
    response = await client.post(url, json={})
    assert response.status_code == 200, response.text
    result = await service.read(child.run_id, owner)
    assert result.status == 'completed', result.message
    assert [a.outcome for a in result.attempts] == ['rejected', 'accepted']
    assert result.result_scene.room == source.working_scene.room
    assert result.result_scene.openings == source.working_scene.openings
    assert result.result_scene.objects[0].color == '#746A5B'
    assert validate_geometry(result.result_scene).valid
    assert (await service.reconstructions.read(source.run_id, owner)) == source
    assert len(calls) - before == result.usage.model_calls == 3
    assert result.usage.input_tokens == 600 and result.usage.output_tokens == 300
    assert result.usage.tool_calls == 4
    assert result.result_assessment.aesthetics.score is None  # No invented numerical taste gain.
    assert [r.operation for r in list(app.state.analysis_service.analyzer.usage_records)[-3:]] == ['optimization_proposal', 'optimization_proposal', 'optimization_review']
    assert all(r.run_id == child.run_id for r in list(app.state.analysis_service.analyzer.usage_records)[-3:])
    assert (await client.post(url, json={})).json() == response.json()
    assert len(calls) == before + 3


async def test_later_failed_proposal_preserves_previously_accepted_candidate(harness):
    _, _, calls, actions = harness
    service, owner, source, child = await prepared_execution(harness)
    actions.extend([provider_action(proposal(finish=False)), review(), provider_action({}, failure='provider')])
    result = await service.execute(child.run_id, owner)
    assert result.status == 'failed' and result.result_scene is not None
    assert result.attempts[0].outcome == 'accepted'
    assert result.result_scene.objects[0].color == '#746A5B'
    count = len(calls)
    assert (await service.execute(child.run_id, owner)) == result and len(calls) == count
    assert 'PRIVATE' not in result.model_dump_json()


@pytest.mark.parametrize('failure', ['timeout', 'provider', 'incomplete', 'refusal', 'invalid'])
async def test_provider_failure_is_terminal_preserves_original_and_never_retries(harness, failure):
    _, _, calls, actions = harness
    service, owner, source, child = await prepared_execution(harness)
    before = len(calls)
    actions.append(provider_action({}, failure=failure))
    result = await service.execute(child.run_id, owner)
    assert result.status == 'failed' and result.result_scene is None
    assert result.source_scene == source.working_scene and len(calls) == before + 1
    assert result.usage.model_calls == 1 and 'PRIVATE' not in result.model_dump_json()
    assert (await service.execute(child.run_id, owner)) == result


async def test_review_uncertainty_and_explicit_constraint_failure_do_not_accept_a_candidate(harness):
    _, _, _, actions = harness
    service, owner, _, child = await prepared_execution(harness, max_candidates=2)
    actions.extend([provider_action(proposal()), review(preferred='uncertain', confidence='low'),
                    provider_action(proposal()), review(constraints_satisfied=False)])
    result = await service.execute(child.run_id, owner)
    assert result.status == 'no_improvement' and result.result_scene is None
    assert len(result.attempts) == 2 and all(a.outcome == 'rejected' for a in result.attempts)
    assert result.usage.model_calls == 4


async def test_insufficient_review_budget_does_not_apply_an_unreviewed_candidate(harness):
    _, _, calls, actions = harness
    service, owner, _, child = await prepared_execution(harness, limits=RunLimits(max_model_calls=1))
    before = len(calls)
    actions.append(provider_action(proposal()))
    result = await service.execute(child.run_id, owner)
    assert result.status == 'budget_exhausted' and result.result_scene is None
    assert result.usage.model_calls == len(calls)-before == 1


async def test_duplicate_execution_and_cancellation_during_provider_call_do_not_dispatch_more_work(harness):
    _, _, calls, actions = harness
    service, owner, _, child = await prepared_execution(harness)
    entered, release = anyio.Event(), anyio.Event()
    async def waiting(request, payload):
        entered.set(); await release.wait()
        return await provider_action(proposal())(request, payload)
    actions.append(waiting)
    before = len(calls)
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(service.execute, child.run_id, owner)
        await entered.wait()
        assert (await service.execute(child.run_id, owner)).status == 'running'
        assert (await service.read(child.run_id, owner)).usage.model_calls == 1
        await service.cancel(child.run_id, owner)
        release.set()
    result = await service.execute(child.run_id, owner)
    assert result.status == 'cancelled' and result.result_scene is None
    assert result.usage.model_calls == len(calls)-before == 1


def policy_run(scene, focus='openness', budget=None):
    from datetime import datetime, UTC, timedelta
    from roometry.optimization.schemas import OptimizationPreferences
    now = datetime.now(UTC)
    return OptimizationRun(run_id=str(uuid4()), source_run_id=str(uuid4()), input_id=str(uuid4()),
        source_scene_hash=scene_hash(scene), source_scene=scene, source_status='completed', baseline_assessment=None,
        focus=focus, preferences=OptimizationPreferences(budget=budget), created_at=now, expires_at=now+timedelta(hours=1))


def test_component_geometry_score_detects_a_real_open_space_gain_at_same_inventory_and_capacity():
    barrier = obj('barrier', kind='wardrobe', x=3., z=3., width=5.4, depth=.4, height=2.)
    run = policy_run(case([barrier]))
    source = SimpleNamespace(reconstruction_constraints=ReconstructionConstraints())
    result = evaluate(run, CandidateProposal.model_validate(proposal([change('barrier', position={'x': 3., 'z': .25})])), source)
    assert not result.issues and result.after_score > result.before_score
    assert result.changed == ['barrier']
    assert result.scene.objects[0].dimensions_m == barrier.dimensions_m


def test_storage_front_access_and_locks_budget_and_inventory_are_enforced():
    cabinet = obj('wardrobe', kind='wardrobe', x=3., z=.35, width=1.5, depth=.6, height=2., rotation=pi)
    source = SimpleNamespace(reconstruction_constraints=ReconstructionConstraints())
    run = policy_run(case([cabinet]), focus='storage')
    result = evaluate(run, CandidateProposal.model_validate(proposal([change('wardrobe', rotation_y_rad=0.)])), source)
    assert not result.issues and result.after_score > result.before_score
    run.source_scene.objects[0].locked = True
    with pytest.raises(ValueError, match='unlocked'): evaluate(run, CandidateProposal.model_validate(proposal([change('wardrobe', rotation_y_rad=0.)])), source)
    from roometry.uploads.schemas import InputBudget
    run = policy_run(case([cabinet]), focus='aesthetics', budget=InputBudget(amount=0., currency='USD'))
    with pytest.raises(ValueError, match='costs are unknown'): evaluate(run, CandidateProposal.model_validate(proposal([change('wardrobe', color='#AA8877')])), source)
    with pytest.raises(ValueError, match='existing'): evaluate(run, CandidateProposal.model_validate(proposal([change('invented', color='#AA8877')])), source)


def test_coordinated_moves_are_validated_atomically_and_supported_items_follow_their_desk():
    from tests.test_agent_graph import Room
    source = SimpleNamespace(reconstruction_constraints=ReconstructionConstraints())
    a, b = obj('a', x=2., z=2., width=.4, depth=.4), obj('b', x=4., z=2., width=.4, depth=.4)
    run = policy_run(case([a, b]))
    candidate = evaluate(run, CandidateProposal.model_validate(proposal([change('a', position={'x': 4., 'z': 2.}), change('b', position={'x': 2., 'z': 2.})])), source)
    assert not candidate.issues and set(candidate.changed) == {'a', 'b'}
    run = policy_run(Room().proposal().scene)
    moved = evaluate(run, CandidateProposal.model_validate(proposal([change('desk_observed', position={'x': 2.8, 'z': .6})])), source)
    assert next(o for o in moved.scene.objects if o.id == 'computer_observed').position.x == pytest.approx(2.8)
    next(o for o in run.source_scene.objects if o.id == 'computer_observed').locked = True
    from roometry.scenes.mutations import EditRejected
    with pytest.raises(EditRejected): evaluate(run, CandidateProposal.model_validate(proposal([change('desk_observed', position={'x': 2.8, 'z': .6})])), source)


async def test_no_candidate_proposal_returns_suggestions_without_a_review_call(harness):
    _, _, _, actions = harness
    service, owner, _, child = await prepared_execution(harness)
    data = proposal([])
    data['suggestions'] = ['Consider a measured desk if you want to create a dedicated workspace. No item was added.']
    actions.append(provider_action(data))
    result = await service.execute(child.run_id, owner)
    assert result.status == 'no_improvement' and result.result_scene is None
    assert result.suggestions == data['suggestions'] and result.usage.model_calls == 1


async def test_step_deadline_and_cancelled_execution_keep_ambiguous_usage_without_replay(harness):
    _, _, calls, actions = harness
    service, owner, _, child = await prepared_execution(harness, limits=RunLimits(step_timeout_seconds=.02))
    async def waiting(request, payload): await anyio.sleep_forever()
    actions.append(waiting)
    before = len(calls)
    result = await service.execute(child.run_id, owner)
    assert result.status == 'failed' and result.usage.unknown_usage_calls == 1
    assert result.usage.model_calls == len(calls)-before == 1
    assert (await service.execute(child.run_id, owner)) == result


async def test_failed_catalog_is_read_once_and_does_not_prevent_existing_item_improvements(harness):
    from roometry.mcp.furniture_client import CatalogFailure
    class FailedCatalog:
        calls = 0
        async def call(self, name, args):
            self.calls += 1
            raise CatalogFailure('catalog_unavailable')
    catalog = FailedCatalog()
    _, _, _, actions = harness
    service, owner, _, child = await prepared_execution(harness)
    service.catalog = catalog
    actions.extend([provider_action(proposal()), review()])
    result = await service.execute(child.run_id, owner)
    assert result.status == 'completed' and catalog.calls == 1
    assert result.usage.model_calls == 2 and result.usage.tool_calls == 3


async def test_aborted_execution_is_cancelled_without_replaying_an_ambiguous_paid_attempt(harness):
    _, _, calls, actions = harness
    service, owner, _, child = await prepared_execution(harness)
    entered = anyio.Event()
    async def waiting(request, payload):
        entered.set()
        await anyio.sleep_forever()
    actions.append(waiting)
    before = len(calls)
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(service.execute, child.run_id, owner)
        await entered.wait()
        tasks.cancel_scope.cancel()
    result = await service.execute(child.run_id, owner)
    assert result.status == 'cancelled' and result.usage.unknown_usage_calls == 1
    assert result.usage.model_calls == len(calls)-before == 1


def test_repeating_existing_material_colors_cannot_claim_an_improvement():
    from tests.test_agent_graph import Room
    from roometry.vision.review_policy import appearance_controls
    scene = Room().proposal().scene
    desk = next(o for o in scene.objects if o.type == 'desk')
    controls = appearance_controls(desk)
    field = next(key for key in controls if key != 'color')
    run = policy_run(scene, 'aesthetics')
    result = evaluate(run, CandidateProposal.model_validate(proposal([change(desk.id, material_colors={field: controls[field]['value']})])),
        SimpleNamespace(reconstruction_constraints=ReconstructionConstraints()))
    assert result.changed == []


def test_echoed_appearance_fields_do_not_reject_a_free_rearrangement():
    from roometry.uploads.schemas import InputBudget
    cabinet = obj('wardrobe', kind='wardrobe', x=3., z=.35, width=1.5, depth=.6, height=2., rotation=pi)
    run = policy_run(case([cabinet]), focus='storage', budget=InputBudget(amount=0., currency='USD'))
    candidate = evaluate(run, CandidateProposal.model_validate(proposal([change('wardrobe', rotation_y_rad=0., color=cabinet.color, material_colors={})])),
        SimpleNamespace(reconstruction_constraints=ReconstructionConstraints()))
    assert not candidate.issues and candidate.after_score > candidate.before_score


def aesthetic_pair():
    return {'retained': {'color_harmony': 1, 'material_cohesion': 1, 'visual_balance': 1, 'visual_order': 1, 'lighting_composition': 2},
            'candidate': {'color_harmony': 2, 'material_cohesion': 2, 'visual_balance': 1, 'visual_order': 1, 'lighting_composition': 2},
            'confidence': 'medium', 'explanation': 'The restrained palette is more cohesive; the lighting arrangement stays the same.'}


async def test_rendered_rubric_is_returned_in_the_existing_review_without_an_extra_call(harness):
    _, _, calls, actions = harness
    service, owner, source, child = await prepared_execution(harness)
    before = len(calls)
    actions.extend([provider_action(proposal()), review(aesthetics=aesthetic_pair())])
    result = await service.execute(child.run_id, owner)
    assert result.status == 'completed'
    pair = result.attempts[-1].review.aesthetics
    assert pair.retained.total == 6 and pair.candidate.total == 8
    assert result.result_assessment.aesthetics.score is None  # No mixing photos and rendered ratings.
    assert result.usage.model_calls == len(calls) - before == 2
    assert (await service.reconstructions.read(source.run_id, owner)) == source


async def test_bad_optional_rubric_does_not_discard_a_valid_pairwise_judgment(harness):
    _, _, calls, actions = harness
    service, owner, _, child = await prepared_execution(harness)
    before = len(calls)
    data = {'preferred': 'candidate', 'confidence': 'medium', 'constraints_satisfied': True,
            'explanation': 'The palette is more cohesive.', 'aesthetics': {'retained': 'bad'}}
    actions.extend([provider_action(proposal()), provider_action(data, 'optimization_review', failure='invalid')])
    result = await service.execute(child.run_id, owner)
    assert result.status == 'completed' and result.result_scene is not None
    assert result.attempts[-1].review.aesthetics is None
    assert result.usage.model_calls == len(calls) - before == 2
