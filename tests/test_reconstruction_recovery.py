"""Bounded local recovery and independent appearance repair; no paid calls."""

from copy import deepcopy
from math import hypot

import pytest

from roometry.scenes.schemas import Position
from roometry.scenes.validation import validate_geometry
from roometry.vision.draft_validation import parse_scene_draft
from roometry.vision.local_placement import recover_local_placements
from roometry.vision.partial import assemble_partial
from roometry.vision.proposal_schemas import SceneDraft
from roometry.vision.proposals import assemble
from roometry.vision.review import draft_from_scene, recover_appearance_groups
from roometry.vision.review_schemas import ReviewJudgment
from roometry.vision.schemas import RoomAnalysisResponse
from tests import test_vision_api as api
from tests.test_catalog_assembly import single_object_case
from tests.test_review_quality import finding
from tests.test_scene_proposals import analyzed, body, confirmed, draft_data, measurements, scene_action, source

anyio_backend = api.anyio_backend
isolated_environment = api.isolated_environment
harness = api.harness


def sill_case():
    analysis, data = single_object_case('shelf')
    raw = analysis.model_dump()
    sample = raw['observations']['objects'][0]
    sample.update(confidence='high', appears_fixed=False)
    raw['observations']['objects'] = [
        {**deepcopy(sample), 'id': key, 'label': key.replace('_', ' ')} for key in ('shelf_left', 'shelf_right')]
    raw['observations']['objects'].append({**deepcopy(sample), 'id':'sill', 'label':'Window sill',
        'type':'wall_shelf', 'placement':'wall', 'appears_fixed':True})
    raw['input_options']['dimensions']['width_m'] = 3.
    analysis = RoomAnalysisResponse.model_validate(raw)
    pose = data['objects'][0]
    data['objects'] = [{**deepcopy(pose), 'id':key, 'asset_id':'shelf_metal_01',
        'position':{'x':x,'y':0.,'z':.2}} for key,x in (('shelf_left',.3),('shelf_right',2.7))]
    data['objects'].append({**deepcopy(pose),'id':'sill','asset_id':'wall_shelf_standard_01',
        'placement':'wall','wall':'north','position':{'x':1.5,'y':.8,'z':.14},
        'dimensions_m':{'width':2.2,'height':.08,'depth':.28}})
    return analysis, data


def test_floor_wall_normalization_preserves_evidence_and_other_mount_references():
    analysis, data = source(), draft_data()
    data['objects'][0]['wall'] = 'west'
    original_data, original_analysis = deepcopy(data), analysis.model_dump()
    draft = parse_scene_draft(data, analysis)
    assert draft.objects[0].wall is None
    assert draft.objects[2].support_object_id == 'desk_observed'
    assert assemble(analysis, measurements(analysis), draft)[0] is not None
    assert data == original_data and analysis.model_dump() == original_analysis
    assert any('mounting wall' in warning for warning in draft.warnings)


@pytest.mark.parametrize('field,value', [('wall','invented'),('support_object_id','desk_observed'),
                                       ('opening_id','window_observed'),('support_surface_id','worktop')])
def test_mount_normalization_does_not_hide_invalid_or_conflicting_authority(field,value):
    analysis, data = source(), draft_data()
    data['objects'][0]['wall'] = 'west'
    data['objects'][0][field] = value
    draft = parse_scene_draft(data, analysis)
    if field == 'wall':
        assert 'bed_observed' not in {o.id for o in draft.objects}
    else:
        assert draft.objects[0].wall == 'west'
    assert assemble(analysis, measurements(analysis), draft)[0] is None


@pytest.mark.anyio
async def test_redundant_mount_metadata_does_not_spend_a_paid_repair(harness):
    app, client, calls, actions = harness
    analysis = await analyzed(harness)
    data = draft_data()
    for obj in data['objects']:
        if obj['placement'] == 'floor': obj['wall'] = 'west'
    actions.append(scene_action(data))
    response = await client.post('/api/rooms/propose-scene', json=body(analysis,
        dimensions=confirmed(length_m=4.,height_m=2.6)))
    assert response.status_code == 200 and response.json()['status'] == 'ready', response.text
    assert len(calls) == 2
    assert [r.operation for r in app.state.analysis_service.analyzer.usage_records] == ['analysis','scene_proposal']


def test_sill_conflicts_recover_both_shelves_without_resize_or_evidence_changes():
    analysis, data = sill_case()
    draft = SceneDraft.model_validate(data)
    before = analysis.model_dump(), draft.model_dump()
    scene, report, issues, missing = assemble_partial(analysis, measurements(analysis), draft)
    assert scene and report.valid and not issues and not missing
    assert len(scene.objects) == 3
    for obj in scene.objects[:2]:
        pose = next(o for o in draft.objects if o.id == obj.id)
        assert 0 < hypot(obj.position.x-pose.position.x, obj.position.z-pose.position.z) <= .5
        assert obj.rotation_y_rad == pose.rotation_y_rad
        assert any(obj.id in note and 'recovered locally' in note for note in scene.assumptions)
    assert analysis.model_dump() == before[0] and draft.model_dump() == before[1]
    unchanged = assemble_partial(analysis, measurements(analysis), draft, recover_local=False)
    assert len(unchanged[0].objects) == 1 and len(unchanged[3]) == 2


@pytest.mark.parametrize('protection', ['fixed','locked','user_added','evidence','too_far'])
def test_local_recovery_does_not_override_protected_or_impossible_poses(protection):
    analysis, data = sill_case()
    # Assemble the invalid full scene in a harmless arrangement, then introduce
    # a collision to exercise the recovery guard with an actual schema-valid scene.
    for pose in data['objects'][:2]: pose['position']['z'] = .7
    scene, _, _ = assemble(analysis, measurements(analysis), SceneDraft.model_validate(data))
    assert scene
    for obj in scene.objects[:2]:
        obj.position.z = .2
        if protection in {'fixed','locked'}: setattr(obj,protection,True)
        elif protection == 'user_added': obj.provenance = 'user_added'
        elif protection == 'too_far': obj.position.x = -5.
    if protection == 'evidence':
        for obj in analysis.observations.objects[:2]: obj.appears_fixed = True
    before = scene.model_dump()
    result, report = recover_local_placements(analysis,scene,validate_geometry(scene))
    assert result.model_dump() == before and not report.valid


def test_local_translation_carries_supported_devices_without_detaching_them():
    analysis, data = source(), draft_data()
    scene, _, _ = assemble(analysis, measurements(analysis), SceneDraft.model_validate(data))
    desk = next(o for o in scene.objects if o.id == 'desk_observed')
    chair = next(o for o in scene.objects if o.id == 'chair_a')
    device = next(o for o in scene.objects if o.id == 'computer_observed')
    chair.fixed = True
    chair.position = Position(x=3.,y=0.,z=.9)
    offset = device.position.x-desk.position.x, device.position.z-desk.position.z
    result, report = recover_local_placements(analysis,scene,validate_geometry(scene))
    assert report.valid
    host = next(o for o in result.objects if o.id == desk.id)
    child = next(o for o in result.objects if o.id == device.id)
    assert (host.position.x,host.position.z) != (desk.position.x,desk.position.z)
    assert (child.position.x-host.position.x,child.position.z-host.position.z) == pytest.approx(offset)
    assert child.position.y == device.position.y and child.dimensions_m == device.dimensions_m


def test_local_recovery_cannot_break_an_established_spatial_relationship():
    analysis, data = sill_case()
    analysis.observations.objects.pop(1)
    data['objects'].pop(1)
    wall = source().observations.walls[0]
    analysis.observations.walls = [wall]
    obj = analysis.observations.objects[0]
    obj.type, obj.wall_id = 'wardrobe', wall.id
    data['wall_assignments'] = [{'observation_id':wall.id,'wall':'north'}]
    data['objects'][0].update(asset_id='wardrobe_standard_01',
        dimensions_m={'width':2.9,'height':2.,'depth':.6}, position={'x':1.5,'y':0.,'z':.6})
    data['objects'][1].update(dimensions_m={'width':2.2,'height':.08,'depth':.7},
        position={'x':1.5,'y':.8,'z':.35})
    draft = SceneDraft.model_validate(data)
    # Moving the wide wardrobe past the ledge would leave its observed wall
    # group. Geometry alone permits that shift, but evidence must reject it.
    scene, _, _, missing = assemble_partial(analysis,measurements(analysis),draft)
    assert scene and {o.entity_id for o in missing} == {obj.id}
    obj.wall_id = None
    recovered, report, _, missing = assemble_partial(analysis,measurements(analysis),draft)
    assert recovered and report.valid and not missing


def test_local_search_has_a_fixed_validation_budget(monkeypatch):
    from roometry.vision import local_placement
    analysis, data = sill_case()
    for pose in data['objects'][:2]: pose['position']['z'] = .7
    scene, _, _ = assemble(analysis, measurements(analysis), SceneDraft.model_validate(data))
    for obj in scene.objects[:2]: obj.position.z = .2
    report = validate_geometry(scene)
    checks = []
    def counted(candidate):
        checks.append(1)
        return validate_geometry(candidate)
    monkeypatch.setattr(local_placement,'MAX_CHECKS',1)
    monkeypatch.setattr(local_placement,'validate_geometry',counted)
    result,_ = recover_local_placements(analysis,scene,report)
    assert len(checks) <= 1
    assert any('recovered locally' in note for note in result.assumptions)


def test_appearance_fallback_rejects_one_bad_evidence_group_and_keeps_another():
    analysis = source()
    scene, report, _ = assemble(analysis, measurements(analysis), SceneDraft.model_validate(draft_data()))
    # A full proposal is loaded only to reuse its ownership/expiry/measurement
    # envelope; it is reconstructed from the same public fixture.
    from tests.test_agent_graph import Room
    proposal = Room().proposal().model_copy(update={'scene':scene,'validation':report})
    previous = draft_from_scene(scene)
    records = []
    for key,value in (('chair_a','#28558A'),('chair_b','#45782A')):
        source_obj = next(o for o in analysis.observations.objects if o.id == key)
        records.append({'entity_id':key,'field':'color','previous_value':source_obj.color,
            'revised_value':value,'reason':'Restore the photo-backed upholstery color.',
            'source_image_ids':source_obj.source_image_ids if key=='chair_a' else ['foreign_photo']})
    patch = SceneDraft.model_validate({**previous.model_dump(),'appearance_corrections':records})
    judgment = ReviewJudgment.model_validate({'verdict':'needs_correction','summary':'Upholstery differs.','limitations':[],
        'findings':[finding('color',key,appearance={'field':'color','value':value})
                    for key,value in (('chair_a','#28558A'),('chair_b','#45782A'))]})
    result = recover_appearance_groups(analysis,measurements(analysis),proposal,previous,patch,list(enumerate(judgment.findings)))
    assert result and result[1] == [0]
    objects = {o.id:o for o in result[0].objects}
    assert objects['chair_a'].color == '#28558A'
    assert objects['chair_b'] == next(o for o in scene.objects if o.id=='chair_b')
    assert all(o.position == next(x.position for x in scene.objects if x.id == o.id) for o in result[0].objects)
