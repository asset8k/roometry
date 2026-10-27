"""Fitted fixtures and evidence-led pose recovery; deterministic, no paid calls."""

from copy import deepcopy
from math import pi

import pytest
import httpx
import json

from roometry.scenes.catalog import ASSET_CATALOG, object_surfaces
from roometry.scenes.validation import validate_geometry
from roometry.vision.assembly_context import assembly_features, construction_sequence
from roometry.vision.catalog import bind_catalog
from roometry.vision.evidence_placement import recover_evidence_placements
from roometry.vision.proposal_schemas import SceneDraft
from roometry.vision.proposals import assemble, issue
from roometry.vision.schemas import RoomAnalysisResponse
from tests.test_catalog_assembly import single_object_case
from tests.test_scene_proposals import measurements, source
from tests import test_vision_api as api

anyio_backend = api.anyio_backend
isolated_environment = api.isolated_environment
harness = api.harness


def case(entries, relationships=(), *, wall=False):
    analysis, data = single_object_case()
    raw = analysis.model_dump()
    original_wall = source().observations.walls[0].model_dump()
    if wall:
        raw['observations']['walls'] = [original_wall]
        data['wall_assignments'] = [{'observation_id': original_wall['id'], 'wall': 'north'}]
    sample, pose = raw['observations']['objects'][0], data['objects'][0]
    observations, objects = [], []
    for key, kind, label, asset, placement, xyz, dimensions, support in entries:
        observations.append({**deepcopy(sample), 'id': key, 'type': kind, 'label': label,
            'confidence': 'high', 'placement': placement, 'placement_confidence': 'high',
            'support_object_id': support, 'appears_fixed': placement == 'wall',
            'wall_id': original_wall['id'] if placement == 'wall' else None})
        objects.append({**deepcopy(pose), 'id': key, 'asset_id': asset, 'placement': placement,
            'wall': 'north' if placement == 'wall' else None,
            'support_object_id': support, 'position': dict(zip(('x','y','z'), xyz)),
            'dimensions_m': dict(zip(('width','height','depth'), dimensions)) if dimensions else None})
    raw['observations']['objects'] = observations
    raw['observations']['relationships'] = [{'id': f'rel_{i}', 'subject_id': a, 'target_id': b,
        'kind': kind, 'confidence': confidence, 'source_image_ids': sample['source_image_ids'],
        'evidence': 'The original photos establish this relationship.'}
        for i, (a,b,kind,confidence) in enumerate(relationships)]
    data['objects'] = objects
    return RoomAnalysisResponse.model_validate(raw), SceneDraft.model_validate(data)


def facing_case(confidence='high'):
    return case([
        ('sofa', 'sofa', 'Sofa facing television', 'sofa_standard_01', 'floor', (2.,0.,3.), (2.,.85,.8), None),
        ('tv', 'tv', 'Television', 'tv_standard_01', 'wall', (2.,1.1,.1), (1.,.7,.08), None),
    ], [('sofa','tv','faces',confidence)], wall=True)


def test_null_asset_uses_shape_evidence_and_preserves_measurements():
    analysis, data = single_object_case('sofa')
    analysis.observations.objects[0].label = 'Curved crescent sofa'
    bound, errors = bind_catalog(analysis, SceneDraft.model_validate(data))
    assert not errors and bound.objects[0].asset_id == 'sofa_curved_01'
    assert measurements(analysis).dimensions.width_m == 4


def test_null_support_model_must_supply_real_areas():
    analysis, draft = case([
        ('vanity','vanity','Single bathroom vanity',None,'floor',(2.,0.,2.),None,None),
        ('plant','plant','Small potted plant','plant_standard_01','surface',(2.3,1.,2.),(.1,.2,.1),'vanity'),
    ])
    bound, errors = bind_catalog(analysis, draft)
    assert bound and not errors
    assert object_surfaces(bound.objects[0].asset_id, 'floor')
    assert bound.objects[1].support_surface_id


def test_lower_usable_vanity_shelf_wins_over_undersized_basin_ledges():
    analysis, draft = case([
        ('vanity','vanity','Open shelf vanity','vanity_open_shelf_01','floor',(2.,0.,2.),None,None),
        ('basket','basket','Basket on the vanity shelf','basket_standard_01','surface',(2.,.4,2.),(.3,.2,.25),'vanity'),
    ])
    bound, errors = bind_catalog(analysis, draft)
    assert bound and not errors
    assert bound.objects[1].support_surface_id == 'towel_shelf'
    scene, report, errors = assemble(analysis, measurements(analysis), draft)
    assert scene and report.valid and not errors
    assert scene.objects[1].position.y < .5


def test_kitchen_run_keeps_sink_cutout_and_adjoining_units_without_overlap_exceptions():
    analysis, draft = case([
        ('sink_unit','kitchen_counter','Apron sink cabinet with faucet','kitchen_counter_apron_sink_01','floor',(1.5,0.,.325),(1.2,1.2,.65),None),
        ('drawers','kitchen_cabinet','Adjacent drawers','kitchen_cabinet_drawers_01','floor',(2.4,0.,.325),(.6,.9,.65),None),
    ])
    scene, report, errors = assemble(analysis, measurements(analysis), draft)
    assert scene and report.valid and not errors
    assert {'basin','faucet','worktop'} <= set(assembly_features(ASSET_CATALOG['kitchen_counter_apron_sink_01']))
    # An unrelated object buried in the basin remains a genuine collision.
    sample = scene.objects[1].model_copy(deep=True)
    sample.position.x, sample.position.z = 1.5, .325
    invalid = scene.model_copy(update={'objects': [scene.objects[0], sample]})
    assert not validate_geometry(invalid).valid


def test_bathroom_assemblies_keep_vanity_basin_and_separate_shower_plumbing():
    analysis, draft = case([
        ('vanity','vanity','Floating vanity with sink and tap','vanity_floating_single_01','wall',(1.,.4,.3),None,None),
        ('mirror','mirror','Mirror above vanity','mirror_standard_01','wall',(1.,1.7,.1),(.6,.7,.04),None),
        ('shower','shower','Walk-in shower','shower_walk_in_right_01','floor',(2.9,0.,.6),None,None),
        ('plumbing','shower_fixture','Rain shower rail and mixer','shower_fixture_standard_01','wall',(2.9,.7,.2),None,None),
    ], wall=True)
    scene, report, errors = assemble(analysis, measurements(analysis), draft)
    assert scene and report.valid and not errors, errors
    assert len(scene.objects) == 4


def test_wrong_sofa_facing_recovers_with_no_room_or_size_changes():
    analysis, draft = facing_case()
    before = analysis.model_dump(), draft.model_dump()
    assert assemble(analysis, measurements(analysis), draft)[0] is None
    scene, report, errors = assemble(analysis, measurements(analysis), draft, recover_local=True)
    assert scene and report.valid and not errors
    assert scene.objects[0].rotation_y_rad == pytest.approx(pi)
    assert scene.objects[0].position.model_dump() == draft.objects[0].position.model_dump()
    assert scene.objects[0].dimensions_m == draft.objects[0].dimensions_m
    assert scene.room.width_m == scene.room.length_m == 4
    assert before == (analysis.model_dump(), draft.model_dump())
    assert any('high-confidence photo' in note for note in scene.assumptions)


def test_observed_fitted_cabinet_aligns_to_wall_and_carries_its_surface_item():
    wall = source().observations.walls[0].id
    analysis, draft = case([
        ('cabinet','kitchen_cabinet','Fitted base cabinet','kitchen_cabinet_drawers_01','floor',(2.,0.,.75),(.6,.9,.6),None),
        ('plant','plant','Small potted plant','plant_standard_01','surface',(2.,1.,.75),(.1,.2,.1),'cabinet'),
    ], [('cabinet', wall, 'against_wall', 'high')], wall=True)
    scene, report, errors = assemble(analysis, measurements(analysis), draft, recover_local=True)
    assert scene and report.valid and not errors
    assert scene.objects[0].position.z == pytest.approx(.3)
    assert scene.objects[1].position.z == pytest.approx(.3)
    assert scene.objects[0].fixed  # Reconstruction can align estimated fixtures; editing cannot.
    assert scene.objects[1].support_object_id == 'cabinet'


def test_recovery_does_not_turn_a_wall_association_into_cross_room_relocation():
    wall = source().observations.walls[0].id
    analysis, draft = case([
        ('cabinet','kitchen_cabinet','Fitted base cabinet','kitchen_cabinet_drawers_01','floor',(2.,0.,2.),None,None),
    ], [('cabinet', wall, 'against_wall', 'high')], wall=True)
    scene, report, _ = assemble(analysis, measurements(analysis), draft, require_fidelity=False)
    before = scene.model_dump()
    recovered, _ = recover_evidence_placements(analysis, scene, report)
    assert recovered.model_dump() == before


@pytest.mark.parametrize('mode', ['uncertain','locked','collision'])
def test_recovery_does_not_override_uncertainty_locks_or_geometry(mode):
    analysis, draft = facing_case('medium' if mode == 'uncertain' else 'high')
    scene, report, _ = assemble(analysis, measurements(analysis), draft, require_fidelity=False)
    assert scene and report.valid
    if mode == 'locked':
        scene.objects[0].locked = True
    if mode == 'collision':
        # Rotation swaps this rectangle into an obstacle's solid volume.
        scene.objects[0].rotation_y_rad = pi / 2
        scene.objects[0].dimensions_m.width = 2.8
        scene.objects[0].position.z = 2.
        obstacle = scene.objects[0].model_copy(deep=True)
        obstacle.id = 'obstacle'
        obstacle.position.x, obstacle.position.z = .6, 2.
        obstacle.dimensions_m.width, obstacle.dimensions_m.depth = .3, .3
        obstacle.rotation_y_rad = 0.
        scene.objects.append(obstacle)
        report = validate_geometry(scene)
        assert report.valid
    before = scene.model_dump()
    recovered, _ = recover_evidence_placements(analysis, scene, report)
    assert recovered.model_dump() == before


def test_context_explains_integrated_parts_and_actual_conflicting_components():
    analysis, draft = case([
        ('sink_unit','kitchen_counter','Apron sink cabinet','kitchen_counter_apron_sink_01','floor',(2.,0.,2.),None,None),
        ('drawers','kitchen_cabinet','Drawers','kitchen_cabinet_drawers_01','floor',(2.,0.,2.),None,None),
    ])
    from roometry.config import Settings
    from roometry.vision.proposal_request import build_proposal_request
    import json
    payload = build_proposal_request(Settings(_env_file=None), analysis, measurements(analysis), draft,
        [issue('geometry','object_overlap: solid components overlap','sink_unit','drawers')], operation='scene_repair')
    data = json.loads(payload['input'][0]['content'][0]['text'])
    assert data['component_conflicts'][0]['conflicting_components'][0]['object_id'] == 'sink_unit'
    assert {'basin','faucet','worktop'} <= set(data['asset_models']['kitchen_counter_apron_sink_01']['included_components'])
    assert data['construction_order']['object_sequence']


def test_sequence_places_support_before_higher_priority_dependent():
    analysis, draft = case([
        ('tv','tv','Television','tv_standard_01','surface',(2.,1.,2.),None,'console'),
        ('console','tv_stand','Console','tv_stand_standard_01','floor',(2.,0.,2.),None,None),
    ])
    assert construction_sequence(analysis) == ['console','tv']


@pytest.mark.anyio
@pytest.mark.parametrize('room', ['living','kitchen','bathroom'])
async def test_mocked_upload_analysis_and_construction_retain_fixture_groups_without_paid_repair(harness, room):
    app, client, calls, actions = harness
    if room == 'living':
        analysis, draft = facing_case()
    elif room == 'kitchen':
        analysis, draft = case([
            ('sink_unit','kitchen_counter','Apron sink cabinet with faucet','kitchen_counter_apron_sink_01','floor',(1.5,0.,.325),(1.2,1.2,.65),None),
            ('drawers','kitchen_cabinet','Adjacent drawers','kitchen_cabinet_drawers_01','floor',(2.4,0.,.325),(.6,.9,.65),None),
        ])
    else:
        analysis, draft = case([
            ('vanity','vanity','Floating vanity with sink and tap','vanity_floating_single_01','wall',(1.,.4,.3),None,None),
            ('shower','shower','Walk-in shower','shower_walk_in_right_01','floor',(2.9,0.,.6),None,None),
            ('plumbing','shower_fixture','Rain shower rail and mixer','shower_fixture_standard_01','wall',(2.9,.7,.2),None,None),
        ], wall=True)

    async def observations(request, payload):
        response = api.provider_response(payload)
        ids = json.loads(payload['input'][0]['content'][0]['text'])['image_ids']
        raw = analysis.observations.model_dump(mode='json')
        raw['image_ids'] = ids
        for entity in [*raw['objects'], *raw['walls'], *raw['relationships']]:
            entity['source_image_ids'] = ids
        response['output'][0]['content'][0]['text'] = json.dumps(raw)
        return httpx.Response(200, json=response)

    from tests.test_scene_proposals import analyzed, scene_action, body, confirmed
    actions.append(observations)
    stored = await analyzed(harness)
    actions.append(scene_action(draft.model_dump(mode='json')))
    response = await client.post('/api/rooms/propose-scene', json=body(stored,
        dimensions=confirmed(length_m=4.,height_m=2.6)))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['status'] == 'ready', result
    assert len(result['scene']['objects']) == len(draft.objects)
    assert len(calls) == 2  # One analysis and one construction; no repair request.
    assert [record.operation for record in app.state.analysis_service.analyzer.usage_records] == ['analysis','scene_proposal']
