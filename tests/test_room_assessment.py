"""Read-only four-card assessment, with real graph/SDK and mocked provider calls."""

import json
from copy import deepcopy
from math import pi

import anyio
import httpx
import pytest
from jsonschema import Draft202012Validator

from roometry.assessment.metrics import measure_room, with_recommendation
from roometry.assessment.schemas import AssessmentCard
from roometry.scenes.hashing import scene_hash
from roometry.scenes.scoring import score_layout
from roometry.scenes.validation import validate_geometry
from tests import test_vision_api as api
from tests.test_agent_tools import decision, prepared, stages
from tests.test_component_geometry import model
from tests.test_geometry import obj
from tests.test_layout_scoring import case

anyio_backend = api.anyio_backend
isolated_environment = api.isolated_environment
harness = api.harness
pytestmark = pytest.mark.anyio


def test_missing_workspace_and_storage_are_not_fabricated_zeroes():
    report = measure_room(case())
    assert report.workspace.status == "not_applicable" and report.workspace.score is None
    assert report.storage.status == "unassessed" and report.storage.score is None
    assert report.aesthetics.score is None
    assert report.openness.score == 10 and report.recommended is None


def test_room_partition_reduces_usable_space_without_changing_empty_floor_area():
    barrier = obj('barrier', kind='wardrobe', x=3., z=3., width=5.4, depth=.4, height=2.)
    before = case([barrier])
    after = before.model_copy(deep=True)
    after.objects[0].position.z = .25
    assert validate_geometry(before).valid and validate_geometry(after).valid
    # Identical physical footprint, allowing one sampled grid-row difference.
    assert score_layout(before).openness.score == pytest.approx(score_layout(after).openness.score, abs=.2)
    a, b = measure_room(before), measure_room(after)
    assert b.openness.score > a.openness.score
    assert a.scene_hash != b.scene_hash


def test_rotating_storage_to_face_a_wall_lowers_access_score_at_same_capacity():
    cabinet = obj('wardrobe', kind='wardrobe', x=3., z=.35, width=1.5, depth=.6, height=2.)
    clear = case([cabinet])
    blocked = clear.model_copy(deep=True)
    blocked.objects[0].rotation_y_rad = pi
    assert validate_geometry(clear).valid and validate_geometry(blocked).valid
    a, b = measure_room(clear), measure_room(blocked)
    assert a.storage.score > b.storage.score
    assert a.storage.metrics[1].value == b.storage.metrics[1].value


def test_integrated_desk_uses_the_worktop_below_its_hutch():
    desk = model('desk_shelved_01', 'desk', x=3., z=3.)
    report = score_layout(case([desk]))
    assert report.workstations[0].worktop_height_m == pytest.approx(.75)
    assert report.workspace.score > 0
    assert measure_room(case([desk])).workspace.score > 0


def test_provisional_major_inventory_never_becomes_a_recommendation():
    report = measure_room(case([obj('wardrobe', kind='wardrobe', x=3., z=.35, depth=.6, rotation=pi)]), incomplete_inventory=True)
    assert report.recommended is None
    assert report.storage.confidence == 'low'
    card = AssessmentCard(status='rated', score=2., confidence='low', source='photos', explanation='Occluded.')
    assert with_recommendation(report, aesthetics=card.model_dump()).recommended is None


def photo_assessment(*, failure=None):
    async def action(request, payload):
        assert payload['text']['format']['name'] == 'room_assessment'
        assert payload['model'] == 'gpt-6-luna' and payload['reasoning'] == {'effort': 'low'}
        assert payload['max_output_tokens'] == 1536
        assert payload['store'] is False and payload['tools'] == []
        ids = payload['text']['format']['schema']['$defs']['AestheticRubric']['properties']['source_image_ids']['items']['enum']
        fake = {'model': payload['model'], 'input': [{'content': [{'text': json.dumps({'image_ids': ids})}]}]}
        response = api.provider_response(fake)
        data = {'aesthetics': {'color_harmony': 2, 'material_cohesion': 2, 'visual_balance': 1,
            'visual_order': 1, 'lighting_composition': 1, 'confidence': 'medium',
            'explanation': 'The visible materials are coherent; the furniture grouping could be calmer.', 'source_image_ids': ids[:1]}}
        Draft202012Validator(payload['text']['format']['schema']).validate(data)
        if failure == 'timeout': raise httpx.ReadTimeout('PRIVATE_ERROR', request=request)
        if failure == 'provider': return httpx.Response(500, json={'error': {'message': 'PRIVATE_ERROR'}})
        if failure == 'incomplete':
            response.update(status='incomplete', incomplete_details={'reason': 'max_output_tokens'})
        if failure == 'foreign_photo': data['aesthetics']['source_image_ids'] = ['foreign_photo']
        if failure == 'invalid_score': data['aesthetics']['visual_balance'] = 20
        if failure == 'unknown': data['aesthetics'] = None
        response['output'][0]['content'][0]['text'] = json.dumps(data)
        if failure == 'refusal': response['output'][0]['content'] = [{'type': 'refusal', 'refusal': 'PRIVATE_ERROR'}]
        return httpx.Response(200, json=response)
    return action


async def setup(harness, failure=None):
    app, _, _, actions = harness
    app.state.settings.room_assessment_enabled = True
    runs, owner, uploaded = await prepared(harness)
    stages(actions)
    actions.extend([decision('finish'), photo_assessment(failure=failure)])
    return runs, owner, uploaded


async def test_final_scene_gets_one_cached_assessment_with_counted_usage_and_no_mutation(harness):
    app, _, calls, _ = harness
    runs, owner, uploaded = await setup(harness)
    state = await runs.start(uploaded['input_id'], owner, '77777777-7777-4777-8777-777777777777')
    assert state.status == 'completed'
    assessment = state.room_assessment
    assert assessment.aesthetics.score == 7
    assert assessment.scene_hash == scene_hash(state.working_scene)
    assert state.base_scene == state.working_scene == state.proposal.scene
    assert state.analysis == app.state.analysis_service._states[uploaded['input_id']].result
    assert state.usage.model_calls == len(calls) == 6
    assert app.state.analysis_service.analyzer.usage_records[-1].operation == 'room_assessment'
    assert (await runs.read(state.run_id, owner)) == state
    assert (await runs.execute(state.run_id, owner)) == state
    assert (await runs.start(uploaded['input_id'], owner, '77777777-7777-4777-8777-777777777777')) == state
    assert len(calls) == 6
    # A second owned reconstruction of the identical input reuses this exact
    # snapshot's assessment, rather than charging another visual assessment.
    from roometry.assessment.service import RoomAssessor
    from roometry.agents.nodes import RunContext
    async def authorize(): await runs.read(state.run_id, owner)
    context = RunContext(runs.advance, authorize, lambda: False, runs.now, owner=owner)
    assert await RoomAssessor(app.state.analysis_service)(state, context) == assessment
    assert len(calls) == 6


@pytest.mark.parametrize('failure', ['timeout', 'provider', 'incomplete', 'refusal', 'foreign_photo', 'invalid_score', 'unknown'])
async def test_visual_failure_preserves_other_cards_and_the_room_without_paid_retries(harness, failure):
    _, _, calls, _ = harness
    runs, owner, uploaded = await setup(harness, failure)
    initial = await runs.create(uploaded['input_id'], owner)
    state = await runs.execute(initial.run_id, owner)
    assert state.status == 'completed' and state.proposal.validation.valid
    assert state.room_assessment.openness.score is not None
    assert state.room_assessment.aesthetics.score is None
    assert state.base_scene == state.working_scene
    assert 'PRIVATE' not in state.model_dump_json()
    assert len(calls) == state.usage.model_calls == 6
    assert (await runs.execute(initial.run_id, owner)) == state
    assert len(calls) == 6


async def test_exhausted_model_budget_still_returns_measured_cards_without_a_new_call(harness):
    _, _, calls, _ = harness
    runs, owner, uploaded = await setup(harness)
    from roometry.agents.state import RunLimits
    initial = await runs.create(uploaded['input_id'], owner, limits=RunLimits(max_model_calls=5))
    state = await runs.execute(initial.run_id, owner)
    assert state.status == 'completed'
    assert state.room_assessment.openness.score is not None
    assert state.room_assessment.aesthetics.score is None
    assert len(calls) == 5


async def test_foreign_owner_cannot_read_assessment_or_trigger_another_call(harness):
    _, _, calls, _ = harness
    runs, owner, uploaded = await setup(harness)
    state = await runs.start(uploaded['input_id'], owner, '77777777-7777-4777-8777-777777777777')
    from roometry.agents.checkpointing import RunUnavailable
    with pytest.raises(RunUnavailable): await runs.read(state.run_id, 'foreign-owner')
    assert len(calls) == 6


async def test_assessment_reads_the_final_visually_corrected_snapshot(harness):
    from tests.test_visual_review import enabled, construction, review_action, discrepancy
    from tests.test_scene_proposals import draft_data, scene_action
    app, _, calls, actions = harness
    runs, owner, uploaded = await enabled(harness)
    app.state.settings.room_assessment_enabled = True
    construction(actions)
    fixed = deepcopy(draft_data()['objects'][0])
    fixed['rotation_y_rad'] = pi
    patch = {'wall_assignments': [], 'openings': [], 'objects': [fixed], 'assumptions': [], 'warnings': []}

    async def inspect_final(request, payload):
        context = json.loads(payload['input'][0]['content'][0]['text'])
        assert context['scene']['objects'][0]['rotation_y_rad'] == pytest.approx(pi)
        return await photo_assessment()(request, payload)

    actions.extend([review_action(discrepancy), scene_action(patch), decision('finish'), inspect_final])
    initial = await runs.create(uploaded['input_id'], owner)
    state = await runs.execute(initial.run_id, owner)
    assert state.status == 'partial' and state.proposal.visual_review.repair == 'accepted'
    assert state.room_assessment.scene_hash == scene_hash(state.working_scene)
    assert state.room_assessment.scene_hash != state.proposal.visual_review.reviewed_scene_hash
    assert state.usage.model_calls == len(calls) == 8


async def test_cancellation_during_assessment_keeps_usage_and_does_not_replay(harness):
    _, _, calls, actions = harness
    runs, owner, uploaded = await setup(harness)
    actions.pop()
    entered, release = anyio.Event(), anyio.Event()

    async def waiting(request, payload):
        entered.set()
        await release.wait()
        return await photo_assessment()(request, payload)

    actions.append(waiting)
    initial = await runs.create(uploaded['input_id'], owner)
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(runs.execute, initial.run_id, owner)
        await entered.wait()
        await runs.cancel(initial.run_id, owner)
        release.set()
    state = await runs.execute(initial.run_id, owner)
    assert state.status == 'cancelled' and state.room_assessment is None
    assert state.working_scene is not None
    assert state.usage.model_calls == len(calls) == 6
