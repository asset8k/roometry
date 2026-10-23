"""Multi-view semantic repair over the real SDK, with mocked provider responses."""

import json
from uuid import uuid4

import httpx
import pytest

from tests import test_vision_api as api
from tests.test_agent_tools import decision
from tests.test_scene_proposals import scene_action

anyio_backend = api.anyio_backend
isolated_environment = api.isolated_environment
harness = api.harness
pytestmark = pytest.mark.anyio


def inconsistent(kind):
    async def action(request, payload):
        response = api.provider_response(payload)
        obs = api.observations_for(payload)
        sources = obs['image_ids']
        if kind == 'wall_span_evidence':
            obs['walls'][0]['span'] = {
                'role': 'shorter', 'confidence': 'medium',
                'source_image_ids': [sources[1]], 'evidence': 'Two room views show the wall span.',
            }
        else:
            obs['relationships'] = [{
                'id': 'relation_1', 'subject_id': 'computer_observed',
                'target_id': 'desk_observed', 'alternative_target_id': None,
                'kind': 'supported_by', 'confidence': 'high',
                'source_image_ids': [sources[-1]], 'evidence': 'The computer rests on the desk.',
            }]
            relation = obs['relationships'][0]
            if kind == 'relationship_endpoint_evidence':
                relation['source_image_ids'] = [sources[1]]
            elif kind == 'relationship_endpoint':
                relation['target_id'] = 'private_missing_endpoint'
            elif kind == 'relationship_support':
                relation['target_id'] = 'bed_observed'
            else:
                raise AssertionError(kind)
        response['output'][0]['content'][0]['text'] = json.dumps(obs)
        return httpx.Response(200, json=response)
    return action


@pytest.mark.parametrize('rule', ['wall_span_evidence', 'relationship_endpoint_evidence', 'relationship_endpoint', 'relationship_support'])
async def test_completed_multi_view_inconsistency_gets_one_grounded_correction(harness, rule, caplog):
    app, client, calls, actions = harness
    actions.append(inconsistent(rule))
    uploaded = await api.upload(client, 5)
    response = await client.post('/api/rooms/analyze', json={'input_id': uploaded['input_id']})
    assert response.status_code == 200, response.text
    assert len(calls) == 2
    initial, correction = calls
    assert correction['input'][0] == initial['input'][0]  # All original photos/context.
    schema = correction['text']['format']['schema']
    assert correction['text']['format']['strict'] is True
    assert schema['properties']['image_ids'] == initial['text']['format']['schema']['properties']['image_ids']
    for kind, definition in [('walls', 'WallObservation'), ('objects', 'ObjectObservation'), ('openings', 'OpeningObservation')]:
        ids = [entity['id'] for entity in api.SOURCE[kind]]
        assert schema['properties'][kind]['minItems'] == schema['properties'][kind]['maxItems'] == len(ids)
        assert schema['$defs'][definition]['properties']['id']['enum'] == ids
    assert correction['store'] is False and correction['tools'] == []
    feedback = json.loads(correction['input'][-1]['content'])
    assert feedback['validation_rules'] and 'private' not in json.dumps(feedback).lower()
    assert f'"rule": "{rule}"' in caplog.text
    assert 'private' not in caplog.text.lower()
    result = response.json()
    assert len(result['observations']['objects']) == len(api.SOURCE['objects'])
    assert [r.outcome for r in app.state.analysis_service.usage_records] == ['invalid_analysis', 'success']
    assert sum(r.total_tokens for r in app.state.analysis_service.usage_records) == 4000
    cached = await client.post('/api/rooms/analyze', json={'input_id': uploaded['input_id']})
    assert cached.json() == result and len(calls) == 2


async def test_a_second_invalid_result_stops_and_cannot_loop_on_client_retry(harness):
    app, client, calls, actions = harness
    actions.extend([inconsistent('relationship_support')] * 2)
    uploaded = await api.upload(client, 5)
    api.assert_error(await client.post('/api/rooms/analyze', json={'input_id': uploaded['input_id']}), 502, 'invalid_analysis')
    api.assert_error(await client.post('/api/rooms/analyze', json={'input_id': uploaded['input_id']}), 429, 'analysis_limit_reached')
    assert len(calls) == 2 and next(iter(app.state.analysis_service._states.values())).result is None


@pytest.mark.parametrize('change', ['remove', 'rename'])
async def test_a_repair_cannot_succeed_by_removing_or_renaming_observed_furniture(harness, change):
    app, client, calls, actions = harness

    async def empty_inventory(request, payload):
        response = api.provider_response(payload)
        obs = api.observations_for(payload)
        if change == 'remove':
            obs.update(objects=[], possible_duplicates=[])
        else:
            obs['objects'][0]['id'] = 'renamed_bed'
        response['output'][0]['content'][0]['text'] = json.dumps(obs)
        return httpx.Response(200, json=response)

    actions.extend([inconsistent('relationship_support'), empty_inventory])
    uploaded = await api.upload(client, 5)
    api.assert_error(await client.post('/api/rooms/analyze', json={'input_id': uploaded['input_id']}), 502, 'invalid_analysis')
    assert len(calls) == 2 and next(iter(app.state.analysis_service._states.values())).result is None


async def test_repair_does_not_run_after_input_deletion(harness):
    app, client, calls, actions = harness

    async def discard(request, payload):
        response = await inconsistent('relationship_support')(request, payload)
        await client.delete(f'/api/rooms/inputs/{uploaded["input_id"]}')
        return response

    actions.append(discard)
    uploaded = await api.upload(client, 5)
    api.assert_error(await client.post('/api/rooms/analyze', json={'input_id': uploaded['input_id']}), 404, 'input_not_found')
    assert len(calls) == 1


async def test_repair_respects_request_size_limit(harness):
    app, client, calls, actions = harness

    async def at_limit(request, payload):
        app.state.settings.analysis_max_request_bytes = len(json.dumps(payload, ensure_ascii=True).encode('utf-8'))
        return await inconsistent('relationship_support')(request, payload)

    actions.append(at_limit)
    uploaded = await api.upload(client, 5)
    api.assert_error(await client.post('/api/rooms/analyze', json={'input_id': uploaded['input_id']}), 502, 'invalid_analysis')
    assert len(calls) == 1


async def test_five_photo_graph_repair_then_clarification_resumes_without_analysis_replay(harness):
    app, client, calls, actions = harness
    uploaded = await api.upload(client, 5)
    actions.extend([
        decision('analyze_photos'), inconsistent('relationship_support'),
        # Default action returns a valid analysis after the failed one.
        lambda request, payload: _valid_response(payload),
        decision('request_clarification', {'kind': 'room_dimensions', 'object_id': None}),
    ])
    response = await client.post('/api/agent-runs', json={'input_id': uploaded['input_id'], 'request_id': str(uuid4())})
    assert response.status_code == 200, response.text
    run = response.json()
    assert run['status'] == 'awaiting_answer', run
    question = run['pending_question']
    assert question['kind'] == 'room_dimensions' and len(calls) == 4
    saved_analysis = run['analysis']
    actions.extend([decision('propose_scene'), scene_action(), decision('finish')])
    answer = {'question_id': question['id'], 'choice_id': 'enter_measurements', 'dimensions': {'length_m': 4, 'height_m': 2.6}}
    resumed = await client.post(f'/api/agent-runs/{run["run_id"]}/answer', json=answer)
    assert resumed.status_code == 200, resumed.text
    final = resumed.json()
    assert final['status'] == 'completed', final
    assert final['analysis'] == saved_analysis
    assert final['usage']['model_calls'] == len(calls) == 7
    assert final['usage']['unknown_usage_calls'] == 0
    assert [r.stage for r in app.state.analysis_service.usage_records] == ['analysis', 'analysis', 'scene_proposal']
    # A duplicate answer reads the checkpoint, without paying again.
    repeated = await client.post(f'/api/agent-runs/{run["run_id"]}/answer', json=answer)
    assert repeated.json() == final and len(calls) == 7


async def _valid_response(payload):
    return httpx.Response(200, json=api.provider_response(payload))
