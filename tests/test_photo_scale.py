"""Scale recovery uses visible references, never generic furniture defaults."""

import pytest

from roometry.uploads.schemas import InputDimensions
from roometry.vision.proposals import resolve_measurements
from roometry.vision.schemas import EstimatedObjectDimensions, EstimatedRoomDimensions, RoomAnalysisResponse, SpatialRelationship
from tests.test_scene_proposals import source


def scale_case(short=3., long=6.):
    data = source().model_dump()
    data['input_options']['dimensions'] = None
    obs = data['observations']
    obs['estimated_dimensions'] = {'width_m': None, 'length_m': None, 'height_m': 2.5}
    obs['objects'] = []
    obs['relationships'] = []
    obs['possible_duplicates'] = []
    for wall, role in zip(obs['walls'], ('shorter', 'longer')):
        wall['span'] = {'role': role, 'confidence': 'high',
                        'source_image_ids': wall['source_image_ids'],
                        'evidence': 'Both corners and the full span are visible across views.'}
    for opening, span in zip(obs['openings'], (short, long)):
        opening.update(confidence='high', estimated_width_m=span * .4, wall_coverage=.4)
    return RoomAnalysisResponse.model_validate(data)


@pytest.mark.parametrize('short,long', [(3., 6.), (2., 3.), (5., 8.)])
def test_different_photo_references_produce_different_rooms(short, long):
    analysis = scale_case(short, long)
    before = analysis.model_dump()
    result = resolve_measurements(analysis, {}, True, assume_missing=True)
    assert (result.dimensions.width_m, result.dimensions.length_m, result.dimensions.height_m) == (short, long, 2.5)
    assert result.assumed == [] and result.estimated == ['width_m', 'length_m', 'height_m']
    assert not result.to_confirm
    assert len(result.estimate_notes) == 2
    assert 'window on' in result.estimate_notes[0]
    assert 'window_observed' not in result.estimate_notes[0]
    assert 'not a measurement' in result.estimate_notes[0]
    assert analysis.model_dump() == before


def test_partial_user_axes_are_preserved_without_guessing_their_wall_assignment():
    analysis = scale_case()
    analysis.input_options.dimensions = InputDimensions(width_m=7., measurements_source='user_confirmed')
    result = resolve_measurements(analysis, {}, True, assume_missing=True)
    assert result.dimensions.width_m == 7. and result.dimensions.length_m == 4.
    assert result.assumed == ['length_m'] and not result.estimate_notes
    analysis.input_options.dimensions = None
    result = resolve_measurements(analysis, {'length_m': 7.}, True, assume_missing=True)
    assert result.dimensions.length_m == 7. and result.dimensions.width_m == 4.
    assert result.assumed == ['width_m'] and not result.estimate_notes


def test_height_answer_does_not_discard_horizontal_photo_scale():
    result = resolve_measurements(scale_case(), {'height_m': 2.9}, True, assume_missing=True)
    assert result.dimensions.height_m == 2.9
    assert result.estimated == ['width_m', 'length_m'] and result.assumed == []


@pytest.mark.parametrize('problem', ['cropped', 'uncertain_span', 'no_metric_reference', 'missing_wall', 'contradictory_spans'])
def test_insufficient_or_conflicting_references_do_not_manufacture_photo_estimates(problem):
    analysis = scale_case()
    if problem == 'cropped':
        analysis.observations.openings[0].wall_coverage = .05
    elif problem == 'uncertain_span':
        analysis.observations.walls[0].span.confidence = 'medium'
    elif problem == 'no_metric_reference':
        analysis.observations.openings[0].estimated_width_m = None
    elif problem == 'missing_wall':
        analysis.observations.openings[0].wall_id = None
    else:
        analysis.observations.walls[0].span.role = 'longer'
        analysis.observations.walls[1].span.role = 'shorter'
    result = resolve_measurements(analysis, {}, True)
    assert result.to_confirm == ['width_m', 'length_m']
    assert result.dimensions.width_m is None and result.dimensions.length_m is None
    assert result.assumed == [] and result.estimate_notes == []


def test_direct_photo_estimates_keep_precedence_and_unknown_height_stays_explicit():
    analysis = scale_case()
    analysis.observations.estimated_dimensions = EstimatedRoomDimensions(width_m=3.5, length_m=6.5, height_m=None)
    result = resolve_measurements(analysis, {}, True, assume_missing=True)
    assert (result.dimensions.width_m, result.dimensions.length_m) == (3.5, 6.5)
    assert result.assumed == ['height_m'] and result.dimensions.height_m == 2.6
    assert result.estimate_notes == []
    assert len(result.assumption_notes) == 1 and 'height' in result.assumption_notes[0]


def test_estimate_permission_is_still_required_before_construction():
    result = resolve_measurements(scale_case(), {}, False)
    assert result.to_confirm == ['width_m', 'length_m', 'height_m']
    assert result.assumed == []


def test_conflicting_wall_topology_does_not_establish_scale():
    analysis = scale_case()
    analysis.observations.relationships = [SpatialRelationship(
        id='wall_pair', subject_id='window_wall', target_id='door_wall', kind='opposite_wall',
        confidence='high', source_image_ids=analysis.observations.image_ids,
        evidence='These are opposite walls, contradicting the shorter/longer assignments.',
    )]
    result = resolve_measurements(analysis, {}, True)
    assert result.dimensions.width_m is None and result.dimensions.length_m is None
    assert result.estimate_notes == []


@pytest.mark.parametrize('parallel', [True, False])
def test_furniture_scale_requires_its_width_to_run_along_the_wall(parallel):
    analysis = scale_case()
    analysis.observations.openings = analysis.observations.openings[1:]
    desk = source().observations.objects[1].model_copy(deep=True)
    desk.confidence = 'high'
    desk.wall_id = 'window_wall'
    desk.wall_coverage = .5
    desk.estimated_dimensions_m = EstimatedObjectDimensions(width=1.5, depth=None, height=None)
    analysis.observations.objects = [desk]
    if parallel:
        analysis.observations.relationships = [SpatialRelationship(
            id='desk_back', subject_id=desk.id, target_id='window_wall', kind='against_wall',
            confidence='high', source_image_ids=desk.source_image_ids,
            evidence='The back of the desk runs parallel to the visible wall.',
        )]
    result = resolve_measurements(analysis, {}, True)
    if parallel:
        assert result.dimensions.width_m == 3. and result.dimensions.length_m == 6.
        assert 'desk_observed' not in ' '.join(result.estimate_notes)
    else:
        assert result.dimensions.width_m is None and result.dimensions.length_m is None
