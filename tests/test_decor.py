"""Complete catalog coverage and finishing model geometry/mounting contracts."""

import json
import math
from pathlib import Path
from typing import get_args

import pytest

from roometry.scenes.assets import ASSET_REGISTRY, ObjectType
from roometry.scenes.decor import DECOR_CATALOG
from roometry.scenes.equipment import EQUIPMENT_CATALOG
from roometry.scenes.fixture_models import FIXTURE_CATALOG
from roometry.scenes.furniture import FURNITURE_CATALOG, MaterialRole, Shape
from roometry.scenes.expanded_models import EXPANSION_CATALOG
from roometry.scenes.schemas import RoomScene
from roometry.scenes.validation import validate_geometry

DECOR_TYPES = {"rug", "picture", "clock", "window_covering", "souvenir", "vase", "book_stack", "household_accessory", "mirror", "plant"}
CONFIGURATIONS = [(asset, placement) for asset in DECOR_CATALOG.values()
                  for placement in sorted(ASSET_REGISTRY[asset.asset_id].placements)]


def test_all_216_approved_models_and_73_types_have_exactly_one_recipe():
    catalogs = (FURNITURE_CATALOG, EQUIPMENT_CATALOG, FIXTURE_CATALOG, DECOR_CATALOG, EXPANSION_CATALOG)
    ids = [key for catalog in catalogs for key in catalog]
    assert len(ids) == len(set(ids)) == 216
    assert set(ids) == set(ASSET_REGISTRY)
    assert {asset.object_type for catalog in catalogs for asset in catalog.values()} == set(get_args(ObjectType))
    assert len(get_args(ObjectType)) == 73
    assert len(DECOR_CATALOG) == 31
    assert {asset.object_type for asset in DECOR_CATALOG.values()} == DECOR_TYPES


@pytest.mark.parametrize("asset,placement", CONFIGURATIONS,
                         ids=[f"{a.asset_id}-{p}" for a, p in CONFIGURATIONS])
def test_decor_configurations_are_bounded_finite_and_match_approved_metadata(asset, placement):
    assert ASSET_REGISTRY[asset.asset_id].object_type == asset.object_type
    assert len(asset.parts) == len({p.name for p in asset.parts})
    active = [p for p in asset.parts if not p.placements or placement in p.placements]
    assert any(p.material in {"primary", "primary_metal", "primary_ceramic"} for p in active)
    assert any(p.collision for p in active)
    exported = asset.to_dict()
    assert set(exported["placement_dimensions_m"]) == ASSET_REGISTRY[asset.asset_id].placements
    assert set(exported["placement_anchors"]) == ASSET_REGISTRY[asset.asset_id].placements
    assert all(math.isfinite(d) and d > 0 for d in exported["placement_dimensions_m"][placement])
    for p in active:
        assert p.material in get_args(MaterialRole)
        assert p.shape in get_args(Shape)
        assert all(math.isfinite(v) for v in (*p.at, *p.size, *p.rotation))
        assert all(d > 0 for d in p.size)
        assert set(p.placements) <= ASSET_REGISTRY[asset.asset_id].placements
        x, y, z = p.rotation
        cx, cy, cz, sx, sy, sz = math.cos(x), math.cos(y), math.cos(z), math.sin(x), math.sin(y), math.sin(z)
        rows = ((cy * cz, -cy * sz, sy), (cx * sz + sx * sy * cz, cx * cz - sx * sy * sz, -sx * cy), (sx * sz - cx * sy * cz, sx * cz + cx * sy * sz, cx * cy))
        for row, at, low, high in zip(rows, p.at, (-.5, 0, -.5), (.5, 1, .5)):
            extent = sum(abs(v) * d / 2 for v, d in zip(row, p.size))
            assert at - extent >= low - 1e-8, p.name
            assert at + extent <= high + 1e-8, p.name
        if p.shape == "basin":
            assert p.profile[0] == (0, 0) and p.profile[-1][0] == 0
            assert all(0 <= r <= .5 and 0 <= h <= 1 for r, h in p.profile)
        if p.shape == "tube":
            assert 0 < p.wall_thickness < .5 * min(p.top_scale, p.bottom_scale)


def test_standing_and_surface_configurations_use_suitable_sizes_and_parts():
    for key in ("plant_standard_01", "plant_palm_01"):
        dims = DECOR_CATALOG[key].to_dict()["placement_dimensions_m"]
        assert dims["floor"][1] > dims["surface"][1] * 3
    for asset in DECOR_CATALOG.values():
        if asset.object_type == "picture" and "surface" in ASSET_REGISTRY[asset.asset_id].placements:
            assert any(p.name == "surface_easel_leg" for p in asset.parts)
            assert not any("easel" in p.name for p in asset.parts if "wall" in p.placements)
    mirror = DECOR_CATALOG["mirror_arch_01"]
    assert not any("kickstand" in p.name for p in mirror.parts if "wall" in p.placements)


def test_frontend_decor_recipes_are_exported_from_python():
    path = Path(__file__).resolve().parents[1] / "frontend/src/data/decor.generated.json"
    assert json.loads(path.read_text()) == json.loads(json.dumps([a.to_dict() for a in DECOR_CATALOG.values()]))


def test_living_room_example_covers_all_decor_types_and_passes_baseline_geometry():
    root = Path(__file__).resolve().parents[1]
    scene = RoomScene.model_validate_json((root / "data/fixtures/decor_catalog_scene.json").read_text())
    report = validate_geometry(scene)
    assert report.valid, report.errors
    assert DECOR_TYPES <= {obj.type for obj in scene.objects}
    assert json.loads((root / "frontend/src/lib/test-support/fixtures/decor_catalog_scene.json").read_text()) == scene.model_dump(mode="json")
