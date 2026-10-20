"""Shared, read-only model lookup; no MCP transport or provider dependencies."""

from types import MappingProxyType

from roometry.scenes.assets import ASSET_REGISTRY, ObjectType, Placement
from roometry.scenes.decor import DECOR_CATALOG
from roometry.scenes.equipment import EQUIPMENT_CATALOG
from roometry.scenes.fixture_models import FIXTURE_CATALOG
from roometry.scenes.furniture import (
    FURNITURE_CATALOG,
    FurnitureAsset,
    Point,
    SupportSurface,
)

ASSET_CATALOG = MappingProxyType(
    {**FURNITURE_CATALOG, **EQUIPMENT_CATALOG, **FIXTURE_CATALOG, **DECOR_CATALOG}
)


def candidates(
    object_type: ObjectType, placement: Placement | None = None
) -> tuple[FurnitureAsset, ...]:
    return tuple(
        asset
        for asset in ASSET_CATALOG.values()
        if asset.object_type == object_type
        and (
            placement is None or placement in ASSET_REGISTRY[asset.asset_id].placements
        )
    )


def dimensions_for(asset_id: str, placement: Placement) -> Point:
    asset = ASSET_CATALOG[asset_id]
    if placement not in ASSET_REGISTRY[asset_id].placements:
        raise ValueError("The model does not support this placement.")
    return dict(asset.placement_dimensions_m).get(placement, asset.dimensions_m)


def object_surfaces(asset_id: str, placement: Placement) -> tuple[SupportSurface, ...]:
    asset = ASSET_CATALOG[asset_id]
    definition = ASSET_REGISTRY[asset_id]
    if not definition.supplies_surface or placement not in definition.placements:
        return ()
    parts = {
        p.name for p in asset.parts if not p.placements or placement in p.placements
    }
    return tuple(
        s for s in asset.surfaces if s.purpose == "objects" and s.part in parts
    )
