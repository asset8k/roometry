"""Attribute missing fixtures separately from their dependent accessories."""

import json

from roometry.vision.partial import assemble_partial
from roometry.vision.proposal_schemas import SceneDraft, UnresolvedEntity
from roometry.vision.proposals import issue
from roometry.vision.reconstruction_diagnostics import omission_diagnostics
from tests.test_scene_proposals import draft_data, measurements, source


def test_failed_fixture_and_dependency_are_attributed_without_raw_model_text():
    analysis = source()
    data = draft_data()
    data['objects'][1]['position']['x'] = -10
    draft = SceneDraft.model_validate(data)
    scene, _, _, unresolved = assemble_partial(analysis, measurements(analysis), draft)
    report = omission_diagnostics(analysis, draft, scene, unresolved)
    assert report['direct_failures'] == report['dependent_failures'] == 1
    assert report['unresolved'] == 2 and report['observed'] == 7 and report['retained'] == 5
    rows = {row['entity_id']: row for row in report['entities']}
    assert rows['desk_observed']['geometry_rule'] == 'object_outside_room'
    assert rows['computer_observed']['root_entity_ids'] == ['desk_observed']
    assert rows['computer_observed']['stage'] == 'dependency'
    assert rows['desk_observed']['placement'] == 'floor'
    text = json.dumps(report)
    assert 'Object\'s rotated bounds' not in text
    assert all(obj.label not in text for obj in analysis.observations.objects)


def test_fourteen_omissions_do_not_masquerade_as_fourteen_catalog_gaps():
    analysis = source()
    root = UnresolvedEntity(entity_id='vanity', kind='object', reason=issue('unsupported_placement', 'MODEL_PROSE', 'vanity'))
    unresolved = [root]
    for index in range(13):
        parent = 'vanity' if index == 0 else f'accessory_{index - 1}'
        key = f'accessory_{index}'
        unresolved.append(UnresolvedEntity(entity_id=key, kind='object', reason=issue('dependency_unresolved', 'MODEL_PROSE', key, parent)))
    report = omission_diagnostics(analysis, None, None, unresolved)
    assert report['direct_failures'] == 1 and report['dependent_failures'] == 13
    assert report['failure_stages'] == {'catalog_binding': 1}
    assert all(row['root_entity_ids'] == ['vanity'] for row in report['entities'])
    assert 'MODEL_PROSE' not in json.dumps(report)


def test_unknown_classification_and_missing_draft_are_separate_stages():
    analysis = source()
    unresolved = [UnresolvedEntity(entity_id=key, kind='object', reason=issue(code, 'ignored', key))
                  for key, code in [('bed_observed', 'unknown_object'), ('desk_observed', 'identity_mismatch')]]
    report = omission_diagnostics(analysis, None, None, unresolved)
    assert report['failure_stages'] == {'classification': 1, 'scene_draft': 1}


def test_old_cyclic_dependency_report_terminates_and_exposes_the_unresolved_chain():
    unresolved = [UnresolvedEntity(entity_id=key, kind='object', reason=issue('dependency_unresolved', 'ignored', key, parent))
                  for key, parent in [('a', 'b'), ('b', 'a')]]
    report = omission_diagnostics(source(), None, None, unresolved)
    assert report['unresolved'] == 2
    assert all(row['root_entity_ids'] for row in report['entities'])
