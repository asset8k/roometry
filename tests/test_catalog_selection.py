"""Expansion stays discoverable without sending every variant on each request."""

import json
from roometry.config import Settings
from roometry.optimization.inventory import catalog_options
from roometry.scenes.catalog import candidates
from roometry.vision.context_budget import photo_references
from roometry.vision.proposal_request import build_proposal_request
from roometry.vision.proposal_schemas import SceneDraft
from tests.test_catalog_assembly import single_object_case
from tests.test_scene_proposals import measurements


def context(analysis, draft=None):
    request = build_proposal_request(Settings(_env_file=None), analysis, measurements(analysis), draft, [],
                                     operation='scene_repair' if draft else 'scene_proposal')
    raw = json.loads(request['input'][0]['content'][0]['text'])
    return photo_references(raw, analysis.observations.image_ids, decode=True)


def test_uncertain_variants_keep_every_mount_without_dumping_the_whole_family():
    analysis, _ = single_object_case('plant', None)
    obj = analysis.observations.objects[0]
    obj.confidence = 'medium'
    obj.label = 'Leafy plant'
    data = context(analysis)
    models = data['asset_bindings'][0]['models']
    assert len(models) <= 7
    assert {p for model in models for p in model['placements']} == {'floor', 'surface', 'wall'}
    assert all(model['asset_id'] in data['asset_models'] for model in models)


def test_shape_matching_and_repair_keep_a_previously_selected_variant():
    analysis, raw = single_object_case('sofa', 'floor')
    analysis.observations.objects[0].label = 'Curved crescent sofa'
    initial = context(analysis)
    assert 'sofa_curved_01' in {m['asset_id'] for m in initial['asset_bindings'][0]['models']}
    assert len(initial['asset_bindings'][0]['models']) <= 4
    raw['objects'][0]['asset_id'] = 'sofa_chesterfield_01'
    repaired = context(analysis, SceneDraft.model_validate(raw))
    assert 'sofa_chesterfield_01' in {m['asset_id'] for m in repaired['asset_bindings'][0]['models']}


def test_ambiguous_attachment_keeps_new_wall_console_as_a_hypothesis():
    analysis, _ = single_object_case('tv_stand', 'floor')
    analysis.observations.objects[0].label = 'Floating media console'
    analysis.observations.objects[0].placement_confidence = 'low'
    data = context(analysis)
    wall = [m for m in data['hypothesis_alternatives'] if m['placement'] == 'wall']
    assert any(m['asset_id'] == 'tv_stand_floating_01' and m['requires_interpretation_correction'] for m in wall)
    assert {m['asset_id'] for m in wall} <= {a.asset_id for a in candidates('tv_stand', 'wall')}


def test_chat_shortlists_keep_types_specific_requested_shapes_and_current_assets():
    base = catalog_options()
    query = catalog_options('round glass coffee table', required_ids=['sofa_chesterfield_01'])
    assert {m['type'] for m in base} == {m['type'] for m in query}
    assert 'coffee_table_round_glass_01' in {m['asset_id'] for m in query}
    assert 'sofa_chesterfield_01' in {m['asset_id'] for m in query}
    assert any(m['type'] == 'tv' and set(m['placements']) == {'wall', 'surface'} for m in query)
    assert len(json.dumps(base)) < 30_000
    assert 'parts' not in json.dumps(query)
