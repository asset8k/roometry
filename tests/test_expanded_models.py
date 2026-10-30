"""Every expanded model must render, mount and validate using shared geometry."""

import json
from collections import Counter
from pathlib import Path

import pytest

from roometry.mcp.catalog import CatalogSearch, search_furniture, get_furniture_details
from roometry.scenes.assets import ASSET_REGISTRY
from roometry.scenes.catalog import ASSET_CATALOG, dimensions_for, object_surfaces
from roometry.scenes.expanded_models import EXPANSION_CATALOG
from roometry.scenes.expansion_specs import EXPANSION_SPECS
from roometry.scenes.schemas import RoomScene
from roometry.scenes.validation import validate_geometry
from tests.test_fixture_models import test_fixture_configuration_geometry_and_metadata as check_configuration

CONFIGURATIONS = [(a, p) for a in EXPANSION_CATALOG.values() for p in sorted(ASSET_REGISTRY[a.asset_id].placements)]


def asset_scene(asset, placement):
    w, h, d = dimensions_for(asset.asset_id, placement)
    y, z = (1., d / 2) if placement == 'wall' else (6. - h, 6.) if placement == 'ceiling' else (.8, 6.) if placement == 'surface' else (0., 6.)
    obj = {'id': 'sample', 'type': asset.object_type, 'asset_id': asset.asset_id,
           'position': {'x': 6., 'y': y, 'z': z}, 'rotation_y_rad': 0.,
           'dimensions_m': {'width': w, 'height': h, 'depth': d}, 'color': asset.color,
           'placement': placement, 'support_object_id': 'host' if placement == 'surface' else None,
           'wall': 'north' if placement == 'wall' else None,
           'opening_id': 'window' if asset.object_type == 'window_covering' else None,
           'fixed': ASSET_REGISTRY[asset.asset_id].fixed_by_default, 'locked': False,
           'provenance': 'photo_observed', 'measurements_source': 'catalog'}
    objects = [obj]
    if placement == 'surface':
        objects.insert(0, {**obj, 'id': 'host', 'type': 'table', 'asset_id': 'table_standard_01',
                           'position': {'x': 6., 'y': 0., 'z': 6.}, 'placement': 'floor',
                           'support_object_id': None, 'fixed': False,
                           'dimensions_m': {'width': 6., 'height': .8, 'depth': 6.}})
    openings = []
    if asset.object_type == 'window_covering':
        openings.append({'id': 'window', 'type': 'window', 'wall': 'north', 'offset_m': 5.5,
                         'width_m': 1., 'height_m': min(1.1, h), 'sill_height_m': 1.,
                         'swing': 'none', 'hinge': None, 'provenance': 'photo_observed', 'measurements_source': 'estimated'})
    return RoomScene.model_validate({'schema_version': 1,
        'room': {'room_type': 'other', 'width_m': 12., 'length_m': 12., 'height_m': 6., 'measurements_source': 'user_confirmed'},
        'objects': objects, 'openings': openings, 'assumptions': [], 'warnings': []})


def test_expansion_inventory_is_exact_and_contains_geometry_changes():
    assert len(EXPANSION_SPECS) == len(EXPANSION_CATALOG) == 110
    assert len(ASSET_CATALOG) == len(ASSET_REGISTRY) == 217
    assert Counter(s.batch for s in EXPANSION_SPECS) == {'kitchen': 34, 'bathroom': 24, 'furniture': 36, 'finishing': 16}
    # No two additions are merely a relabel/recolor of identical parts and sizes.
    recipes = [(a.dimensions_m, a.parts, a.placement_dimensions_m) for a in EXPANSION_CATALOG.values()]
    assert len(set(recipes)) == len(recipes)


@pytest.mark.parametrize('asset,placement', CONFIGURATIONS, ids=[f'{a.asset_id}-{p}' for a, p in CONFIGURATIONS])
def test_expanded_geometry_surfaces_and_mounts_are_usable(asset, placement):
    check_configuration(asset, placement)
    scene = asset_scene(asset, placement)
    report = validate_geometry(scene)
    assert report.valid, [(e.code, e.entity_ids, e.message) for e in report.errors]
    details = get_furniture_details(asset.asset_id)
    assert tuple(details.configurations[placement].dimensions_m.model_dump().values()) == dimensions_for(asset.asset_id, placement)


def test_wall_media_console_is_available_without_reclassifying_it_as_a_floor_object():
    result = search_furniture(CatalogSearch(object_type='tv_stand', placement='wall'))
    assert {item.asset_id for item in result.items} == {'tv_stand_floating_01'}
    assert get_furniture_details('tv_stand_floating_01').configurations['wall'].support_surfaces


def test_fitted_worktops_and_sink_ledges_do_not_claim_empty_space_as_support():
    for key in ('countertop_l_left_01', 'countertop_l_right_01', 'kitchen_counter_l_left_01', 'kitchen_counter_l_right_01'):
        areas = object_surfaces(key, 'wall' if key.startswith('countertop') else 'floor')
        assert len(areas) == 2
        # The front-center of the L is open, not a tabletop across its bounding box.
        assert not any(a.x_min < 0 < a.x_max and a.z_min < .35 < a.z_max for a in areas)
    for key in ('kitchen_counter_apron_sink_01', 'kitchen_counter_double_sink_01', 'vanity_floating_single_01', 'vanity_inset_single_01'):
        placement = 'wall' if key == 'vanity_floating_single_01' else 'floor'
        areas = object_surfaces(key, placement)
        assert areas and not any(a.x_min < 0 < a.x_max and a.z_min < 0 < a.z_max for a in areas)


def test_frontend_expansion_is_generated_from_authoritative_recipes():
    root = Path(__file__).resolve().parents[1]
    assert json.loads((root / 'frontend/src/data/expanded.generated.json').read_text()) == json.loads(json.dumps([a.to_dict() for a in EXPANSION_CATALOG.values()]))
