"""Real SDK/graph routing with mocked HTTP; these tests never spend API credits."""

import httpx
import pytest
from pydantic import ValidationError

from roometry.config import Settings
from tests import test_vision_api as api
from tests.test_agent_tools import decision, photo, prepared
from tests.test_scene_proposals import (
    analyzed,
    body,
    confirmed,
    output_limited_scene,
    scene_action,
)

anyio_backend = api.anyio_backend
isolated_environment = api.isolated_environment
harness = api.harness


def test_default_stage_routes_and_visual_review_configuration_do_not_enable_calls():
    settings = Settings(_env_file=None)
    expected = {
        'analysis': ('gpt-6-luna', 'medium'),
        'scene_proposal': ('gpt-6.1-terra', 'medium'),
        'scene_repair': ('gpt-6-luna', 'medium'),
        'visual_review': ('gpt-6.1-terra', 'low'),
        'visual_repair': ('gpt-6-luna', 'medium'),
        'agent_decision': ('gpt-6-luna', 'low'),
        'room_assessment': ('gpt-6-luna', 'low'),
        'optimization_proposal': ('gpt-6.1-terra', 'low'),
        'optimization_review': ('gpt-6-luna', 'low'),
    }
    for operation, (model, effort) in expected.items():
        route = settings.model_route(operation)
        assert (route.model, route.reasoning_effort) == (model, effort)


def test_stage_overrides_take_precedence_over_the_legacy_economical_fallback():
    settings = Settings(
        _env_file=None, model_name='economy-default',
        analysis_model_name='analysis-test', review_model_name='review-test', visual_review_model_name='visual-review-test',
        scene_model_name='construction-test', scene_repair_model_name='repair-test',
        optimization_model_name='optimization-test',
    )
    assert settings.model_route('agent_decision').model == 'economy-default'
    assert settings.model_route('analysis').model == 'analysis-test'
    assert settings.model_route('visual_review').model == 'visual-review-test'
    assert settings.model_route('room_assessment').model == 'review-test'
    assert settings.model_route('optimization_review').model == 'review-test'
    assert settings.model_route('optimization_proposal').model == 'optimization-test'
    assert settings.model_route('scene_repair').model == 'repair-test'
    assert settings.model_route('scene_proposal').model == 'construction-test'


@pytest.mark.parametrize('field', ['analysis_model_name', 'agent_model_name', 'review_model_name', 'visual_review_model_name', 'optimization_model_name', 'scene_repair_model_name', 'model_name'])
def test_sol_cannot_silently_enter_economical_stages(field):
    with pytest.raises(ValidationError, match='Sol is reserved'):
        Settings(_env_file=None, **{field: 'gpt-6.1-sol'})


@pytest.mark.parametrize('field', ['analysis_model_name', 'agent_model_name', 'review_model_name', 'visual_review_model_name', 'optimization_model_name', 'scene_repair_model_name', 'scene_model_name'])
def test_blank_explicit_stage_models_are_rejected(field):
    with pytest.raises(ValidationError, match='must not be blank'):
        Settings(_env_file=None, **{field: '  '})


def test_sol_incompatible_effort_fails_before_any_provider_request():
    with pytest.raises(ValidationError, match='reasoning effort'):
        Settings(_env_file=None, scene_model_name='gpt-6.1-sol', scene_reasoning_effort='none')
    assert Settings(_env_file=None, scene_model_name='gpt-6.1-terra', scene_reasoning_effort='none')
    assert Settings(_env_file=None, scene_model_name='gpt-6-luna', scene_reasoning_effort='none')


def test_explicit_sol_construction_remains_available_for_comparison():
    settings = Settings(_env_file=None, scene_model_name='gpt-6.1-sol')
    assert settings.model_route('scene_proposal').model == 'gpt-6.1-sol'
    assert settings.model_route('scene_repair').model == 'gpt-6-luna'


def test_scene_timeout_is_independent_and_outer_graph_allows_initial_and_repair():
    from roometry.main import create_app
    settings = Settings(_env_file=None, analysis_timeout_seconds=150, scene_timeout_seconds=240)
    assert settings.request_timeout('scene_proposal') == settings.request_timeout('scene_repair') == 240
    for operation in ('analysis', 'visual_review', 'visual_repair', 'optimization_proposal', 'optimization_review'):
        assert settings.request_timeout(operation) == 150
    assert create_app(settings).state.agent_runs.default_limits.step_timeout_seconds == 485
    legacy = Settings(_env_file=None, analysis_timeout_seconds=12)
    assert legacy.request_timeout('scene_proposal') == 12


@pytest.mark.parametrize('value', [0, -1, 301, float('inf')])
def test_scene_timeout_remains_bounded(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, scene_timeout_seconds=value)


@pytest.mark.anyio
async def test_scene_sdk_uses_scene_timeout_without_automatic_paid_retries(harness):
    app, client, calls, actions = harness
    analysis = await analyzed(harness)
    app.state.settings.analysis_timeout_seconds = 0.01
    app.state.settings.scene_timeout_seconds = 0.5
    async def slow(request, payload):
        import anyio
        assert request.extensions['timeout']['read'] == 0.5
        await anyio.sleep(0.03)
        return await scene_action()(request, payload)
    actions.append(slow)
    response = await client.post('/api/rooms/propose-scene', json=body(analysis, dimensions=confirmed(length_m=4, height_m=2.6)))
    assert response.json()['status'] == 'ready'
    assert len(calls) == 2


@pytest.mark.anyio
async def test_output_limit_repairs_with_luna_and_cache_and_revised_dimensions_cannot_reset_initial_construction(harness):
    app, client, calls, actions = harness
    analysis = await analyzed(harness)
    actions.extend([output_limited_scene(), scene_action()])
    request = body(analysis, dimensions=confirmed(length_m=4, height_m=2.6))
    result = await client.post('/api/rooms/propose-scene', json=request)
    assert result.status_code == 200 and result.json()['status'] == 'ready'
    assert [call['model'] for call in calls] == ['gpt-6-luna', 'gpt-6.1-terra', 'gpt-6-luna']
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
async def test_ambiguous_or_failed_construction_call_consumes_its_allowance_before_dispatch(harness, kind):
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
    assert failed_record.model == 'gpt-6.1-terra' and failed_record.total_tokens is None
    actions.append(scene_action())
    second = await client.post('/api/rooms/propose-scene', json=request)
    assert second.status_code == 200 and second.json()['status'] == 'ready'
    assert [call['model'] for call in calls] == ['gpt-6-luna', 'gpt-6.1-terra', 'gpt-6-luna']
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
    assert sum(r.model == 'gpt-6.1-terra' for r in records) == 1
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
        result['model'] = 'gpt-6.1-terra-test-snapshot'
        return httpx.Response(200, json=result)

    actions.append(snapshot)
    result = await client.post('/api/rooms/propose-scene', json=body(analysis, dimensions=confirmed(length_m=4, height_m=2.6)))
    assert result.json()['status'] == 'ready'
    for record in (app.state.analysis_service.usage_records[-1], app.state.analysis_service.analyzer.usage_records[-1]):
        assert record.requested_model == 'gpt-6.1-terra'
        assert record.model == 'gpt-6.1-terra-test-snapshot'
