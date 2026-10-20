"""Fixture catalog coverage, cavities, support definitions and mounting data."""

import json
import math
from pathlib import Path
from typing import get_args

import pytest

from roometry.scenes.assets import ASSET_REGISTRY
from roometry.scenes.fixture_models import FIXTURE_CATALOG
from roometry.scenes.furniture import MaterialRole, Shape
from roometry.scenes.validation import validate_geometry
from roometry.scenes.schemas import RoomScene

FIXTURE_TYPES = {
    "kitchen_cabinet", "kitchen_counter", "wall_cabinet", "extractor_hood",
    "refrigerator", "cooker", "dishwasher", "washing_machine", "dryer",
    "microwave", "kettle", "toaster", "sink", "toilet", "bathtub", "shower",
    "vanity", "towel_rack", "water_heater", "drying_rack",
}
CONFIGURATIONS = [(asset, placement) for asset in FIXTURE_CATALOG.values()
                  for placement in sorted(ASSET_REGISTRY[asset.asset_id].placements)]


def test_twenty_types_and_every_reserved_fixture_variant_are_covered():
    assert {a.object_type for a in FIXTURE_CATALOG.values()} == FIXTURE_TYPES
    assert set(FIXTURE_CATALOG) == {key for key, a in ASSET_REGISTRY.items() if a.object_type in FIXTURE_TYPES}


@pytest.mark.parametrize("asset,placement", CONFIGURATIONS,
                         ids=[f"{a.asset_id}-{p}" for a, p in CONFIGURATIONS])
def test_fixture_configuration_geometry_and_metadata(asset, placement):
    assert len(asset.parts) == len({p.name for p in asset.parts})
    assert ASSET_REGISTRY[asset.asset_id].object_type == asset.object_type
    exported = asset.to_dict()
    assert set(exported["placement_anchors"]) == set(exported["placements"])
    assert set(exported["placement_dimensions_m"]) == set(exported["placements"])
    assert all(math.isfinite(d) and d > 0 for d in exported["placement_dimensions_m"][placement])
    active = [p for p in asset.parts if not p.placements or placement in p.placements]
    assert any(p.material in {"primary", "primary_metal", "primary_ceramic"} for p in active)
    for part in active:
        assert part.shape in get_args(Shape)
        assert part.material in get_args(MaterialRole)
        assert all(d > 0 for d in part.size)
        assert all(math.isfinite(v) for v in (*part.size, *part.at, *part.rotation))
        assert set(part.placements) <= ASSET_REGISTRY[asset.asset_id].placements
        assert 0 < part.top_scale <= 1 and 0 < part.bottom_scale <= 1
        if part.shape == "tube":
            assert 0 < part.wall_thickness < 0.5 * min(part.top_scale, part.bottom_scale)
        if part.shape == "basin":
            assert part.profile[0] == (0, 0)
            assert part.profile[-1][0] == 0
            assert 0 < part.profile[-1][1] < 0.5
            assert all(0 <= r <= 0.5 and 0 <= y <= 1 for r, y in part.profile)
            assert all(r > 0 for r, y in part.profile if y >= 0.95)
        x, y, z = part.rotation
        cx, cy, cz, sx, sy, sz = math.cos(x), math.cos(y), math.cos(z), math.sin(x), math.sin(y), math.sin(z)
        rotation = ((cy * cz, -cy * sz, sy),
                    (cx * sz + sx * sy * cz, cx * cz - sx * sy * sz, -sx * cy),
                    (sx * sz - cx * sy * cz, sx * cz + cx * sy * sz, cx * cy))
        for row, at, low, high in zip(rotation, part.at, (-0.5, 0, -0.5), (0.5, 1, 0.5)):
            extent = sum(abs(c) * d / 2 for c, d in zip(row, part.size))
            assert at - extent >= low - 1e-8, part.name
            assert at + extent <= high + 1e-8, part.name
    for support in asset.surfaces:
        parent = next(p for p in active if p.name == support.part)
        assert parent.collision
        assert support.y == pytest.approx(parent.at[1] + parent.size[1] / 2)
        assert support.x_min < support.x_max
        assert support.z_min < support.z_max
        assert parent.at[0] - parent.size[0] / 2 <= support.x_min <= support.x_max <= parent.at[0] + parent.size[0] / 2
        assert parent.at[2] - parent.size[2] / 2 <= support.z_min <= support.z_max <= parent.at[2] + parent.size[2] / 2
    if ASSET_REGISTRY[asset.asset_id].supplies_surface:
        assert asset.surfaces


def test_counter_variants_keep_a_consistent_working_height_and_no_support_over_a_basin():
    plain = FIXTURE_CATALOG["kitchen_counter_standard_01"]
    sink = FIXTURE_CATALOG["kitchen_counter_sink_01"]
    hob = FIXTURE_CATALOG["kitchen_counter_hob_01"]
    assert sink.surfaces[0].y * sink.dimensions_m[1] == pytest.approx(plain.dimensions_m[1], abs=0.01)
    assert next(p for p in hob.parts if p.name == "worktop").at[1] * hob.dimensions_m[1] == pytest.approx(plain.dimensions_m[1], abs=0.025)
    assert not any(s.x_min <= 0 <= s.x_max and s.z_min <= 0 <= s.z_max for s in sink.surfaces)
    assert not hob.surfaces


def test_stack_contains_both_machines_without_squashing_front_facing_parts():
    stack = FIXTURE_CATALOG["washing_machine_stack_01"]
    washer = FIXTURE_CATALOG["washing_machine_standard_01"]
    assert any(p.name == "dryer_door_glass" for p in stack.parts)
    standalone = next(p for p in washer.parts if p.name == "door_glass")
    stacked = next(p for p in stack.parts if p.name == "washer_door_glass")
    assert stacked.size[2] * stack.dimensions_m[1] == pytest.approx(standalone.size[2] * washer.dimensions_m[1], rel=0.025)
    assert stacked.size[1] == standalone.size[1]  # Its depth stays unchanged.


def test_frontend_fixture_recipes_match_the_backend_catalog():
    path = Path(__file__).resolve().parents[1] / "frontend/src/data/fixtures.generated.json"
    assert json.loads(path.read_text()) == json.loads(json.dumps([a.to_dict() for a in FIXTURE_CATALOG.values()]))


@pytest.mark.parametrize("name", ("kitchen_catalog_scene.json", "bathroom_catalog_scene.json"))
def test_catalog_room_examples_validate_and_are_exported_without_changes(name):
    root = Path(__file__).resolve().parents[1]
    scene = RoomScene.model_validate_json((root / "data/fixtures" / name).read_text())
    report = validate_geometry(scene)
    assert report.valid, report.errors
    assert json.loads((root / "frontend/src/data" / name).read_text()) == scene.model_dump(mode="json")
