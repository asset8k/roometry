"""Dimension consent, provenance and actual graph resumption; all model HTTP is mocked."""

import json
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image
from pydantic import ValidationError

from roometry.config import PROJECT_ENV_FILE, Settings
from roometry.uploads.schemas import InputDimensions
from roometry.vision.proposal_schemas import SceneProposalResponse
from roometry.vision.proposals import resolve_measurements
from roometry.vision.schemas import EstimatedRoomDimensions
from tests import test_vision_api
from tests.test_agent_clarification import paused_dimensions
from tests.test_agent_tools import photo, prepared
from tests.test_scene_proposals import draft_data, scene_action, source

anyio_backend = test_vision_api.anyio_backend
isolated_environment = test_vision_api.isolated_environment
harness = test_vision_api.harness


def test_resolution_uses_confirmed_then_photo_then_disclosed_assumptions():
    analysis = source()
    analysis.input_options.dimensions = InputDimensions(width_m=5., measurements_source='user_confirmed')
    analysis.observations.estimated_dimensions = EstimatedRoomDimensions(width_m=8., length_m=6., height_m=None)
    before = analysis.model_dump()
    known = {'height_m': 2.8}
    resolved = resolve_measurements(analysis, known, True, assume_missing=True)
    assert resolved.dimensions.width_m == 5. and resolved.dimensions.length_m == 6.
    assert resolved.dimensions.height_m == 2.8
    assert resolved.estimated == ['length_m'] and resolved.assumed == [] and resolved.to_confirm == []
    assert analysis.model_dump() == before and known == {'height_m': 2.8}
    fallback = resolve_measurements(analysis, {}, True, assume_missing=True)
    assert fallback.dimensions.height_m == 2.6
    assert fallback.assumed == ['height_m'] and fallback.estimated == ['length_m', 'height_m']
    assert 'not a photo measurement' in fallback.assumption_notes[0]


@pytest.mark.parametrize('allow_estimates,consent', [(True, False), (False, True), (False, False)])
def test_missing_sizes_are_not_manufactured_without_estimation_consent(allow_estimates, consent):
    analysis = source()
    analysis.input_options.dimensions = None
    analysis.observations.estimated_dimensions = None
    result = resolve_measurements(analysis, {}, allow_estimates, assume_missing=consent)
    assert result.to_confirm == ['width_m', 'length_m', 'height_m']
    assert result.assumed == [] and result.estimated == []
    assert result.dimensions.width_m is None


@pytest.mark.anyio
@pytest.mark.parametrize('known_width', [True, False])
async def test_default_graph_unknown_measurements_builds_a_labeled_scene_without_reanalysis(harness, known_width):
    app, client, calls, actions = harness
    app.state.settings.agent_deterministic_routing = True
    _, _, uploaded = await prepared(harness, complete=False)
    if not known_width:
        # Same photo with no user measurements at all.
        data = BytesIO()
        with Image.new('RGB', (256, 256), 'gray') as image:
            image.save(data, 'PNG')
        response = await client.post('/api/rooms/inputs', files=[
            ('options', (None, json.dumps({'room_type': 'bedroom'}))),
            ('images', ('room.png', data.getvalue(), 'image/png')),
        ])
        assert response.status_code == 201
        uploaded = response.json()
    actions.append(photo)
    request = {'input_id': uploaded['input_id'], 'request_id': str(uuid4())}
    paused = await client.post('/api/agent-runs', json=request)
    assert paused.status_code == 200 and paused.json()['status'] == 'awaiting_answer', paused.text
    initial = paused.json()
    question = initial['pending_question']
    assert question['kind'] == 'room_dimensions'
    assert next(c for c in question['choices'] if c['id'] == 'leave_unknown')['label'] == 'Use estimated dimensions'
    assert len(calls) == 1
    actions.append(scene_action())
    answer = {'question_id': question['id'], 'choice_id': 'leave_unknown'}
    path = f"/api/agent-runs/{initial['run_id']}/answer"
    result = await client.post(path, json=answer)
    assert result.status_code == 200, result.text
    final = result.json()
    assert final['status'] == 'completed', final
    assert final['analysis'] == initial['analysis']
    assert final['confirmed_dimensions'] == initial['confirmed_dimensions']
    axes = ['length_m', 'height_m'] if known_width else ['width_m', 'length_m', 'height_m']
    assert final['proposal']['estimated_axes'] == final['proposal']['assumed_axes'] == axes
    assert final['working_scene']['room']['measurements_source'] == 'estimated'
    assert final['working_scene']['room']['width_m'] == 4.
    assert final['working_scene']['room']['length_m'] == 4.
    assert final['working_scene']['room']['height_m'] == 2.6
    assert sum(note.startswith('Assumed room ') for note in final['working_scene']['assumptions']) == len(axes)
    context = json.loads(calls[-1]['input'][0]['content'][0]['text'])
    assert context['assumed_axes'] == axes
    assert len(calls) == final['usage']['model_calls'] == 2
    assert (await client.post(path, json=answer)).json() == final
    assert (await client.post('/api/agent-runs', json=request)).json() == final
    assert len(calls) == 2
    # Structured provenance cannot silently promote a confirmed axis to assumed.
    corrupted = {**final['proposal'], 'estimated_axes': []}
    with pytest.raises(ValidationError):
        SceneProposalResponse.model_validate_json(json.dumps(corrupted))


@pytest.mark.anyio
async def test_photo_estimates_build_directly_and_never_override_a_user_axis(harness, monkeypatch):
    app, client, calls, actions = harness
    app.state.settings.agent_deterministic_routing = True
    observations_for = test_vision_api.observations_for
    def observed_sizes(payload):
        observed = observations_for(payload)
        observed['estimated_dimensions'] = {'width_m': 8., 'length_m': 4., 'height_m': 2.6}
        return observed
    monkeypatch.setattr(test_vision_api, 'observations_for', observed_sizes)
    _, _, uploaded = await prepared(harness, complete=False)  # Confirmed width: 4 m.
    actions.extend([photo, scene_action()])
    response = await client.post('/api/agent-runs', json={'input_id': uploaded['input_id'], 'request_id': str(uuid4())})
    assert response.status_code == 200, response.text
    final = response.json()
    assert final['status'] == 'completed' and final['questions'] == []
    assert final['analysis']['observations']['estimated_dimensions']['width_m'] == 8.
    assert final['working_scene']['room']['width_m'] == 4.
    assert final['confirmed_dimensions']['width_m'] == 4.
    assert final['proposal']['estimated_axes'] == ['length_m', 'height_m']
    assert final['proposal']['assumed_axes'] == []
    assert not any(note.startswith('Assumed room ') for note in final['working_scene']['assumptions'])
    assert len(calls) == 2


@pytest.mark.anyio
async def test_visible_wall_scale_resumes_unknown_height_without_reanalysis(harness, monkeypatch):
    app, client, calls, actions = harness
    app.state.settings.agent_deterministic_routing = True
    observations_for = test_vision_api.observations_for

    def observed_scale(payload):
        observed = observations_for(payload)
        observed['estimated_dimensions'] = None
        for wall, role in zip(observed['walls'], ('shorter', 'longer')):
            wall['span'] = {'role': role, 'confidence': 'high',
                            'source_image_ids': wall['source_image_ids'],
                            'evidence': 'Full wall extents are visible across the supplied views.'}
        for opening, width, coverage in zip(observed['openings'], (2.4, 1.2), (.6, .2)):
            opening.update(confidence='high', estimated_width_m=width, wall_coverage=coverage)
        return observed

    monkeypatch.setattr(test_vision_api, 'observations_for', observed_scale)
    data = BytesIO()
    with Image.new('RGB', (256, 256), 'gray') as image:
        image.save(data, 'PNG')
    response = await client.post('/api/rooms/inputs', files=[
        ('options', (None, json.dumps({'room_type': 'bedroom'}))),
        ('images', ('room.png', data.getvalue(), 'image/png')),
    ])
    assert response.status_code == 201
    actions.append(photo)
    paused = await client.post('/api/agent-runs', json={
        'input_id': response.json()['input_id'], 'request_id': str(uuid4()),
    })
    assert paused.status_code == 200, paused.text
    initial = paused.json()
    question = initial['pending_question']
    assert question['kind'] == 'room_dimensions' and question['dimension_axes'] == ['height_m']
    assert len(calls) == 1
    draft = draft_data()
    for opening, width in zip(draft['openings'], (2.4, 1.2)):
        opening['width_m'] = width
    actions.append(scene_action(draft))
    path = f"/api/agent-runs/{initial['run_id']}/answer"
    answer = {'question_id': question['id'], 'choice_id': 'leave_unknown'}
    resumed = await client.post(path, json=answer)
    assert resumed.status_code == 200, resumed.text
    final = resumed.json()
    assert final['status'] == 'completed', final
    assert final['analysis'] == initial['analysis']
    assert final['proposal']['assumed_axes'] == ['height_m']
    assert final['working_scene']['room']['width_m'] == 4.
    assert final['working_scene']['room']['length_m'] == 6.
    assert final['working_scene']['room']['height_m'] == 2.6
    context = json.loads(calls[-1]['input'][0]['content'][0]['text'])
    assert len(context['dimension_estimate_evidence']) == 2
    assert any('full-wall coverage' in note for note in final['working_scene']['assumptions'])
    assert (await client.post(path, json=answer)).json() == final
    assert len(calls) == 2


@pytest.mark.anyio
async def test_estimation_resumes_an_older_question_with_the_deterministic_router(harness):
    app, client, calls, actions = harness
    _, _, _, paused = await paused_dimensions(harness)
    assert paused['proposal']['status'] == 'needs_dimensions' and len(calls) == 3
    app.state.settings.agent_deterministic_routing = True
    actions.append(scene_action())
    response = await client.post(f"/api/agent-runs/{paused['run_id']}/answer", json={
        'question_id': 'q1', 'choice_id': 'leave_unknown',
    })
    assert response.status_code == 200 and response.json()['status'] == 'completed', response.text
    assert response.json()['analysis'] == paused['analysis']
    assert response.json()['proposal']['assumed_axes'] == ['length_m', 'height_m']
    assert len(calls) == 4


def test_settings_default_is_project_relative_and_works_from_frontend(tmp_path, monkeypatch):
    assert PROJECT_ENV_FILE == Path(__file__).resolve().parents[1] / '.env'
    assert Settings.model_config['env_file'] == PROJECT_ENV_FILE and PROJECT_ENV_FILE.is_absolute()
    root = tmp_path / 'project'
    frontend = root / 'frontend'
    frontend.mkdir(parents=True)
    env = root / '.env'
    env.write_text('ANALYSIS_ENABLED=true\nMODEL_NAME=fixture-model\n')
    (frontend / '.env').write_text('ANALYSIS_ENABLED=false\nMODEL_NAME=wrong-directory\n')
    monkeypatch.setattr(Settings, 'model_config', {**Settings.model_config, 'env_file': env})
    monkeypatch.chdir(root)
    first = Settings()
    monkeypatch.chdir(frontend)
    second = Settings()
    assert first.analysis_enabled is second.analysis_enabled is True
    assert first.model_name == second.model_name == 'fixture-model'
    monkeypatch.setenv('MODEL_NAME', 'environment-override')
    assert Settings().model_name == 'environment-override'
    assert Settings(_env_file=None).analysis_enabled is False
