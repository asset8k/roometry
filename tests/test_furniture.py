"""Catalog coverage, declared surfaces, and useful empty spaces in the models."""

import json
import math
from pathlib import Path

import pytest

from roometry.scenes.assets import ASSET_REGISTRY
from roometry.scenes.furniture import FURNITURE_CATALOG, FurnitureAsset

CORE_TYPES = {
    "bed", "sofa", "armchair", "chair", "stool", "bench", "ottoman", "desk",
    "table", "coffee_table", "side_table", "nightstand", "wardrobe", "dresser",
    "shelf", "tv_stand", "shoe_rack", "sideboard", "wall_shelf", "storage_box",
    "basket", "bin",
}


def test_core_types_and_all_their_reserved_variants_have_models():
    assert {a.object_type for a in FURNITURE_CATALOG.values()} == CORE_TYPES
    assert set(FURNITURE_CATALOG) == {
        key for key, asset in ASSET_REGISTRY.items() if asset.object_type in CORE_TYPES
    }


@pytest.mark.parametrize("asset", FURNITURE_CATALOG.values(), ids=lambda a: a.asset_id)
def test_asset_geometry_and_surfaces_are_well_formed(asset: FurnitureAsset):
    assert ASSET_REGISTRY[asset.asset_id].object_type == asset.object_type
    assert len(asset.parts) == len({p.name for p in asset.parts})
    assert all(math.isfinite(d) and d > 0 for d in asset.dimensions_m)
    assert any(p.collision for p in asset.parts)
    assert any(p.material == "primary" for p in asset.parts)
    exported = asset.to_dict()
    assert set(exported["placement_anchors"]) == set(exported["placements"])
    for part in asset.parts:
        assert all(math.isfinite(n) for n in (*part.size, *part.at, part.radius, part.top_scale))
        assert all(d > 0 for d in part.size)
        assert 0 < part.top_scale <= 1
        assert 0 < part.bottom_scale <= 1
        assert 0 < part.wall_thickness < .5
        for size, at, low, high in zip(part.size, part.at, (-.5, 0, -.5), (.5, 1, .5)):
            assert at - size / 2 >= low - 1e-9, part.name
            assert at + size / 2 <= high + 1e-9, part.name
    for surface in asset.surfaces:
        parent = next(p for p in asset.parts if p.name == surface.part)
        assert parent.collision
        assert surface.x_min < surface.x_max
        assert surface.z_min < surface.z_max
        assert surface.y == pytest.approx(parent.at[1] + parent.size[1] / 2)
        assert parent.at[0] - parent.size[0] / 2 <= surface.x_min
        assert surface.x_max <= parent.at[0] + parent.size[0] / 2
        assert parent.at[2] - parent.size[2] / 2 <= surface.z_min
        assert surface.z_max <= parent.at[2] + parent.size[2] / 2
    if ASSET_REGISTRY[asset.asset_id].supplies_surface:
        assert any(s.purpose == "objects" for s in asset.surfaces)


def occupied(asset_id: str, point: tuple[float, float, float]) -> bool:
    # These probes are for box-based furniture, away from rounded edges.
    return any(p.collision and all(abs(v - at) < size / 2 for v, at, size in zip(point, p.at, p.size))
               for p in FURNITURE_CATALOG[asset_id].parts)


def test_table_has_empty_legroom_but_a_solid_top():
    assert not occupied("table_standard_01", (0, .5, 0))
    assert occupied("table_standard_01", (0, .96, 0))


def test_sectionals_keep_their_empty_corner_and_mirror_the_chaise():
    for side, direction in (("left", -1), ("right", 1)):
        asset_id = f"sofa_sectional_{side}_01"
        assert occupied(asset_id, (direction * .25, .4, .3))
        assert not occupied(asset_id, (-direction * .25, .4, .3))


def test_shelf_surfaces_are_separate_levels_and_basket_is_open():
    surfaces = FURNITURE_CATALOG["shelf_standard_01"].surfaces
    assert len({s.y for s in surfaces}) == 4
    assert not occupied("shelf_standard_01", (0, .5, 0))
    assert not occupied("basket_standard_01", (0, .7, 0))


def test_frontend_recipes_are_generated_from_the_backend_catalog():
    path = Path(__file__).resolve().parents[1] / "frontend/src/data/core-furniture.generated.json"
    exported = json.loads(path.read_text())
    expected = json.loads(json.dumps([asset.to_dict() for asset in FURNITURE_CATALOG.values()]))
    assert exported == expected
