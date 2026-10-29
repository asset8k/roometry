"""Fictional regressions for missing furniture, hidden openings and repairs."""

from copy import deepcopy
import json
from time import perf_counter

import pytest
from pydantic import TypeAdapter

from roometry.config import Settings
from roometry.scenes.schemas import RoomScene
from roometry.scenes.validation import validate_geometry
from roometry.vision.evidence import apply_constraints
from roometry.vision.importance import retention_importance
from roometry.vision.local_placement import recover_local_placements
from roometry.vision.proposal_request import build_proposal_request
from roometry.vision.proposal_schemas import SceneDraft
from roometry.vision.proposals import assemble, issue
from roometry.vision.review_policy import review_targets, select_findings
from roometry.vision.review_schemas import ReviewJudgment
from roometry.vision.schemas import ReconstructionConstraints
from roometry.vision.service import AnalysisState
from tests import test_vision_api as api
from tests.test_catalog_assembly import single_object_case
from tests.test_component_geometry import model
from tests.test_geometry import room, scene
from tests.test_scene_proposals import curtained_door_case, draft_data, measurements, scene_action, source

anyio_backend = api.anyio_backend
isolated_environment = api.isolated_environment
harness = api.harness


@pytest.mark.parametrize("asset", [None, "window_covering_curtain_01", "window_covering_sheer_01"])
def test_visible_drapes_do_not_need_an_invented_hidden_opening(asset):
    analysis, data = curtained_door_case()
    analysis.observations.openings = []
    data['openings'] = []
    data['objects'][0].update(opening_id=None, asset_id=asset)
    result, report, problems = assemble(analysis, measurements(analysis), SceneDraft.model_validate(data))
    assert result is not None and report.valid and not problems
    assert not result.openings and result.objects[0].opening_id is None
    assert any('hidden opening' in note for note in result.assumptions)
    assert RoomScene.model_validate_json(result.model_dump_json()) == result


def test_unlinked_drapes_cannot_hide_a_known_opening_or_turn_a_blind_into_a_wall_panel():
    analysis, data = curtained_door_case()
    data['objects'][0]['opening_id'] = None
    assert assemble(analysis, measurements(analysis), SceneDraft.model_validate(data))[0] is None
    analysis.observations.openings = []
    data['openings'] = []
    data['objects'][0]['asset_id'] = 'window_covering_blind_01'
    assert assemble(analysis, measurements(analysis), SceneDraft.model_validate(data))[0] is None


def test_confirmed_type_replaces_the_request_label_without_mutating_original_evidence():
    analysis = source()
    obj = analysis.observations.objects[-1]
    obj.type, obj.alternative_type, obj.label = 'drying_rack', 'chair', 'Folding clothes rack'
    before = analysis.model_dump()
    effective = apply_constraints(analysis, ReconstructionConstraints(object_types={obj.id: 'chair'}))
    selected = effective.observations.objects[-1]
    assert selected.type == 'chair' and selected.alternative_type is None
    assert selected.label == 'Chair (user confirmed)'
    assert any('User confirmed category: chair' in note for note in selected.notes)
    assert analysis.model_dump() == before


def test_bad_accessory_cannot_remove_boundary_feedback_for_failed_major_furniture():
    analysis, data = source(), draft_data()
    data['objects'][0]['position']['x'] = -.3
    unknown = analysis.observations.objects[-1].model_copy(update={'id':'unclassified', 'type':None})
    analysis.observations.objects.append(unknown)
    bad = deepcopy(data['objects'][-1]); bad['id'] = unknown.id
    data['objects'].append(bad)
    payload = build_proposal_request(Settings(), analysis, measurements(analysis), SceneDraft.model_validate(data),
        [issue('geometry', 'Outside the room.', 'bed_observed')], operation='scene_repair')
    context = json.loads(payload['input'][0]['content'][0]['text'])
    assert 'bed_observed' in context['required_object_ids']
    assert 'unclassified' not in context['required_object_ids']
    assert any(item['object_id'] == 'bed_observed' for item in context['boundary_repairs'])
    assert payload['text']['format']['schema']['$defs']['Position']['properties']['x']['maximum'] == 4


def test_targeted_visual_repair_does_not_require_every_unrelated_missing_accessory():
    analysis, data = source(), draft_data()
    data['objects'] = [obj for obj in data['objects'] if obj['id'] != 'computer_observed']
    payload = build_proposal_request(Settings(), analysis, measurements(analysis), SceneDraft.model_validate(data),
        [issue('fidelity', 'Repair this observed item.', 'bed_observed')], operation='scene_repair', repair_entity_ids={'bed_observed'})
    context = json.loads(payload['input'][0]['content'][0]['text'])
    assert context['repair']['editable_entity_ids'] == ['bed_observed']
    assert context['required_object_ids'] == ['bed_observed']
    assert payload['text']['format']['schema']['$defs']['DraftObject']['properties']['id']['enum'] == ['bed_observed']


def test_missing_major_furniture_has_models_and_can_be_restored_after_an_agreeing_check():
    analysis, data = source(), draft_data()
    original, _, _ = assemble(analysis, measurements(analysis), SceneDraft.model_validate(data))
    original.objects = [obj for obj in original.objects if obj.id != 'bed_observed']
    targets, _ = review_targets(analysis, original)
    target = next(item for item in targets if item['entity_id'] == 'bed_observed')
    assert not target['appearance_controls'] and target['restoration_models']
    judgment = ReviewJudgment.model_validate({
        'verdict':'needs_correction','summary':'The observed bed is absent.','limitations':[],
        'findings':[{'kind':'missing','entity_ids':['bed_observed'],'confidence':'high','severity':'major',
            'source_image_ids':analysis.observations.image_ids[:1], 'view_ids':['top'],
            'description':'The bed is missing.','correction':'Restore the observed bed.',
            'placement_target':None,'appearance_target':None,'repairable':False}],
        'checks':[{'entity_id':'bed_observed','placement':'difference','appearance':'uncertain',
                   'evidence':'The observed bed is absent from this scene.','source_image_ids':analysis.observations.image_ids[:1]}]})
    selected = select_findings(analysis, judgment, original)
    assert selected and selected[0][1].kind == 'missing' and selected[0][1].repairable


def test_tv_and_console_are_both_retained_by_fitting_the_actual_media_bay():
    tv = model('tv_standard_01','screen',placement='wall',x=1.4,y=1.,z=.03)
    tv.provenance, tv.fixed = 'photo_observed', True
    console = model('tv_stand_media_wall_01','console',x=2.,z=.225)
    console.provenance = 'photo_observed'
    original = scene([tv,console], dimensions=room(4.,4.,2.6))
    analysis, _ = single_object_case('tv','wall')
    template = analysis.observations.objects[0]
    analysis.observations.objects = [template.model_copy(update={'id':'screen','wall_id':None,'appears_fixed':True}),
        template.model_copy(update={'id':'console','type':'tv_stand','placement':'floor','appears_fixed':True})]
    before = original.model_dump()
    report = validate_geometry(original)
    assert {e.code for e in report.errors} == {'object_overlap'}
    result, checked = recover_local_placements(analysis, original, report)
    assert checked.valid and len(result.objects) == 2
    assert original.model_dump() == before
    assert result.objects[1] == console
    assert result.objects[0].wall == tv.wall and result.objects[0].dimensions_m == tv.dimensions_m
    assert abs(result.objects[0].position.x-tv.position.x) <= .5


def test_multiple_errors_do_not_disable_all_bounded_recovery():
    objects = [model('chair_standard_01',f'chair_{i}',x=-.1,z=1.+i) for i in range(9)]
    for obj in objects: obj.provenance = 'photo_observed'
    original = scene(objects, dimensions=room(4.,12.,2.6))
    analysis,_ = single_object_case('chair','floor')
    template = analysis.observations.objects[0]
    analysis.observations.objects = [template.model_copy(update={'id':o.id,'appears_fixed':False}) for o in objects]
    initial = validate_geometry(original)
    assert len(initial.errors) > 8
    result, revised = recover_local_placements(analysis, original, initial)
    assert len(revised.errors) < len(initial.errors)
    assert len(result.objects) == len(objects)


def test_fixed_trim_is_not_more_important_than_major_furniture():
    analysis,_ = single_object_case('sofa','floor')
    sofa = analysis.observations.objects[0]
    trim = sofa.model_copy(update={'type':'ceiling_detail','appears_fixed':True,'confidence':'high'})
    sofa.confidence = 'medium'
    assert retention_importance(sofa) < retention_importance(trim)


@pytest.mark.anyio
async def test_visual_repair_restores_missing_major_without_replaying_analysis(harness):
    from tests.test_visual_review import enabled, construction, review_action, run
    from tests.test_agent_tools import decision
    from tests.test_review_quality import finding
    app,_,calls,actions = harness
    runs,owner,uploaded = await enabled(harness)
    data = draft_data()
    data['objects'] = [item for item in data['objects'] if item['id'] != 'bed_observed']
    construction(actions,data)
    actions.append(scene_action({'wall_assignments':[],'openings':[],'objects':[],'assumptions':[],'warnings':[]}))
    def missing(payload):
        context = json.loads(payload['input'][0]['content'][0]['text'])
        item = finding('missing','bed_observed',repairable=False)
        item['source_image_ids'] = context['observations']['image_ids'][:1]
        return {'verdict':'needs_correction','summary':'The bed was observed but omitted.','findings':[item],'limitations':[]}
    actions.extend([review_action(missing),scene_action({'wall_assignments':[],'openings':[],
        'objects':[draft_data()['objects'][0]],'assumptions':[],'warnings':[]}),decision('finish')])
    result = await run(runs,owner,uploaded)
    assert result.proposal.visual_review.repair == 'accepted'
    assert 'bed_observed' in {obj.id for obj in result.base_scene.objects}
    assert validate_geometry(result.base_scene).valid
    records = app.state.analysis_service.analyzer.usage_records
    assert sum(row.operation == 'analysis' for row in records) == 1
    assert sum(row.operation == 'visual_repair' for row in records) == 1
    count = len(calls)
    assert await runs.execute(result.run_id,owner) == result and len(calls) == count
    cached = next(iter(app.state.analysis_service._states.values()))
    assert any(row.get('operation') == 'visual_repair' and row['outcome'] == 'accepted' for row in cached.diagnostics)
    restored = TypeAdapter(AnalysisState).validate_json(TypeAdapter(AnalysisState).dump_json(cached))
    assert restored.diagnostics == cached.diagnostics


@pytest.mark.anyio
async def test_repair_diagnostics_are_bounded_persistable_and_do_not_copy_exception_text(harness):
    from tests.test_visual_review import enabled
    app,_,_,_ = harness
    await enabled(harness)
    state = AnalysisState(owner='local',analysis_id='local',expires_at=source().expires_at)
    service = app.state.analysis_service
    for _ in range(40):
        service.record_repair_outcome(state,'visual_repair','failed',perf_counter(),error=ValueError('PRIVATE_PROVIDER_TEXT'))
    assert len(state.diagnostics) == 32
    assert 'PRIVATE_PROVIDER_TEXT' not in json.dumps(state.diagnostics)
    assert state.diagnostics[-1]['reason'] == 'ValueError'
