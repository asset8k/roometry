"""Catalog-only redesign operations, separate from stricter manual-edit ancestry."""

from math import cos, sin

from roometry.scenes.assets import ASSET_REGISTRY
from roometry.scenes.catalog import ASSET_CATALOG, dimensions_for, object_surfaces
from roometry.scenes.schemas import (
    MaterialColors,
    ObjectDimensions,
    Position,
    RoomScene,
    SceneObject,
)
from roometry.vision.review_policy import appearance_controls


def catalog_options():
    """Compact authoritative choices; no meshes, invented sizes or prices."""
    return [
        {
            "asset_id": a.asset_id,
            "type": a.object_type,
            "label": a.label,
            "dimensions_m": dict(
                zip(
                    ("width", "height", "depth"),
                    dimensions_for(a.asset_id, "floor"),
                    strict=True,
                )
            ),
            "color": a.color,
        }
        for a in ASSET_CATALOG.values()
        if "floor" in ASSET_REGISTRY[a.asset_id].placements
        and not ASSET_REGISTRY[a.asset_id].fixed_by_default
        and a.category in {"furniture", "decor", "lighting"}
    ]


def catalog_object(operation):
    asset = ASSET_CATALOG.get(operation.asset_id)
    if (
        asset is None
        or "floor" not in ASSET_REGISTRY[asset.asset_id].placements
        or ASSET_REGISTRY[asset.asset_id].fixed_by_default
    ):
        raise ValueError("Choose a movable floor model from the approved catalog.")
    width, height, depth = dimensions_for(asset.asset_id, "floor")
    return SceneObject(
        id=operation.object_id,
        type=asset.object_type,
        asset_id=asset.asset_id,
        position=Position(x=operation.position.x, y=0.0, z=operation.position.z),
        rotation_y_rad=operation.rotation_y_rad,
        dimensions_m=ObjectDimensions(width=width, height=height, depth=depth),
        color=operation.color or asset.color,
        placement="floor",
        support_object_id=None,
        wall=None,
        opening_id=None,
        fixed=False,
        locked=False,
        provenance="agent_added",
        measurements_source="catalog",
    )


def apply_inventory(base, proposal):
    candidate = base.model_copy(deep=True)
    objects = {o.id: o for o in candidate.objects}
    removed = set(proposal.removals)
    for key in removed | {r.object_id for r in proposal.replacements}:
        old = objects.get(key)
        if old is None or old.fixed or old.locked:
            raise ValueError(
                "Removal or replacement needs an existing movable, unlocked object."
            )
        if key not in removed and old.placement != "floor":
            raise ValueError(
                "Only floor furniture can be replaced; keep attached fixtures."
            )
    for obj in candidate.objects:
        if obj.support_object_id in removed and obj.id not in removed:
            raise ValueError(
                "Removing a support requires explicitly removing its movable children too."
            )
    candidate.objects = [o for o in candidate.objects if o.id not in removed]
    for replacement in proposal.replacements:
        old = objects[replacement.object_id]
        operation = replacement.model_copy(
            update={
                "position": replacement.position or old.position,
                "rotation_y_rad": old.rotation_y_rad
                if replacement.rotation_y_rad is None
                else replacement.rotation_y_rad,
                "color": replacement.color or old.color,
            }
        )
        new = catalog_object(operation)
        if old.type != new.type:
            raise ValueError(
                "A replacement must preserve the item's function; use removal and addition for another function."
            )
        if old.material_colors:
            controls = appearance_controls(new)
            new.material_colors = MaterialColors(
                **{
                    key: value
                    for key, value in old.material_colors.model_dump().items()
                    if value is not None and key in controls
                }
            )
        children = [o for o in candidate.objects if o.support_object_id == old.id]
        areas = object_surfaces(new.asset_id, "floor")
        for child in children:
            if child.fixed or child.locked:
                raise ValueError(
                    "Replacing this support would affect a fixed or locked child."
                )
            area = next((a for a in areas if a.name == child.support_surface_id), None)
            if area is None and len(areas) == 1:
                area = areas[0]
            if area is None:
                raise ValueError(
                    "Replacement needs a compatible catalog support area for every child."
                )
            dx, dz = (
                child.position.x - old.position.x,
                child.position.z - old.position.z,
            )
            angle = new.rotation_y_rad - old.rotation_y_rad
            child.position = Position(
                x=new.position.x + cos(angle) * dx + sin(angle) * dz,
                y=area.y * new.dimensions_m.height,
                z=new.position.z - sin(angle) * dx + cos(angle) * dz,
            )
            child.rotation_y_rad += angle
            child.support_surface_id = area.name
        candidate.objects[candidate.objects.index(old)] = new
    used = {o.id for o in base.objects} | {o.id for o in base.openings}
    for addition in proposal.additions:
        if addition.object_id in used:
            raise ValueError("New furniture needs a new unique scene ID.")
        used.add(addition.object_id)
        candidate.objects.append(catalog_object(addition))
    return RoomScene.model_validate(candidate)


def utility_issues(original, candidate):
    """Do not game floor-space scores by deleting the room's primary functions."""

    def capacity(scene, types, measure):
        return sum(measure(o) for o in scene.objects if o.type in types)

    from roometry.scenes.scoring import STORAGE_TYPES

    groups = [
        (
            {"bed"},
            "sleeping space",
            lambda o: o.dimensions_m.width * o.dimensions_m.depth,
            0.75,
        ),
        (
            {"sofa", "armchair", "chair", "stool", "bench"},
            "seating",
            lambda o: (
                max(1.0, o.dimensions_m.width / 0.65)
                if o.type in {"sofa", "bench"}
                else 1.0
            ),
            0.65,
        ),
        (
            {"table"},
            "dining space",
            lambda o: o.dimensions_m.width * o.dimensions_m.depth,
            0.65,
        ),
        (
            {"desk"},
            "workspace",
            lambda o: o.dimensions_m.width * o.dimensions_m.depth,
            0.75,
        ),
        (
            STORAGE_TYPES,
            "storage capacity",
            lambda o: (
                o.dimensions_m.width * o.dimensions_m.depth * o.dimensions_m.height
                if o.placement in {"floor", "wall"}
                else 0.0
            ),
            0.85,
        ),
    ]
    return [
        f"Keep useful {label}; replace it compactly instead of deleting the room's function."
        for types, label, measure, fraction in groups
        if capacity(original, types, measure) > 0
        and capacity(candidate, types, measure) + 1e-8
        < fraction * capacity(original, types, measure)
    ]
