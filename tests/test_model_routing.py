"""Real SDK/graph routing with mocked HTTP; these tests never spend API credits."""

import httpx
import pytest
from pydantic import ValidationError

from roometry.config import Settings
from tests import test_vision_api as api
from tests.test_agent_tools import decision, photo, prepared
from tests.test_scene_proposals import analyzed, body, confirmed, output_limited_scene, scene_action

anyio_backend = api.anyio_backend
isolated_environment = api.isolated_environment
harness = api.harness


def test_default_stage_routes_and_visual_review_configuration_do_not_enable_calls():
    settings = Settings(_env_file=None)
    expected = {
        'analysis': ('gpt-6-luna', 'medium'),
        'scene_proposal': ('gpt-6.1-sol', 'medium'),
        'scene_repair': ('gpt-6-luna', 'medium'),
        'visual_review': ('gpt-6-luna', 'medium'),
        'visual_repair': ('gpt-6-luna', 'medium'),
        'agent_decision': ('gpt-6-luna', 'low'),
    }
    for operation, (model, effort) in expected.items():
        route = settings.model_route(operation)
        assert (route.model, route.reasoning_effort) == (model, effort)


def test_stage_overrides_take_precedence_over_the_legacy_economical_fallback():
    settings = Settings(
        _env_file=None, model_name='economy-default',
        analysis_model_name='analysis-test', review_model_name='review-test',
        scene_model_name='construction-test', scene_repair_model_name='repair-test',
    )
    assert settings.model_route('agent_decision').model == 'economy-default'
    assert settings.model_route('analysis').model == 'analysis-test'
    assert settings.model_route('visual_review').model == 'review-test'
    assert settings.model_route('scene_repair').model == 'repair-test'
    assert settings.model_route('scene_proposal').model == 'construction-test'


@pytest.mark.parametrize('field', ['analysis_model_name', 'agent_model_name', 'review_model_name', 'scene_repair_model_name', 'model_name'])
def test_sol_cannot_silently_enter_economical_stages(field):
    with pytest.raises(ValidationError, match='Sol is reserved'):
        Settings(_env_file=None, **{field: 'gpt-6.1-sol'})


@pytest.mark.parametrize('field', ['analysis_model_name', 'agent_model_name', 'review_model_name', 'scene_repair_model_name', 'scene_model_name'])
def test_blank_explicit_stage_models_are_rejected(field):
    with pytest.raises(ValidationError, match='must not be blank'):
        Settings(_env_file=None, **{field: '  '})


def test_sol_incompatible_effort_fails_before_any_provider_request():
    with pytest.raises(ValidationError, match='reasoning effort'):
        Settings(_env_file=None, scene_reasoning_effort='none')
    assert Settings(_env_file=None, scene_model_name='gpt-6-luna', scene_reasoning_effort='none')


@pytest.mark.anyio
async def test_output_limit_repairs_with_luna_and_cache_and_revised_dimensions_cannot_reset_sol(harness):
    app, client, calls, actions = harness
    analysis = await analyzed(harness)
    actions.extend([output_limited_scene(), scene_action()])
    request = body(analysis, dimensions=confirmed(length_m=4, height_m=2.6))
    result = await client.post('/api/rooms/propose-scene', json=request)
    assert result.status_code == 200 and result.json()['status'] == 'ready'
    assert [call['model'] for call in calls] == ['gpt-6-luna', 'gpt-6.1-sol', 'gpt-6-luna']
    assert all(call['reasoning'] == {'effort': 'medium'} for call in calls)
    assert (await client.post('/api/rooms/propose-scene', json=request)).json() == result.json()
    changed = await client.post('/api/rooms/propose-scene', json=body(analysis, dimensions=confirmed(length_m=5, height_m=2.6)))
    assert changed.json()['status'] == 'incomplete'
    assert len(calls) == 3
    records = app.state.analysis_service.usage_records
    assert [r.model for r in records] == [call['model'] for call in calls]
    assert [r.operation for r in records] == ['analysis', 'scene_proposal', 'scene_repair']
    provider_records = app.state.analysis_service.analyzer.usage_records
    assert [r.operation for r in provider_records] == ['analysis', 'scene_proposal', 'scene_repair']
    assert provider_records[1].outcome == 'incomplete'
    assert provider_records[-1].outcome == 'completed'
    assert all(r.latency_ms >= 0 for r in provider_records)


@pytest.mark.anyio
@pytest.mark.parametrize('kind', ['timeout', 'connection', 'provider_error'])
async def test_ambiguous_or_failed_sol_call_consumes_its_allowance_before_dispatch(harness, kind):
    app, client, calls, actions = harness
    analysis = await analyzed(harness)

    async def failure(request, payload):
        if kind == 'timeout':
            raise httpx.ReadTimeout('PRIVATE_PROVIDER_FAILURE', request=request)
        if kind == 'connection':
            raise httpx.ConnectError('PRIVATE_PROVIDER_FAILURE', request=request)
        return httpx.Response(500, json={'error': {'message': 'PRIVATE_PROVIDER_FAILURE'}})

    actions.append(failure)
    request = body(analysis, dimensions=confirmed(length_m=4, height_m=2.6))
    first = await client.post('/api/rooms/propose-scene', json=request)
    assert first.status_code in {503, 504} and len(calls) == 2
    failed_record = app.state.analysis_service.analyzer.usage_records[-1]
    assert failed_record.model == 'gpt-6.1-sol' and failed_record.total_tokens is None
    actions.append(scene_action())
    second = await client.post('/api/rooms/propose-scene', json=request)
    assert second.status_code == 200 and second.json()['status'] == 'ready'
    assert [call['model'] for call in calls] == ['gpt-6-luna', 'gpt-6.1-sol', 'gpt-6-luna']
    assert 'PRIVATE' not in first.text


@pytest.mark.anyio
async def test_entire_graph_records_model_and_usage_for_decisions_analysis_and_repairs(harness, caplog):
    app, client, calls, actions = harness
    runs, owner, uploaded = await prepared(harness)
    actions.extend([
        decision('analyze_photos'), photo, decision('propose_scene'),
        output_limited_scene(), scene_action(), decision('finish'),
    ])
    initial = await runs.create(uploaded['input_id'], owner)
    with caplog.at_level('INFO', logger='roometry.vision.analyzer'):
        result = await runs.execute(initial.run_id, owner)
    assert result.status == 'completed'
    assert len(calls) == result.usage.model_calls == 6
    records = app.state.analysis_service.analyzer.usage_records
    assert len(records) == 6
    assert [r.operation for r in records] == [
        'agent_decision', 'analysis', 'agent_decision', 'scene_proposal', 'scene_repair', 'agent_decision',
    ]
    assert sum(r.model == 'gpt-6.1-sol' for r in records) == 1
    assert sum(r.input_tokens for r in records) == result.usage.input_tokens
    assert sum(r.output_tokens for r in records) == result.usage.output_tokens
    assert all(r.run_id == initial.run_id for r in records)
    logged = [r.model_usage for r in caplog.records if hasattr(r, 'model_usage')]
    assert len(logged) == len(records)
    assert all('input' not in r and 'owner' not in r and 'api_key' not in r for r in logged)
    assert not any(r.operation == 'visual_review' for r in records)
    assert await runs.execute(initial.run_id, owner) == result and len(calls) == 6


@pytest.mark.anyio
async def test_record_distinguishes_requested_model_from_provider_snapshot(harness):
    app, client, calls, actions = harness
    analysis = await analyzed(harness)

    async def snapshot(request, payload):
        result = (await scene_action()(request, payload)).json()
        result['model'] = 'gpt-6.1-sol-test-snapshot'
        return httpx.Response(200, json=result)

    actions.append(snapshot)
    result = await client.post('/api/rooms/propose-scene', json=body(analysis, dimensions=confirmed(length_m=4, height_m=2.6)))
    assert result.json()['status'] == 'ready'
    for record in (app.state.analysis_service.usage_records[-1], app.state.analysis_service.analyzer.usage_records[-1]):
        assert record.requested_model == 'gpt-6.1-sol'
        assert record.model == 'gpt-6.1-sol-test-snapshot'
