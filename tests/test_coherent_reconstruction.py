"""Coherent photo reconstruction: real geometry, scoped repairs, no provider calls."""

from copy import deepcopy
import json
from math import hypot, pi

import pytest
from jsonschema import Draft202012Validator

from roometry.config import Settings
from roometry.scenes.dependencies import support_closure
from roometry.scenes.edit_schemas import MoveObject
from roometry.scenes.mutations import apply_edit, EditRejected
from roometry.scenes.schemas import RoomScene
from roometry.scenes.validation import validate_geometry
from roometry.vision.essential_recovery import essential_analysis, expand_lean_draft, MAX_ESSENTIAL_OBJECTS
from roometry.vision.fidelity import evaluate_fidelity
from roometry.vision.functional_groups import carry_review_groups, reconstruction_group, workstation_pairs
from roometry.vision.local_placement import recover_local_placements, MAX_SHIFT_M
from roometry.vision.partial import assemble_partial
from roometry.vision.proposal_request import build_proposal_request
from roometry.vision.proposal_schemas import SceneDraft
from roometry.vision.proposals import assemble, issue
from roometry.vision.review import candidate_allowed, draft_from_scene, patch_matches_findings
from roometry.vision.review_schemas import ReviewFinding
from tests.test_assembly_quality import case
from tests.test_component_geometry import model
from tests.test_geometry import scene, room
from tests.test_review_quality import finding
from tests.test_scene_proposals import measurements, source, draft_data, analyzed, body, confirmed, output_limited_scene, scene_action
from tests import test_vision_api as api

anyio_backend = api.anyio_backend
isolated_environment = api.isolated_environment
harness = api.harness


def office():
    analysis, draft = case([
        ('desk_a','desk','First desk','desk_standard_01','floor',(1.2,0.,2.),(1.2,.75,.6),None),
        ('chair_a','chair','First task chair','chair_standard_01','floor',(1.2,0.,2.8),(.45,.9,.45),None),
        ('screen','monitor','Monitor on first desk','monitor_standard_01','surface',(1.2,.75,2.),None,'desk_a'),
        ('desk_b','desk','Second desk','desk_standard_01','floor',(3.2,0.,2.),(1.2,.75,.6),None),
        ('chair_b','chair','Second task chair','chair_standard_01','floor',(3.2,0.,2.8),(.45,.9,.45),None),
    ], [('chair_a','desk_a','faces','high'),('chair_b','desk_b','faces','high')])
    for obj in draft.objects:
        if obj.id.startswith('chair_'): obj.rotation_y_rad = pi
    result, report, problems = assemble(analysis, measurements(analysis), draft)
    assert result and report.valid, problems
    return analysis, result


@pytest.mark.parametrize('angle', [0., pi/2])
def test_review_transform_preserves_the_right_chair_and_screen_with_two_desks(angle):
    analysis, before = office()
    original = before.model_dump()
    previous = draft_from_scene(before)
    merged = previous.model_copy(deep=True)
    desk = next(obj for obj in merged.objects if obj.id == 'desk_a')
    desk.position.x, desk.position.z, desk.rotation_y_rad = 1.5, 1.2, angle
    target = ReviewFinding.model_validate(finding('position','desk_a',target={'relation':'near','target_id':'desk_b'}))
    findings = [target]
    if angle:
        findings.append(ReviewFinding.model_validate(finding('orientation','desk_a',target={'relation':'faces','target_id':'desk_b'})))
    revised = carry_review_groups(analysis,before,previous,merged,findings)
    assert reconstruction_group(analysis,previous.objects,{'desk_a'}) == {'desk_a','chair_a','screen'}
    assert patch_matches_findings(previous,revised,findings,analysis=analysis)
    candidate, report, issues = assemble(analysis, measurements(analysis), revised)
    assert candidate and report.valid and candidate_allowed(analysis,before,candidate), issues
    objects = {obj.id:obj for obj in candidate.objects}
    assert objects['chair_a'].position != next(obj for obj in before.objects if obj.id=='chair_a').position
    assert objects['desk_b'] == next(obj for obj in before.objects if obj.id=='desk_b')
    assert objects['chair_b'] == next(obj for obj in before.objects if obj.id=='chair_b')
    assert candidate.room == before.room
    assert before.model_dump() == original
    assert all(check.passed for check in evaluate_fidelity(analysis,candidate).checks if check.kind in {'facing','adjacency','support'})


def test_collision_valid_desk_repair_cannot_abandon_the_paired_chair():
    analysis, before = office()
    bad = before.model_copy(deep=True)
    for obj in bad.objects:
        if obj.id in {'desk_a','screen'}: obj.position.z -= 1.5
    assert validate_geometry(bad).valid
    assert not candidate_allowed(analysis,before,bad)
    assert any(not check.passed and check.kind=='adjacency' and check.entity_ids==['chair_a','desk_a']
               for check in evaluate_fidelity(analysis,bad).checks)


@pytest.mark.parametrize('protection', ['locked','fixed','user_added'])
def test_protected_chair_is_not_carried_by_a_reviewed_desk(protection):
    analysis, before = office()
    chair = next(obj for obj in before.objects if obj.id=='chair_a')
    if protection=='user_added': chair.provenance='user_added'
    else: setattr(chair,protection,True)
    previous = draft_from_scene(before)
    patch = previous.model_copy(deep=True)
    patch.objects[0].position.z -= .5
    target = ReviewFinding.model_validate(finding('position','desk_a',target={'relation':'near','target_id':'desk_b'}))
    with pytest.raises(ValueError,match='protected follower'):
        carry_review_groups(analysis,before,previous,patch,[target])
    assert chair.position.z == 2.8


def test_pairing_needs_unambiguous_photo_evidence_and_missing_support_children_keep_their_scope():
    analysis, before = office()
    assert workstation_pairs(analysis,before.objects)=={'chair_a':'desk_a','chair_b':'desk_b'}
    relation = analysis.observations.relationships[0]
    analysis.observations.relationships.append(relation.model_copy(update={'id':'ambiguous_peer','target_id':'desk_b'}))
    assert 'chair_a' not in workstation_pairs(analysis,before.objects)
    retained = [obj for obj in before.objects if obj.id!='screen']
    assert 'screen' in reconstruction_group(analysis,retained,{'desk_a'})


def test_restored_child_has_no_old_pose_to_carry():
    analysis, before = office()
    previous = draft_from_scene(before)
    previous.objects = [obj for obj in previous.objects if obj.id != 'screen']
    merged = draft_from_scene(before)
    merged.objects[0].position.z -= .2
    screen = next(obj for obj in merged.objects if obj.id == 'screen')
    screen.position.z -= .2
    target = ReviewFinding.model_validate(finding('position','desk_a',target={'relation':'near','target_id':'desk_b'}))
    revised = carry_review_groups(analysis,before,previous,merged,[target])
    assert next(obj for obj in revised.objects if obj.id == 'screen') == screen


def fixture_conflict():
    shower = model('shower_standard_01','shower',x=1.5,z=1.5)
    tub = model('bathtub_alcove_01','tub',x=2.5,z=1.5)
    for obj in (shower,tub): obj.fixed,obj.provenance=True,'photo_observed'
    original = scene([shower,tub],dimensions=room(4.,4.,2.6))
    analysis, _ = case([
        ('shower','shower','Installed shower',None,'floor',(1.5,0.,1.5),None,None),
        ('tub','bathtub','Installed bathtub',None,'floor',(2.5,0.,1.5),None,None),
    ])
    for obj in analysis.observations.objects: obj.appears_fixed=True
    return analysis, original


def test_estimated_installed_fixture_conflict_can_recover_locally_without_resizing():
    analysis, original = fixture_conflict()
    report = validate_geometry(original)
    assert any(error.code=='object_overlap' for error in report.errors)
    prior = original.model_dump()
    recovered, checked = recover_local_placements(analysis,original,report)
    assert checked.valid
    assert recovered.room==original.room and original.model_dump()==prior
    assert {obj.id for obj in recovered.objects}=={obj.id for obj in original.objects}
    for obj, old in zip(recovered.objects,original.objects):
        assert obj.dimensions_m==old.dimensions_m and obj.rotation_y_rad==old.rotation_y_rad
        assert obj.asset_id==old.asset_id and obj.fixed
        assert hypot(obj.position.x-old.position.x,obj.position.z-old.position.z)<=MAX_SHIFT_M+1e-6


def test_recovery_scope_and_locks_do_not_let_neighbors_move_to_make_a_patch_fit():
    analysis, original = fixture_conflict()
    report = validate_geometry(original)
    result, checked = recover_local_placements(analysis,original,report,allowed_ids=set())
    assert result==original and not checked.valid
    for obj in original.objects: obj.locked=True
    result, checked = recover_local_placements(analysis,original,report)
    assert result==original and not checked.valid


def test_estimated_pantry_can_shift_along_its_wall_without_moving_the_worktop_group():
    analysis, draft = case([
        ('base','kitchen_cabinet','Base cabinets','kitchen_cabinet_drawers_01','floor',(2.,0.,.325),(1.55,.86,.65),None),
        ('top','countertop','Stone worktop','countertop_standard_01','surface',(2.,.86,.325),(1.45,.04,.6),'base'),
        ('pantry','kitchen_cabinet','Tall pantry','kitchen_cabinet_tall_pantry_01','floor',(2.7,0.,.3),None,None),
    ])
    for obj in analysis.observations.objects:
        obj.wall_id = 'window_wall'
        obj.appears_fixed = True
    # The existing worktop group is outside a targeted pantry repair's scope.
    missing, _, _ = assemble(analysis,measurements(analysis),draft)
    assert missing is None
    restored, report, issues = assemble(analysis,measurements(analysis),draft,recover_local=True,recovery_ids={'pantry'})
    assert restored and report.valid, issues
    pantry = next(obj for obj in restored.objects if obj.id == 'pantry')
    assert pantry.position.z == .3 and 0 < abs(pantry.position.x-2.7) <= MAX_SHIFT_M
    assert next(obj for obj in restored.objects if obj.id == 'base').position.x == 2.
    assert next(obj for obj in restored.objects if obj.id == 'top').position.x == 2.


def nested_kitchen(reverse=False):
    analysis, draft = case([
        ('base','kitchen_counter','Base worktop cabinet','kitchen_counter_standard_01','floor',(2.,0.,2.),(1.5,.9,.7),None),
        ('top','countertop','Separate stone slab','countertop_standard_01','surface',(2.,0.,2.),(1.5,.04,.7),'base'),
        ('hob','cooker','Built-in cooktop','cooker_inset_hob_01','surface',(2.,0.,2.),None,'top'),
    ])
    if reverse:
        analysis.observations.objects.reverse();draft.objects.reverse()
    return analysis,draft


@pytest.mark.parametrize('reverse',[False,True])
def test_countertop_supports_hob_in_dependency_order_and_every_contact_is_checked(reverse):
    analysis,draft = nested_kitchen(reverse)
    result, report, issues = assemble(analysis,measurements(analysis),draft)
    assert result and report.valid, issues
    objects={obj.id:obj for obj in result.objects}
    assert objects['top'].position.y==pytest.approx(.9)
    assert objects['hob'].position.y==pytest.approx(.94)
    bad=result.model_copy(deep=True)
    next(obj for obj in bad.objects if obj.id=='hob').position.x+=1.
    assert any(error.code=='surface_support' for error in validate_geometry(bad).errors)
    floating=result.model_copy(deep=True)
    next(obj for obj in floating.objects if obj.id=='top').position.y+=.1
    assert not validate_geometry(floating).valid


def test_long_slab_cannot_be_supported_by_a_small_cabinet_or_a_cycle():
    analysis,draft = nested_kitchen()
    next(obj for obj in draft.objects if obj.id=='base').dimensions_m.width=.6
    result, report, issues = assemble(analysis,measurements(analysis),draft)
    assert result is None and any(error.code=='surface_support' for error in report.errors)
    analysis,draft = nested_kitchen()
    next(obj for obj in draft.objects if obj.id=='top').support_object_id='hob'
    result, _, issues = assemble(analysis,measurements(analysis),draft)
    assert result is None and issues


def test_l_shaped_slab_contacts_real_coplanar_parts_of_one_fitted_run():
    analysis,draft=case([
        ('run','kitchen_counter','L shaped fitted cabinet run','kitchen_counter_l_left_01','floor',(2.,0.,2.),None,None),
        ('slab','countertop','L shaped stone slab','countertop_l_left_01','surface',(2.,.9,2.),None,'run'),
    ])
    result,report,issues=assemble(analysis,measurements(analysis),draft)
    assert result and report.valid,issues
    # An incompatible straight cabinet cannot support the return through empty space.
    draft.objects[0].asset_id='kitchen_counter_standard_01'
    result,report,issues=assemble(analysis,measurements(analysis),draft)
    assert result is None


def test_manual_transform_carries_nested_supports_and_respects_a_locked_grandchild():
    analysis,draft=nested_kitchen()
    original,_,issues=assemble(analysis,measurements(analysis),draft)
    assert original,issues
    for obj in original.objects: obj.fixed=False
    edit=MoveObject(operation='move',object_id='base',position={'x':2.1,'z':2.})
    result=apply_edit(original,edit)
    assert validate_geometry(result).valid
    assert support_closure(original.objects,{'base'})=={'base','top','hob'}
    assert all(obj.position.x==pytest.approx(2.1) for obj in result.objects)
    next(obj for obj in original.objects if obj.id=='hob').locked=True
    with pytest.raises(EditRejected,match='locked or fixed'):
        apply_edit(original,edit)


def test_essential_recovery_keeps_dependency_ancestors_and_exposes_a_bounded_contract():
    analysis=source()
    template=analysis.observations.objects[-1]
    for n in range(50):
        analysis.observations.objects.append(template.model_copy(update={'id':f'optional_{n}','type':'household_accessory','appears_fixed':False}))
    selected,deferred=essential_analysis(analysis)
    assert len(selected.observations.objects)<=MAX_ESSENTIAL_OBJECTS and len(deferred)==50
    assert {obj.id for obj in selected.observations.objects}=={obj.id for obj in source().observations.objects}
    payload=build_proposal_request(Settings(_env_file=None),analysis,measurements(analysis),None,
        [issue('draft_schema','The first response exceeded its output limit.')],operation='scene_repair',essential_only=True)
    context=json.loads(payload['input'][0]['content'][0]['text'])
    assert context['recovery_contract']=='essential_objects_after_output_limit'
    assert set(context['deferred_object_ids'])==set(deferred)
    schema=payload['text']['format']['schema']
    assert 'dimensions_m' not in schema['$defs']['DraftObject']['properties']
    assert schema['properties']['openings']['minItems']==len(analysis.observations.openings)
    lean=SceneDraft.model_validate(draft_data()).model_dump(mode='json')
    for obj in lean['objects']:
        obj.pop('dimensions_m');obj.pop('support_surface_id',None)
    Draft202012Validator(schema).validate(lean)
    parsed=SceneDraft.model_validate(expand_lean_draft(lean))
    assert all(obj.dimensions_m is None and obj.support_surface_id is None for obj in parsed.objects)
    assert len(analysis.observations.objects)==55


@pytest.mark.anyio
async def test_output_exhaustion_uses_one_lean_recovery_and_caches_the_result(harness):
    app,client,calls,actions=harness
    analysis=await analyzed(harness)
    async def lean_response(request,payload):
        context=json.loads(payload['input'][0]['content'][0]['text'])
        assert context['recovery_contract']=='essential_objects_after_output_limit'
        data=SceneDraft.model_validate(draft_data()).model_dump(mode='json')
        for obj in data['objects']:
            obj.pop('dimensions_m');obj.pop('support_surface_id',None)
        Draft202012Validator(payload['text']['format']['schema']).validate(data)
        return await scene_action(data)(request,payload)
    actions.extend([output_limited_scene(),lean_response])
    request=body(analysis,dimensions=confirmed(length_m=4.,height_m=2.6))
    response=await client.post('/api/rooms/propose-scene',json=request)
    assert response.status_code==200,response.text
    assert response.json()['scene'] and response.json()['validation']['valid']
    assert len(calls)==3
    assert calls[1]['model']=='gpt-6.1-terra' and calls[2]['model']=='gpt-6-luna'
    assert calls[1]['max_output_tokens']==calls[2]['max_output_tokens']
    assert (await client.post('/api/rooms/propose-scene',json=request)).json()==response.json()
    assert len(calls)==3
    records=[row for row in app.state.analysis_service.usage_records if row.stage=='scene_proposal']
    assert len(records)==2 and records[0].outcome=='analysis_incomplete'
