"""Regressions for colored joints, low shelving, green art and useful trade-offs."""

from types import SimpleNamespace

import pytest

from roometry.mcp.catalog import CatalogSearch, search_furniture
from roometry.optimization.policy import practical_tradeoffs
from roometry.optimization.inventory import catalog_options
from roometry.optimization.planning import planning_context
from roometry.scenes.catalog import ASSET_CATALOG, object_surfaces
from roometry.vision.catalog import bind_catalog
from roometry.vision.proposal_schemas import SceneDraft
from tests.test_catalog_assembly import single_object_case


def same_facing_coplanar_area(a, b):
    spans = [[(p.at[i] - p.size[i] / 2, p.at[i] + p.size[i] / 2) for i in range(3)] for p in (a, b)]
    area = 0.
    for axis in range(3):
        if any(abs(spans[0][axis][side] - spans[1][axis][side]) < 1e-9 for side in (0, 1)):
            product = 1.
            for i in range(3):
                if i != axis:
                    product *= max(0., min(spans[0][i][1], spans[1][i][1]) - max(spans[0][i][0], spans[1][i][0]))
            area = max(area, product)
    return area


@pytest.mark.parametrize('asset,left,right', [
    ('wardrobe_standard_01','bottom','left_side'),
    ('wardrobe_standard_01','bottom','right_side'),
    ('chair_office_01','high_back','headrest'),
])
def test_different_colors_do_not_share_visible_coplanar_faces(asset, left, right):
    parts = {p.name:p for p in ASSET_CATALOG[asset].parts}
    assert parts[left].material != parts[right].material
    assert same_facing_coplanar_area(parts[left], parts[right]) < 1e-9


def test_low_and_medium_shelves_have_distinct_tiers_and_are_discoverable():
    results = search_furniture(CatalogSearch(object_type='shelf', placement='floor', query='metal'))
    found = {item.asset_id for item in results.items}
    for key, tiers in [('shelf_low_metal_01',2),('shelf_medium_metal_01',3)]:
        asset = ASSET_CATALOG[key]
        assert key in found
        assert asset.dimensions_m[1] < 1.2
        assert len(object_surfaces(key,'floor')) == tiers
        assert len([p for p in asset.parts if p.name.startswith('board_')]) == tiers


def test_low_console_evidence_does_not_default_to_another_tall_metal_bookcase():
    analysis, raw = single_object_case('shelf','floor')
    analysis.observations.objects[0].label = 'Low waist-height open metal and wood shelving console'
    bound, errors = bind_catalog(analysis, SceneDraft.model_validate(raw))
    assert not errors and bound.objects[0].asset_id == 'shelf_low_metal_01'
    assert bound.objects[0].dimensions_m.height == .8


def test_general_improvement_shortlist_keeps_all_three_shelf_height_bands():
    options = catalog_options()
    ids = {option['asset_id'] for option in options}
    assert {'shelf_low_metal_01', 'shelf_medium_metal_01'} <= ids
    assert any(o['type'] == 'shelf' and o['dimensions_m']['height'] > 1.4 for o in options)


def test_planner_receives_narrow_pairs_and_actual_routes_without_mutating_scene():
    from tests.test_geometry import obj
    from tests.test_layout_scoring import case
    scene = case([obj('bed',kind='bed',x=1.,z=2.,width=1.,depth=2.,height=.5),
                  obj('shelf',kind='shelf',x=2.,z=2.,width=.4,depth=.4,height=1.2)])
    before = scene.model_dump()
    data = planning_context(scene)
    gap = next(item for item in data['nearby_gaps'] if set(item['object_ids']) == {'bed','shelf'})
    assert gap['bounding_gap_m'] == pytest.approx(.3)
    assert data['walking_space']['policy']['minimum_width_m'] == .6
    assert len(data['nearby_gaps']) <= 12
    assert scene.model_dump() == before


@pytest.mark.parametrize('asset', [a for a in ASSET_CATALOG.values() if a.object_type == 'picture'], ids=lambda a:a.asset_id)
def test_all_picture_variants_contain_green_nature_art_without_changing_mounts(asset):
    artwork = [p for p in asset.parts if p.material in {'foliage','foliage_dark'}]
    assert artwork and all(not p.collision for p in artwork)
    assert any('leaf' in p.name or 'hill' in p.name for p in artwork)
    assert any('frame_' in p.name for p in asset.parts)


def scores(**values):
    return SimpleNamespace(**{key:SimpleNamespace(score=values.get(key,7.)) for key in ('openness','storage','workspace','aesthetics')})


@pytest.mark.parametrize('drop,blocked,warned', [(.5,False,False),(.6,False,True),(1.5,False,True),(1.6,True,False)])
def test_modest_practical_tradeoffs_are_visible_but_only_large_ones_block(drop, blocked, warned):
    issues, warnings = practical_tradeoffs(scores(), scores(openness=7.-drop, workspace=8.),'workspace')
    assert bool(issues) is blocked
    assert bool(warnings) is warned


def test_unknown_score_does_not_become_zero_and_aesthetic_tradeoffs_are_not_a_hard_guard():
    assert practical_tradeoffs(scores(aesthetics=9.), scores(aesthetics=6.,workspace=8.), 'workspace') == ([],[])
    assert practical_tradeoffs(scores(), scores(storage=None, workspace=8.),'workspace')[0]
    assert practical_tradeoffs(scores(storage=None),scores(storage=None,workspace=8.),'workspace') == ([],[])


def test_small_per_iteration_losses_cannot_accumulate_past_the_run_allowance(monkeypatch):
    from roometry.optimization.policy import evaluate
    from roometry.optimization.schemas import CandidateProposal
    from tests.test_geometry import obj
    from tests.test_layout_scoring import case
    from tests.test_optimization_execution import policy_run, change
    from tests.test_optimization_redesign import source
    scene = case([obj('sofa',kind='sofa',x=3.,z=3.)])
    scene.objects[0].color = '#111111'
    run = policy_run(scene, 'workspace').model_copy(update={'result_scene':scene.model_copy(deep=True)})
    run.result_scene.objects[0].color = '#222222'
    def measured(candidate, **_):
        value = {'#111111':8., '#222222':7.2, '#333333':6.1}[candidate.objects[0].color]
        return scores(openness=value,workspace=8.)
    monkeypatch.setattr('roometry.optimization.policy.measure_room', measured)
    proposal = CandidateProposal(changes=[change('sofa',color='#333333')], summary='Candidate', suggestions=[])
    result = evaluate(run, proposal, source())
    assert any('openness' in item and 'too far' in item for item in result.issues)
