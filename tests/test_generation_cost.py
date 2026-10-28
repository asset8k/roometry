"""Economical routing and compact evidence over mocked HTTP; never paid calls."""

import json
from base64 import b64decode
from itertools import chain
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from roometry.agents.state import AgentState
from roometry.config import Settings
from roometry.vision.context_budget import photo_references, select_photo_ids
from roometry.vision.proposal_request import build_proposal_request
from roometry.vision.proposal_schemas import SceneDraft
from roometry.vision.proposals import issue
from roometry.vision.recovery import actionable_repair
from tests import test_vision_api as api
from tests.test_agent_tools import photo, prepared
from tests.test_scene_proposals import draft_data, measurements, scene_action, source

anyio_backend = api.anyio_backend
isolated_environment = api.isolated_environment
harness = api.harness
pytestmark = pytest.mark.anyio


async def test_default_economical_graph_analyzes_constructs_and_finishes_without_decision_calls(harness):
    app, client, calls, actions = harness
    app.state.settings.agent_deterministic_routing = True
    runs, owner, uploaded = await prepared(harness)
    actions.extend([photo, scene_action()])
    request = {'input_id': uploaded['input_id'], 'request_id': str(uuid4())}
    response = await client.post('/api/agent-runs', json=request)
    assert response.status_code == 200, response.text
    state = AgentState.model_validate_json(response.text)
    assert state.status == 'completed' and state.proposal.validation.valid
    assert [c['model'] for c in calls] == ['gpt-6-luna', 'gpt-6.1-terra']
    assert state.usage.model_calls == 2
    assert state.usage.tool_calls == 2
    assert all(c['prompt_cache_options'] == {'mode': 'explicit'} for c in calls)
    assert (await client.post('/api/agent-runs', json=request)).json() == response.json()
    assert (await runs.execute(state.run_id, owner)) == state
    assert len(calls) == 2
    records = app.state.analysis_service.analyzer.usage_records
    assert records[-1].request_image_count == 1 and records[-1].request_text_characters > 0


async def test_free_clarification_interrupt_and_answer_do_not_repeat_analysis(harness):
    app, client, calls, actions = harness
    app.state.settings.agent_deterministic_routing = True
    _, _, uploaded = await prepared(harness, complete=False)
    actions.append(photo)
    paused = await client.post('/api/agent-runs', json={'input_id': uploaded['input_id'], 'request_id': str(uuid4())})
    assert paused.status_code == 200
    state = paused.json()
    assert state['status'] == 'awaiting_answer' and len(calls) == 1
    assert state['pending_question']['kind'] == 'room_dimensions'
    actions.append(scene_action())
    answer = {'question_id': state['pending_question']['id'], 'choice_id': 'enter_measurements',
              'dimensions': {'length_m': 4., 'height_m': 2.6}}
    url = f"/api/agent-runs/{state['run_id']}/answer"
    resumed = await client.post(url, json=answer)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()['status'] == 'completed'
    assert resumed.json()['analysis'] == state['analysis']
    assert len(calls) == 2
    assert (await client.post(url, json=answer)).json() == resumed.json()
    assert len(calls) == 2


async def test_default_full_flow_reviews_rates_then_optimizes_only_after_user_choice(harness):
    from tests.test_optimization_execution import proposal, provider_action, review
    from tests.test_optimization_runs import request
    from tests.test_room_assessment import photo_assessment
    from tests.test_visual_review import review_action

    app, client, calls, actions = harness
    app.state.settings.agent_deterministic_routing = True
    app.state.settings.visual_review_enabled = True
    app.state.settings.room_assessment_enabled = True
    runs, owner, uploaded = await prepared(harness)
    actions.extend([photo, scene_action(), review_action(), photo_assessment()])
    initial = await client.post('/api/agent-runs', json={'input_id': uploaded['input_id'], 'request_id': str(uuid4())})
    assert initial.status_code == 200, initial.text
    source = AgentState.model_validate_json(initial.text)
    assert source.status == 'completed' and source.room_assessment.aesthetics.score == 7
    ledger = app.state.analysis_service.analyzer.usage_records
    assert [r.operation for r in ledger] == ['analysis', 'scene_proposal', 'visual_review', 'room_assessment']
    assert len(calls) == source.usage.model_calls == 4

    path = f'/api/agent-runs/{source.run_id}/optimizations'
    body = request('aesthetics')
    child = await client.post(path, json=body)
    assert child.status_code == 200, child.text
    assert len(calls) == 4  # Preparation is free; improvement is a separate input.
    actions.extend([provider_action(proposal()), review()])
    execute = f"{path}/{child.json()['run_id']}/execute"
    improved = await client.post(execute, json={})
    assert improved.status_code == 200, improved.text
    assert improved.json()['status'] == 'completed'
    assert [r.operation for r in ledger][-2:] == ['optimization_proposal', 'optimization_review']
    assert [r.model for r in ledger][-2:] == ['gpt-6.1-terra', 'gpt-6-luna']
    assert len(calls) == 6 and not actions
    assert (await runs.read(source.run_id, owner)) == source
    assert (await client.post(path, json=body)).json() == improved.json()
    assert (await client.post(execute, json={})).json() == improved.json()
    assert len(calls) == 6


async def test_economical_graph_keeps_failure_terminal_instead_of_repeating_paid_work(harness):
    app, client, calls, actions = harness
    app.state.settings.agent_deterministic_routing = True
    _, _, uploaded = await prepared(harness)
    async def timeout(request, payload): raise httpx.ReadTimeout('PRIVATE', request=request)
    actions.append(timeout)
    body = {'input_id': uploaded['input_id'], 'request_id': str(uuid4())}
    response = await client.post('/api/agent-runs', json=body)
    assert response.json()['status'] == 'failed'
    assert len(calls) == 1
    assert (await client.post('/api/agent-runs', json=body)).json() == response.json()
    assert len(calls) == 1


async def test_explicit_retry_after_construction_timeout_reuses_analysis_and_remaining_attempt(harness):
    app, client, calls, actions = harness
    app.state.settings.agent_deterministic_routing = True
    _, _, uploaded = await prepared(harness)
    async def timeout(request, payload):
        raise httpx.ReadTimeout('PRIVATE', request=request)
    actions.extend([photo, timeout])
    request = {'input_id': uploaded['input_id'], 'request_id': str(uuid4())}
    first = await client.post('/api/agent-runs', json=request)
    assert first.json()['status'] == 'failed'
    assert first.json()['tool_feedback'][-1]['details']['code'] == 'analysis_timeout'
    assert (await client.post('/api/agent-runs', json=request)).json() == first.json()
    assert len(calls) == 2
    actions.append(scene_action())
    second = await client.post('/api/agent-runs', json={**request, 'request_id': str(uuid4())})
    assert second.json()['status'] == 'completed'
    assert second.json()['analysis'] == first.json()['analysis']
    assert [record.operation for record in app.state.analysis_service.analyzer.usage_records] == ['analysis', 'scene_proposal', 'scene_repair']
    assert len(calls) == 3


def photo_observations():
    ids = [str(uuid4()) for _ in range(5)]
    objects = [SimpleNamespace(source_image_ids=[key], span=None) for key in ids[:4]]
    return SimpleNamespace(image_ids=ids, walls=[], objects=objects, openings=[], relationships=[], finishes=None)


def test_photo_selection_retains_unique_views_and_honors_repair_sources():
    observations = photo_observations()
    chosen = select_photo_ids(observations, target=3)
    assert chosen == observations.image_ids[:4]  # Four unique views beat a three-view target.
    assert select_photo_ids(observations, target=3, mandatory=[observations.image_ids[-1]]) == observations.image_ids
    with pytest.raises(ValueError): select_photo_ids(observations, mandatory=['foreign'])
    observations.objects[0].source_image_ids = []
    assert select_photo_ids(observations) == observations.image_ids


def test_selected_photo_bytes_stay_aligned_with_aliases_and_mandatory_repair_evidence():
    analysis = source()
    analysis.observations.image_ids.extend(str(uuid4()) for _ in range(4))
    images = [f'normalized-photo-{i}'.encode() for i in range(len(analysis.observations.image_ids))]
    last = analysis.observations.image_ids[-1]
    payload = build_proposal_request(Settings(_env_file=None), analysis, measurements(analysis), None, [],
        images, mandatory_image_ids=[last])
    content = payload['input'][0]['content']
    data = json.loads(content[0]['text'])
    chosen = photo_references(data, analysis.observations.image_ids, decode=True)['photo_selection']['attached_image_ids']
    assert last in chosen and len(chosen) == 3
    for index, photo_id in enumerate(chosen):
        source_index = analysis.observations.image_ids.index(photo_id)
        assert content[1 + 2*index]['text'] == f'Photo evidence ID: photo_{source_index+1}'
        assert b64decode(content[2 + 2*index]['image_url'].split(',', 1)[1]) == images[source_index]


@pytest.mark.parametrize('model,mode', [('gpt-6-luna', 'implicit'), ('gpt-4.1-mini', None)])
async def test_cache_policy_is_configurable_and_older_models_keep_supported_parameters(harness, model, mode):
    app, client, calls, actions = harness
    app.state.settings.analysis_model_name = model
    app.state.settings.model_prompt_cache_mode = 'implicit'
    uploaded = await api.upload(client)
    response = await client.post('/api/rooms/analyze', json={'input_id': uploaded['input_id']})
    assert response.status_code == 200, response.text
    assert calls[0].get('prompt_cache_options') == ({'mode': mode} if mode else None)
    record = app.state.analysis_service.analyzer.usage_records[-1]
    assert record.prompt_cache_mode == mode and record.cache_write_tokens is None


@pytest.mark.parametrize('name', ['synthetic_study', 'living_room_tv'])
def test_compaction_preserves_regression_inventory_relations_supports_and_measurements(name):
    from tests.test_reconstruction_evaluation import load
    _, analysis, _ = load(name)
    original = analysis.model_dump()
    request = build_proposal_request(Settings(_env_file=None), analysis, measurements(analysis), None, [])
    data = json.loads(request['input'][0]['content'][0]['text'])
    data = photo_references(data, analysis.observations.image_ids, decode=True)
    assert {o['id'] for o in data['observations']['objects']} == {o.id for o in analysis.observations.objects}
    assert len(data['observations']['relationships']) == len(analysis.observations.relationships)
    for obj in analysis.observations.objects:
        copy = next(item for item in data['observations']['objects'] if item['id'] == obj.id)
        assert copy.get('support_object_id') == obj.support_object_id
        assert copy.get('wall_id') == obj.wall_id
        assert copy['source_image_ids'] == obj.source_image_ids
    assert analysis.model_dump() == original
    for binding in data['asset_bindings']:
        assert all(m['asset_id'] in data['asset_models'] for m in binding['models'])
    for record in chain(data['observations']['walls'], data['observations']['objects'], data['observations']['openings'], data['observations']['relationships']):
        assert set(record['source_image_ids']) & set(select_photo_ids(analysis.observations))


def test_repair_stays_available_for_missing_major_items_but_not_only_unrepresentable_details():
    analysis = source(); draft = SceneDraft.model_validate(draft_data())
    assert actionable_repair(analysis, draft.model_copy(update={'objects': draft.objects[1:]}),
        [issue('identity_mismatch', 'Missing bed.', draft.objects[0].id)])
    missing = analysis.observations.objects[0].model_copy(update={'id': 'detail', 'type': 'wardrobe',
        'placement': 'wall', 'placement_confidence': 'high', 'alternative_type': None, 'appears_fixed': True})
    analysis.observations.objects.append(missing)
    assert not actionable_repair(analysis, draft, [issue('identity_mismatch', 'Unsupported door leaf.', 'detail')])
    assert actionable_repair(analysis, draft, [issue('geometry', 'A known chair is outside.', 'chair_a')])


def test_supported_host_surfaces_are_retained_and_catalog_models_are_shared():
    analysis = source()
    data = json.loads(build_proposal_request(Settings(_env_file=None), analysis, measurements(analysis), None, [])['input'][0]['content'][0]['text'])
    desk = next(item for item in data['asset_bindings'] if item['id'] == 'desk_observed')
    assert any(config['support_surfaces'] for entry in desk['models']
               for config in data['asset_models'][entry['asset_id']]['configurations'].values())
    assert data['room_dimensions'] == measurements(analysis).dimensions.model_dump(mode='json')


def test_short_photo_references_round_trip_without_changing_object_ids_or_foreign_refs():
    ids = [str(uuid4()), str(uuid4())]
    original = {'id': 'photo_1', 'source_image_ids': ids, 'notes': ['photo_1'],
                'objects': [{'id': ids[0], 'source_image_ids': [ids[1]]}]}
    encoded = photo_references(original, ids)
    assert encoded['source_image_ids'] == ['photo_1', 'photo_2']
    assert encoded['id'] == 'photo_1' and encoded['objects'][0]['id'] == ids[0]
    assert photo_references(encoded, ids, decode=True) == original
    assert photo_references({'source_image_ids': ['photo_99']}, ids, decode=True)['source_image_ids'] == ['photo_99']


@pytest.mark.parametrize('foreign', [False, True])
def test_photo_aliases_restore_original_ids_before_correction_validation(foreign):
    from roometry.vision.draft_validation import parse_scene_draft
    from roometry.vision.proposals import assemble

    analysis = source()
    original = analysis.model_dump()
    obj = next(o for o in analysis.observations.objects if o.id == 'chair_a')
    data = draft_data()
    alias = f'photo_{analysis.observations.image_ids.index(obj.source_image_ids[0])+1}'
    data['appearance_corrections'] = [{'entity_id': obj.id, 'field': 'color',
        'previous_value': obj.color, 'revised_value': '#28558A',
        'source_image_ids': ['photo_99' if foreign else alias],
        'reason': 'Restore the blue seat shown in the original source view.'}]
    draft = parse_scene_draft(data, analysis)
    scene, validation, issues = assemble(analysis, measurements(analysis), draft)
    # Foreign evidence remains invalid, even when it resembles a short alias.
    if foreign:
        assert scene is None and issues
        assert analysis.model_dump() == original
        return
    assert scene is not None and validation.valid
    chair = next(o for o in scene.objects if o.id == obj.id)
    assert chair.color == '#28558A'
    assert scene.appearance_corrections[0].source_image_ids == obj.source_image_ids[:1]
    assert analysis.model_dump() == original


def test_already_satisfied_material_target_does_not_consume_paid_repair():
    from roometry.vision.proposals import assemble
    from roometry.vision.review_policy import select_findings
    from roometry.vision.review_schemas import ReviewJudgment
    from tests.test_review_quality import finding

    analysis = source()
    scene, _, _ = assemble(analysis, measurements(analysis), SceneDraft.model_validate(draft_data()))
    current = next(o for o in scene.objects if o.id == 'chair_a').color
    data = {'verdict': 'needs_correction', 'summary': 'The chair material was flagged.', 'limitations': [],
        'findings': [finding('color', 'chair_a', appearance={'field': 'color', 'value': current})]}
    review = ReviewJudgment.model_validate(data)
    assert select_findings(analysis, review, scene) == []
    review.findings[0].appearance_target.value = '#FF00FF'
    assert len(select_findings(analysis, review, scene)) == 1
