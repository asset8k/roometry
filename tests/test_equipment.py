"""Equipment catalog coverage, mounting configurations and geometry export."""

import json
import math
from pathlib import Path
from typing import get_args

import pytest

from roometry.scenes.assets import ASSET_REGISTRY
from roometry.scenes.expansion_specs import EXPANSION_IDS
from roometry.scenes.equipment import EQUIPMENT_CATALOG
from roometry.scenes.furniture import MaterialRole, Shape

EQUIPMENT_TYPES = {
    "computer", "laptop", "monitor", "printer", "game_console", "speaker", "tv",
    "projector", "floor_lamp", "table_lamp", "chandelier", "ceiling_light",
    "ceiling_fan", "radiator", "air_conditioner", "fan",
}
CONFIGURATIONS = [(asset, placement) for asset in EQUIPMENT_CATALOG.values()
                  for placement in sorted(ASSET_REGISTRY[asset.asset_id].placements)]


def test_all_sixteen_equipment_types_and_their_approved_ids_have_models():
    assert {a.object_type for a in EQUIPMENT_CATALOG.values()} == EQUIPMENT_TYPES
    assert set(EQUIPMENT_CATALOG) == {key for key, asset in ASSET_REGISTRY.items()
                                      if asset.object_type in EQUIPMENT_TYPES and key not in EXPANSION_IDS}


@pytest.mark.parametrize("asset,placement", CONFIGURATIONS,
                         ids=[f"{a.asset_id}-{p}" for a, p in CONFIGURATIONS])
def test_each_configuration_fits_its_declared_bounds_and_has_valid_metadata(asset, placement):
    assert ASSET_REGISTRY[asset.asset_id].object_type == asset.object_type
    assert len(asset.parts) == len({p.name for p in asset.parts})
    active = [p for p in asset.parts if not p.placements or placement in p.placements]
    assert any(p.material == "primary" for p in active)
    assert any(p.collision for p in active)
    exported = asset.to_dict()
    assert set(exported["placement_anchors"]) == set(exported["placements"])
    assert set(exported["placement_dimensions_m"]) == set(exported["placements"])
    assert all(math.isfinite(d) and d > 0 for d in exported["placement_dimensions_m"][placement])
    for part in active:
        assert part.material in get_args(MaterialRole)
        assert part.shape in get_args(Shape)
        assert all(d > 0 for d in part.size)
        assert all(math.isfinite(n) for n in (*part.size, *part.at, *part.rotation))
        assert set(part.placements) <= ASSET_REGISTRY[asset.asset_id].placements
        assert 0 < part.top_scale <= 1 and 0 < part.bottom_scale <= 1
        if part.shape == "tube":
            assert 0 < part.wall_thickness < .5 * min(part.top_scale, part.bottom_scale)
        if part.shape == "torus":
            assert 0 < part.radius < .5
        # Three.js XYZ Euler order. A rotated primitive's bounding box must fit,
        # rather than only its unrotated size or its center.
        x, y, z = part.rotation
        cx, cy, cz, sx, sy, sz = math.cos(x), math.cos(y), math.cos(z), math.sin(x), math.sin(y), math.sin(z)
        matrix = ((cy * cz, -cy * sz, sy),
                  (cx * sz + sx * sy * cz, cx * cz - sx * sy * sz, -sx * cy),
                  (sx * sz - cx * sy * cz, sx * cz + cx * sy * sz, cx * cy))
        for row, at, low, high in zip(matrix, part.at, (-.5, 0, -.5), (.5, 1, .5)):
            extent = sum(abs(coefficient) * dimension / 2 for coefficient, dimension in zip(row, part.size))
            assert at - extent >= low - 1e-8, part.name
            assert at + extent <= high + 1e-8, part.name


def test_mounts_and_stands_are_separate_configurations():
    tv = EQUIPMENT_CATALOG["tv_standard_01"]
    assert any(p.name.startswith("surface_foot_") for p in tv.parts)
    assert not any("foot" in p.name for p in tv.parts if "wall" in p.placements)
    projector = EQUIPMENT_CATALOG["projector_standard_01"]
    assert any(p.name == "ceiling_mount_post" for p in projector.parts)
    assert not any("mount_post" in p.name for p in projector.parts if "surface" in p.placements)
    fan = EQUIPMENT_CATALOG["fan_standard_01"].to_dict()
    assert fan["placement_dimensions_m"]["floor"][1] > fan["placement_dimensions_m"]["surface"][1]


def test_frontend_equipment_is_exported_from_the_shared_recipes():
    path = Path(__file__).resolve().parents[1] / "frontend/src/data/equipment.generated.json"
    assert json.loads(path.read_text()) == json.loads(json.dumps([a.to_dict() for a in EQUIPMENT_CATALOG.values()]))


def test_projector_mount_adds_height_without_stretching_the_housing():
    asset = EQUIPMENT_CATALOG["projector_standard_01"]
    dimensions = asset.to_dict()["placement_dimensions_m"]
    heights = {
        placement: next(p for p in asset.parts if p.name == f"{placement}_housing").size[1] * dimensions[placement][1]
        for placement in ("surface", "ceiling")
    }
    assert heights["surface"] == pytest.approx(heights["ceiling"])
