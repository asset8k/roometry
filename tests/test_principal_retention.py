"""Preserve uncertain photo evidence and functional fixtures without extra calls."""

from copy import deepcopy
import json

import httpx
import pytest

from roometry.agents.clarification import answer_updates, question_for
from roometry.agents.state import AgentState, ClarificationAnswer, ClarificationChoice
from roometry.scenes.catalog import ASSET_CATALOG, object_surfaces
from roometry.scenes.validation import validate_geometry
from roometry.vision.analyzer import _normalize_observations
from roometry.vision.assembly_context import assembly_features
from roometry.vision.catalog import bind_catalog
from roometry.vision.evidence import apply_constraints
from roometry.vision.features import required_features, retains_features
from roometry.vision.importance import importance
from roometry.vision.proposal_request import build_proposal_request
from roometry.vision.proposal_schemas import SceneDraft
from roometry.vision.proposals import assemble, preflight
from roometry.vision.review_policy import review_targets, select_findings
from roometry.vision.review_schemas import ReviewJudgment
from roometry.vision.review_validation import parse_judgment
from roometry.vision.schemas import ReconstructionConstraints
from roometry.config import Settings
from tests.test_assembly_quality import case
from tests.test_scene_proposals import draft_data, measurements, scene_action, source
from tests.test_review_quality import finding
from tests.test_visual_review import with_checks
from tests import test_vision_api as api

anyio_backend = api.anyio_backend
isolated_environment = api.isolated_environment
harness = api.harness


def state_for(analysis):
    return AgentState(run_id=analysis.analysis_id, input_id=analysis.input_id,
        image_ids=analysis.observations.image_ids, input_options=analysis.input_options,
        created_at=analysis.created_at, input_expires_at=analysis.expires_at,
        expires_at=analysis.expires_at, analysis=analysis)


def answer_identity(analysis, key, choice, legacy=False):
    state = state_for(analysis)
    question = question_for(state, "object_identity", key)
    if legacy:
        question = question.model_copy(update={"choices": [*question.choices[:2],
            ClarificationChoice(id="leave_unknown", label="Neither / not sure — leave this item unresolved")]})
    state = state.model_copy(update={"pending_question": question, "questions": [question]})
    updates = answer_updates(state, ClarificationAnswer(question_id=question.id, choice_id=choice))
    return ReconstructionConstraints.model_validate(updates['reconstruction_constraints'])


@pytest.mark.parametrize("primary", ["nightstand", None])
def test_approximation_retains_photo_hypothesis_and_its_supported_lamp(primary):
    analysis, draft = case([
        ("bedside", "nightstand", "Photo-backed bedside furniture", None, "floor", (1., 0., 1.), None, None),
        ("lamp", "table_lamp", "Bedside lamp", None, "surface", (1., .6, 1.), None, "bedside"),
    ])
    obj = analysis.observations.objects[0]
    obj.type, obj.alternative_type = primary, "stool"
    # When the primary is unclassified, the only recorded hypothesis is used.
    if primary is None:
        obj.alternative_type = "nightstand"
    before = analysis.model_dump()
    constraints = answer_identity(analysis, obj.id, "use_estimate")
    assert constraints.object_types == {} and constraints.approximate_object_ids == [obj.id]
    effective = apply_constraints(analysis, constraints)
    chosen = effective.observations.objects[0]
    assert chosen.type == "nightstand"
    assert any("unconfirmed hypotheses" in note for note in chosen.notes)
    assert not any("User confirmed category" in note for note in chosen.notes)
    result, report, issues = assemble(effective, measurements(effective), draft)
    assert result and report.valid, issues
    assert {item.id for item in result.objects} == {"bedside", "lamp"}
    assert next(item for item in result.objects if item.id == "lamp").support_object_id == "bedside"
    assert analysis.model_dump() == before


def test_historical_neither_is_still_an_exclusion_and_confirmed_choices_still_win():
    analysis = source()
    obj = analysis.observations.objects[0]
    obj.alternative_type = "sofa"
    exclusion = answer_identity(analysis, obj.id, "leave_unknown", legacy=True)
    assert exclusion.object_types == {obj.id: None}
    excluded = apply_constraints(analysis, exclusion)
    assert excluded.observations.objects[0].type is None
    assert any(obj.id in issue.entity_ids for issue in preflight(excluded))
    for choice, expected in [("use_primary", "bed"), ("use_alternative", "sofa")]:
        effective = apply_constraints(analysis, answer_identity(analysis, obj.id, choice))
        assert effective.observations.objects[0].type == expected
        assert effective.observations.objects[0].alternative_type is None
        assert any("User confirmed category" in note for note in effective.observations.objects[0].notes)
    with pytest.raises(ValueError, match="confirmed or excluded"):
        apply_constraints(analysis, ReconstructionConstraints(object_types={obj.id: None}, approximate_object_ids=[obj.id]))


@pytest.mark.parametrize("primary,alternative", [("nightstand", "side_table"), ("bench", "ottoman"), ("monitor", "computer")])
def test_equivalent_categories_do_not_consume_a_clarification(primary, alternative):
    analysis = source()
    obj = analysis.observations.objects[0]
    obj.type, obj.alternative_type = primary, alternative
    with pytest.raises(ValueError, match="Equivalent functional"):
        question_for(state_for(analysis), "object_identity", obj.id)


@pytest.mark.parametrize("label", ["Corner base cabinets with sink and faucet", "Sink cabinet and worktop with basin and faucet"])
def test_sink_cabinet_is_canonicalized_and_binds_to_real_basin_and_faucet(label):
    analysis, draft = case([("sink_base", "kitchen_cabinet", label, None, "floor", (1.5, 0., 1.2), None, None)])
    before = analysis.model_dump()
    raw = analysis.observations.model_dump(mode="json")
    normalized = _normalize_observations(raw, raw['image_ids'])
    assert normalized['objects'][0]['type'] == 'kitchen_counter'
    assert normalized['objects'][0]['source_image_ids'] == raw['objects'][0]['source_image_ids']
    assert raw['objects'][0]['type'] == 'kitchen_cabinet'
    # Cached observations receive the same correction on a copy, not a rewrite.
    effective = apply_constraints(analysis, ReconstructionConstraints())
    selected = effective.observations.objects[0]
    assert required_features(selected) == {'basin', 'faucet'}
    assert any('fitted kitchen_counter' in note for note in selected.notes)
    bound, errors = bind_catalog(effective, draft)
    assert bound and not errors
    assert {'basin','faucet'} <= set(assembly_features(ASSET_CATALOG[bound.objects[0].asset_id]))
    assert analysis.model_dump() == before
    request = build_proposal_request(Settings(_env_file=None), effective, measurements(effective), None, [])
    context = json.loads(request['input'][0]['content'][0]['text'])
    assert set(context['asset_bindings'][0]['required_components']) == {'basin','faucet'}
    assert all({'basin','faucet'} <= set(m['included_components']) for m in context['asset_models'].values())


@pytest.mark.parametrize("label", ["Cabinet beside separate sink", "Cabinet without sink", "Wall cupboard above sink"])
def test_relative_or_negated_sink_mentions_do_not_invent_fitted_components(label):
    analysis, _ = case([("cabinet", "kitchen_cabinet", label, None, "floor", (1.,0.,1.), None,None)])
    effective = apply_constraints(analysis, ReconstructionConstraints())
    assert effective.observations.objects[0].type == 'kitchen_cabinet'
    assert not required_features(effective.observations.objects[0])


def test_surface_hob_is_a_supported_thin_component_without_a_second_oven_or_cabinet():
    analysis, draft = case([
        ("counter", "kitchen_counter", "Plain kitchen worktop counter", "kitchen_counter_standard_01", "floor", (1.5,0.,1.5), None,None),
        ("hob", "cooker", "Built-in cooktop", None, "surface", (1.5,.92,1.5), None,"counter"),
    ])
    assert not preflight(analysis)
    assert required_features(analysis.observations.objects[1]) == {'cooktop'}
    assert not retains_features(ASSET_CATALOG['cooker_standard_01'], analysis.observations.objects[1])
    result, report, issues = assemble(analysis, measurements(analysis), draft)
    assert result and report.valid, issues
    hob = next(obj for obj in result.objects if obj.id == 'hob')
    assert hob.asset_id == 'cooker_inset_hob_01' and hob.placement == 'surface'
    assert hob.support_object_id == 'counter' and hob.dimensions_m.height < .05
    assert hob.position.y == pytest.approx(.92)
    assert not any('oven' in part.name or 'cabinet' in part.name for part in ASSET_CATALOG[hob.asset_id].parts)
    assert not object_surfaces(hob.asset_id, 'surface')
    assert validate_geometry(result).valid


def scene_and_context():
    analysis = source()
    result, _, _ = assemble(analysis, measurements(analysis), SceneDraft.model_validate(draft_data()))
    result.objects = [obj for obj in result.objects if obj.id != 'bed_observed']
    targets, _ = review_targets(analysis, result)
    return analysis, result, {'observations':analysis.observations.model_dump(mode='json'), 'review_targets': targets, 'scene':result.model_dump(mode='json')}


def test_absent_object_appearance_na_keeps_valid_absence_even_with_an_invalid_color_finding():
    analysis, scene, context = scene_and_context()
    absent = finding('missing', 'bed_observed')
    invalid_color = finding('color','desk_observed',appearance={'field':'wood','value':'brown wood'})
    data = with_checks({'verdict':'needs_correction','summary':'Bed absent.','limitations':[], 'findings':[absent, invalid_color]}, context)
    next(item for item in data['checks'] if item['entity_id']=='bed_observed')['appearance']='not_applicable'
    judgment = parse_judgment(data, analysis, scene, require_comparisons=True)
    check = next(item for item in judgment.checks if item.entity_id == 'bed_observed')
    assert check.placement == 'difference' and check.appearance == 'not_applicable'
    selected = select_findings(analysis, judgment, scene)
    assert selected and selected[0][1].kind == 'missing'
    assert selected[0][1].entity_ids == ['bed_observed']
    assert judgment.limitations  # The malformed color finding is still disclosed.


def test_retained_object_cannot_use_na_appearance_to_hide_its_color_check():
    analysis, scene, context = scene_and_context()
    data = with_checks({'verdict':'needs_correction','summary':'Bed absent.','limitations':[], 'findings':[finding('missing','bed_observed')]}, context)
    next(item for item in data['checks'] if item['entity_id']=='desk_observed')['appearance']='not_applicable'
    judgment = parse_judgment(data, analysis, scene)
    check = next(item for item in judgment.checks if item.entity_id == 'desk_observed')
    assert check.appearance == check.placement == 'uncertain'
    assert any(item.kind == 'missing' and item.repairable for _,item in select_findings(analysis, judgment, scene))


def test_missing_essential_gets_repair_slot_before_colors_and_fixed_decor():
    analysis, scene, _ = scene_and_context()
    bed = analysis.observations.objects[0]
    decor = bed.model_copy(update={'id':'art','type':'picture','appears_fixed':True})
    assert importance(decor) > importance(bed)
    for kind in ['computer','monitor','laptop','table_lamp']:
        assert importance(bed.model_copy(update={'type':kind,'appears_fixed':False})) <= 1
    colors = [finding('color','desk_observed',appearance={'field':'wood','value':'#887766'}) for _ in range(11)]
    judgment = ReviewJudgment.model_validate({'verdict':'needs_correction','summary':'Bed absent and colors differ.',
        'findings':[*colors,finding('missing','bed_observed')],'limitations':[]})
    selected = select_findings(analysis, judgment, scene)
    assert len(selected) <= 6 and selected[0][0] == 11
    targets, _ = review_targets(analysis, scene)
    assert 'computer_observed' in {item['entity_id'] for item in targets}


def test_grouped_placement_finding_is_split_with_each_subjects_photo_evidence():
    analysis = source()
    scene, _, _ = assemble(analysis, measurements(analysis), SceneDraft.model_validate(draft_data()))
    targets, _ = review_targets(analysis, scene)
    group = finding('position','desk_observed',target={'relation':'near','target_id':'window_observed'})
    group['entity_ids']=['desk_observed','chair_a']
    data = with_checks({'verdict':'needs_correction','summary':'Furniture misplaced.','limitations':[], 'findings':[group]},
        {'observations':analysis.observations.model_dump(mode='json'),'review_targets':targets,'scene':scene.model_dump(mode='json')})
    judgment = parse_judgment(data, analysis, scene)
    individual = [item for item in judgment.findings if item.kind=='position']
    assert {tuple(item.entity_ids) for item in individual} == {('desk_observed',),('chair_a',)}
    assert all(item.repairable for item in individual)
    for item in individual:
        observation = next(obj for obj in analysis.observations.objects if obj.id == item.entity_ids[0])
        assert set(item.source_image_ids) <= set(observation.source_image_ids)


@pytest.mark.anyio
async def test_new_approximate_answer_resumes_langgraph_once_and_keeps_source_analysis(harness):
    from tests.test_agent_tools import prepared, decision
    app, client, calls, actions = harness
    runs, owner, uploaded = await prepared(harness)
    async def uncertain(request, payload):
        body = api.provider_response(payload)
        observations = json.loads(body['output'][0]['content'][0]['text'])
        observations['objects'][0]['alternative_type'] = 'sofa'
        body['output'][0]['content'][0]['text'] = json.dumps(observations)
        return httpx.Response(200, json=body)
    actions.extend([decision('analyze_photos'), uncertain,
        decision('request_clarification',{'kind':'object_identity','object_id':'bed_observed'})])
    initial = await runs.create(uploaded['input_id'], owner)
    paused = await runs.execute(initial.run_id, owner)
    assert paused.status == 'awaiting_answer'
    assert paused.pending_question.choices[-1].id == 'use_estimate'
    original = paused.analysis.model_dump()
    actions.extend([decision('propose_scene'), scene_action(), decision('finish')])
    body = {'question_id':paused.pending_question.id,'choice_id':'use_estimate'}
    response = await client.post(f'/api/agent-runs/{initial.run_id}/answer',json=body)
    assert response.status_code == 200, response.text
    result = AgentState.model_validate_json(response.text)
    assert result.status == 'completed' and result.analysis.model_dump() == original
    assert result.reconstruction_constraints.approximate_object_ids == ['bed_observed']
    assert 'bed_observed' in {obj.id for obj in result.base_scene.objects}
    assert sum(record.operation=='analysis' for record in app.state.analysis_service.analyzer.usage_records) == 1
    count = len(calls)
    assert (await client.post(f'/api/agent-runs/{initial.run_id}/answer',json=body)).json() == result.model_dump(mode='json')
    assert len(calls) == count == 6


@pytest.mark.parametrize('label,features', [
    ('Sink cabinet without faucet', {'basin'}),
    ('Cabinet with sink and faucet, no upper cupboard', {'basin','faucet'}),
    ('Cabinet without sink but with faucet', {'faucet'}),
    ('Cabinet next to a separate sink', set()),
    ('Cabinet with sink near the window', {'basin'}),
])
def test_component_negation_does_not_erase_other_positive_features(label, features):
    analysis = source()
    item = analysis.observations.objects[0].model_copy(update={'type':'kitchen_cabinet','label':label,'notes':[]})
    assert required_features(item) == features


def test_hob_without_oven_stays_a_hob_instead_of_a_range():
    analysis = source()
    item = analysis.observations.objects[0].model_copy(update={'type':'cooker','label':'Built-in cooktop without oven',
        'placement':'surface','notes':[]})
    assert required_features(item) == {'cooktop'}
    assert retains_features(ASSET_CATALOG['cooker_inset_hob_01'], item)
    assert not retains_features(ASSET_CATALOG['cooker_standard_01'], item)
