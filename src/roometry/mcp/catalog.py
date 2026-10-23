"""Read-only MCP views over the same catalog used by assembly and rendering.

Nominal model dimensions are examples, not measurements of a user's furniture.
There is no second JSON catalog, inventory, pricing, or scene mutation here.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from roometry.scenes.assets import ASSET_REGISTRY, ObjectType, Placement
from roometry.scenes.catalog import ASSET_CATALOG, dimensions_for, object_surfaces
from roometry.scenes.furniture import Category, FurnitureAsset, MaterialRole, Point
from roometry.scenes.schemas import Color, PositiveDistance

ItemId = Annotated[str, Field(min_length=1, max_length=96, pattern=r"^[a-z][a-z0-9_]*$")]
Query = Annotated[str, Field(max_length=120)]
PageSize = Annotated[int, Field(strict=True, ge=1, le=20)]
Offset = Annotated[int, Field(strict=True, ge=0, le=10_000)]


class CatalogItemNotFound(ValueError):
    """Expected lookup failure, safe to expose over MCP."""


class CatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, revalidate_instances="always")


class CatalogSearch(CatalogModel):
    query: Query | None = None
    category: Category | None = None
    object_type: ObjectType | None = None
    placement: Placement | None = None
    max_width_m: PositiveDistance | None = None
    max_height_m: PositiveDistance | None = None
    max_depth_m: PositiveDistance | None = None
    limit: PageSize = 8
    offset: Offset = 0


class Dimensions(CatalogModel):
    width: PositiveDistance
    height: PositiveDistance
    depth: PositiveDistance


class CatalogItem(CatalogModel):
    asset_id: ItemId
    object_type: ObjectType
    label: str
    category: Category
    color: Color
    fixed_by_default: bool
    dimensions_source: Literal["catalog_nominal"] = "catalog_nominal"
    # A dimension filter applies to an entire configuration, never mixed mounts.
    configurations: dict[Placement, Dimensions]


class SearchResults(CatalogModel):
    schema_version: Literal["catalog-v1"] = "catalog-v1"
    items: tuple[CatalogItem, ...]
    total_matches: int = Field(ge=0)
    offset: Offset
    next_offset: Offset | None


class SupportArea(CatalogModel):
    """Upward object support in unrotated model-local meters, bottom center origin."""

    name: str
    part: str
    height_m: float
    x_min_m: float
    x_max_m: float
    z_min_m: float
    z_max_m: float


class MountingConfiguration(CatalogModel):
    dimensions_m: Dimensions
    anchor_at_normalized: Point = Field(description="Catalog attachment point: x/z normalized to [-.5,.5], y to [0,1].")
    anchor_normal: Point
    support_surfaces: tuple[SupportArea, ...]


class FurnitureDetails(CatalogModel):
    schema_version: Literal["catalog-v1"] = "catalog-v1"
    item: CatalogItem
    configurations: dict[Placement, MountingConfiguration]
    variant_asset_ids: tuple[ItemId, ...] = Field(description="Other catalog models sharing this semantic object type; not retailer inventory.")
    material_roles: tuple[MaterialRole, ...]
    coordinate_system: Literal["x_right_y_up_z_front_bottom_center"] = "x_right_y_up_z_front_bottom_center"


class CategorySummary(CatalogModel):
    category: Category
    item_count: int = Field(ge=1)
    object_types: tuple[ObjectType, ...]


class CatalogCategories(CatalogModel):
    schema_version: Literal["catalog-v1"] = "catalog-v1"
    total_items: int = Field(ge=0)
    categories: tuple[CategorySummary, ...]


def _dimensions(asset_id: str, placement: Placement) -> Dimensions:
    width, height, depth = dimensions_for(asset_id, placement)
    return Dimensions(width=width, height=height, depth=depth)


def _item(asset: FurnitureAsset, configurations: dict[Placement, Dimensions]) -> CatalogItem:
    return CatalogItem(
        asset_id=asset.asset_id, object_type=asset.object_type, label=asset.label,
        category=asset.category, color=asset.color,
        fixed_by_default=ASSET_REGISTRY[asset.asset_id].fixed_by_default,
        configurations=configurations,
    )


def search_furniture(filters: CatalogSearch | None = None) -> SearchResults:
    """AND-combine filters; size limits use unrotated placement-specific sizes."""
    filters = CatalogSearch.model_validate(filters) if filters is not None else CatalogSearch()
    tokens = (filters.query or "").casefold().replace("_", " ").split()
    matches: list[CatalogItem] = []
    for asset_id in sorted(ASSET_CATALOG):
        asset = ASSET_CATALOG[asset_id]
        if filters.category is not None and asset.category != filters.category:
            continue
        if filters.object_type is not None and asset.object_type != filters.object_type:
            continue
        haystack = f"{asset.asset_id} {asset.label} {asset.object_type} {asset.category}".casefold().replace("_", " ")
        if any(token not in haystack for token in tokens):
            continue
        configurations: dict[Placement, Dimensions] = {}
        for placement in sorted(ASSET_REGISTRY[asset_id].placements):
            if filters.placement is not None and filters.placement != placement:
                continue
            dimensions = _dimensions(asset_id, placement)
            if any(maximum is not None and getattr(dimensions, axis) > maximum
                   for axis, maximum in (("width", filters.max_width_m), ("height", filters.max_height_m), ("depth", filters.max_depth_m))):
                continue
            configurations[placement] = dimensions
        if configurations:
            matches.append(_item(asset, configurations))
    end = min(filters.offset + filters.limit, len(matches))
    return SearchResults(
        items=tuple(matches[filters.offset:end]), total_matches=len(matches),
        offset=filters.offset, next_offset=end if end < len(matches) else None,
    )


def get_furniture_details(asset_id: ItemId) -> FurnitureDetails:
    """Return authoritative mounting/support metadata, without renderer meshes."""
    asset = ASSET_CATALOG.get(asset_id)
    if asset is None:
        raise CatalogItemNotFound("catalog_item_not_found: No catalog item has that asset_id.")
    recipe = asset.to_dict()
    configurations: dict[Placement, MountingConfiguration] = {}
    for placement in sorted(ASSET_REGISTRY[asset_id].placements):
        dimensions = _dimensions(asset_id, placement)
        anchor = recipe["placement_anchors"][placement]
        configurations[placement] = MountingConfiguration(
            dimensions_m=dimensions, anchor_at_normalized=anchor["at"], anchor_normal=anchor["normal"],
            support_surfaces=tuple(SupportArea(
                name=area.name, part=area.part, height_m=area.y * dimensions.height,
                x_min_m=area.x_min * dimensions.width, x_max_m=area.x_max * dimensions.width,
                z_min_m=area.z_min * dimensions.depth, z_max_m=area.z_max * dimensions.depth,
            ) for area in object_surfaces(asset_id, placement)),
        )
    return FurnitureDetails(
        item=_item(asset, {p: c.dimensions_m for p, c in configurations.items()}),
        configurations=configurations,
        variant_asset_ids=tuple(sorted(a.asset_id for a in ASSET_CATALOG.values()
                                      if a.object_type == asset.object_type and a.asset_id != asset_id)),
        material_roles=tuple(sorted({part.material for part in asset.parts})),
    )


def list_furniture_categories() -> CatalogCategories:
    return CatalogCategories(
        total_items=len(ASSET_CATALOG),
        categories=tuple(CategorySummary(
            category=category,
            item_count=sum(a.category == category for a in ASSET_CATALOG.values()),
            object_types=tuple(sorted({a.object_type for a in ASSET_CATALOG.values() if a.category == category})),
        ) for category in sorted({a.category for a in ASSET_CATALOG.values()})),
    )
