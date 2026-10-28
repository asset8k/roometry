"""Catalog-only redesign operations, separate from stricter manual-edit ancestry."""

from math import cos, sin
from types import SimpleNamespace

from roometry.optimization.schemas import CatalogAddition
from roometry.scenes.assets import ASSET_REGISTRY
from roometry.scenes.catalog import ASSET_CATALOG, dimensions_for, object_surfaces
from roometry.scenes.geometry import WALL_ROTATIONS_RAD, catalog_surface_bounds
from roometry.scenes.schemas import (
    MaterialColors,
    ObjectDimensions,
    Position,
    RoomScene,
    SceneObject,
)
from roometry.vision.review_policy import appearance_controls
from roometry.vision.context_budget import shortlist


def catalog_options(query="", *, required_ids=(), object_types=None):
    """Relevant variants of every movable type, retaining mounts and current IDs.

    The complete catalogue stays searchable through MCP; growing its size must
    not force every chat request to carry every variant's support metadata.
    """
    options = [
        {
            "asset_id": a.asset_id,
            "type": a.object_type,
            "label": a.label,
            "dimensions_m": dict(
                zip(
                    ("width", "height", "depth"),
                    a.dimensions_m,
                    strict=True,
                )
            ),
            "color": a.color,
            "placements": sorted(
                ASSET_REGISTRY[a.asset_id].placements & {"floor", "wall", "surface"}
            ),
            "placement_dimensions_m": dict(a.placement_dimensions_m),
            "support_surfaces": {
                placement: [
                    {
                        "id": s.name,
                        "y": s.y,
                        "x_min": s.x_min,
                        "x_max": s.x_max,
                        "z_min": s.z_min,
                        "z_max": s.z_max,
                    }
                    for s in object_surfaces(a.asset_id, placement)
                ]
                for placement in ("floor", "wall")
                if object_surfaces(a.asset_id, placement)
            },
        }
        for a in ASSET_CATALOG.values()
        if ASSET_REGISTRY[a.asset_id].placements & {"floor", "wall", "surface"}
        and not ASSET_REGISTRY[a.asset_id].fixed_by_default
        and (object_types is None or a.object_type in object_types)
    ]
    groups = {}
    for option in options:
        groups.setdefault(option['type'], []).append({
            'asset_id': option['asset_id'], 'label': option['label'],
            'configurations': {p: {} for p in option['placements']},
        })
    context = SimpleNamespace(label=query, estimated_dimensions_m=None)
    chosen = {m['asset_id'] for models in groups.values()
              for m in shortlist(models, context, limit=2, required=required_ids)}
    # Two text matches alone hid low/medium shelves behind tall bookcases in
    # broad "improve storage" requests. Keep one per practical height band.
    shelves = [option for option in options if option['type'] == 'shelf']
    for low, high in ((0., 1.), (1., 1.4), (1.4, float('inf'))):
        band = [m for m in groups.get('shelf', []) if any(
            o['asset_id'] == m['asset_id'] and low < o['dimensions_m']['height'] <= high for o in shelves)]
        chosen.update(m['asset_id'] for m in shortlist(band, context, limit=1))
    return [option for option in options if option['asset_id'] in chosen]


def scene_supports(scene):
    """Exact world-space support choices, without meshes or private observations."""
    return {
        obj.id: [
            {
                "surface_id": area.name,
                "height_m": bounds.vertical[0],
                "corners_xz": bounds.footprint.corners,
            }
            for area in object_surfaces(obj.asset_id, obj.placement)
            for bounds in [
                catalog_surface_bounds(
                    area, obj.dimensions_m, obj.position, obj.rotation_y_rad
                )
            ]
        ]
        for obj in scene.objects
        if object_surfaces(obj.asset_id, obj.placement)
    }


def catalog_object(operation: CatalogAddition, room, objects):
    asset = ASSET_CATALOG.get(operation.asset_id)
    if (
        asset is None
        or operation.placement not in ASSET_REGISTRY[asset.asset_id].placements
        or ASSET_REGISTRY[asset.asset_id].fixed_by_default
    ):
        raise ValueError(
            "Choose an approved movable catalog model and one of its supported placements."
        )
    width, height, depth = dimensions_for(asset.asset_id, operation.placement)
    x, y, z, angle = (
        operation.position.x,
        0.0,
        operation.position.z,
        operation.rotation_y_rad,
    )
    surface_id = None
    if operation.placement == "wall":
        y, angle = operation.elevation_m, WALL_ROTATIONS_RAD[operation.wall]
        if operation.wall == "north":
            z = depth / 2
        elif operation.wall == "south":
            z = room.length_m - depth / 2
        elif operation.wall == "west":
            x = depth / 2
        else:
            x = room.width_m - depth / 2
    elif operation.placement == "surface":
        support = objects.get(operation.support_object_id)
        if support is None or support.placement not in {"floor", "wall"}:
            raise ValueError(
                "A surface addition needs a retained floor or wall support; nested supports are not allowed."
            )
        areas = object_surfaces(support.asset_id, support.placement)
        area = next((s for s in areas if s.name == operation.support_surface_id), None)
        if operation.support_surface_id is None and len(areas) == 1:
            area = areas[0]
        if area is None:
            raise ValueError(
                "Choose an explicit approved support surface when the support has multiple areas."
            )
        y = support.position.y + area.y * support.dimensions_m.height
        surface_id = area.name
    return SceneObject(
        id=operation.object_id,
        type=asset.object_type,
        asset_id=asset.asset_id,
        position=Position(x=x, y=y, z=z),
        rotation_y_rad=angle,
        dimensions_m=ObjectDimensions(width=width, height=height, depth=depth),
        color=operation.color or asset.color,
        placement=operation.placement,
        support_object_id=operation.support_object_id,
        support_surface_id=surface_id,
        wall=operation.wall,
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
        operation = CatalogAddition.model_validate(
            {
                "object_id": replacement.object_id,
                "asset_id": replacement.asset_id,
                "position": {
                    "x": (replacement.position or old.position).x,
                    "z": (replacement.position or old.position).z,
                },
                "rotation_y_rad": old.rotation_y_rad
                if replacement.rotation_y_rad is None
                else replacement.rotation_y_rad,
                "color": replacement.color or old.color,
            }
        )
        new = catalog_object(operation, candidate.room, {})
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
    # Resolve hosts before their children regardless of provider list order.
    available = {o.id: o for o in candidate.objects}
    for addition in sorted(
        proposal.additions, key=lambda item: item.placement == "surface"
    ):
        obj = catalog_object(addition, candidate.room, available)
        candidate.objects.append(obj)
        available[obj.id] = obj
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
