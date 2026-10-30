"""Conservative catalog component boxes shared by validation and reconstruction.

Each active solid part retains its own box, so furniture gaps stay empty. Local
XYZ rotations are enclosed before the object's dimensional scale and Y rotation,
matching the renderer's transform order without pretending to be mesh physics.
Curved/hollow primitives remain conservative boxes at the individual-part level.
"""

from dataclasses import dataclass
from functools import lru_cache
from math import cos, sin
from typing import Protocol

from roometry.scenes.assets import Placement
from roometry.scenes.catalog import ASSET_CATALOG, object_surfaces
from roometry.scenes.furniture import Point
from roometry.scenes.geometry import (
    TOLERANCE_M,
    ObjectBounds,
    Rectangle,
    boxes_overlap,
    catalog_surface_bounds,
    surface_is_supported,
)
from roometry.scenes.schemas import ObjectDimensions, Position


class CatalogPose(Protocol):
    @property
    def asset_id(self) -> str: ...

    @property
    def placement(self) -> Placement: ...

    @property
    def dimensions_m(self) -> ObjectDimensions: ...

    @property
    def position(self) -> Position: ...

    @property
    def rotation_y_rad(self) -> float: ...

    @property
    def support_surface_id(self) -> str | None: ...


@dataclass(frozen=True)
class ComponentBounds:
    name: str
    bounds: ObjectBounds


@lru_cache(maxsize=256)
def _local_components(
    asset_id: str, placement: Placement
) -> tuple[tuple[str, Point, Point], ...]:
    parts = []
    for part in ASSET_CATALOG[asset_id].parts:
        if not part.collision or (part.placements and placement not in part.placements):
            continue
        x, y, z = part.rotation
        cx, cy, cz, sx, sy, sz = cos(x), cos(y), cos(z), sin(x), sin(y), sin(z)
        # Three.js Euler XYZ, followed by parent nonuniform scale (not vice versa).
        rows = (
            (cy * cz, -cy * sz, sy),
            (cx * sz + sx * sy * cz, cx * cz - sx * sy * sz, -sx * cy),
            (sx * sz - cx * sy * cz, sx * cz + cx * sy * sz, cx * cy),
        )
        ex, ey, ez = (
            sum(abs(v) * d / 2 for v, d in zip(row, part.size)) for row in rows
        )
        extents: Point = (ex, ey, ez)
        parts.append((part.name, part.at, extents))
    if not parts:
        raise ValueError("The catalog configuration has no solid components.")
    return tuple(parts)


def component_bounds(obj: CatalogPose) -> tuple[ComponentBounds, ...]:
    width, height, depth = (
        obj.dimensions_m.width,
        obj.dimensions_m.height,
        obj.dimensions_m.depth,
    )
    c, s = cos(obj.rotation_y_rad), sin(obj.rotation_y_rad)
    result = []
    for name, at, extent in _local_components(obj.asset_id, obj.placement):
        x, y, z = at[0] * width, at[1] * height, at[2] * depth
        result.append(
            ComponentBounds(
                name,
                ObjectBounds(
                    Rectangle(
                        (
                            obj.position.x + c * x + s * z,
                            obj.position.z - s * x + c * z,
                        ),
                        ((c, -s), (s, c)),
                        (extent[0] * width, extent[2] * depth),
                    ),
                    (
                        obj.position.y + y - extent[1] * height,
                        obj.position.y + y + extent[1] * height,
                    ),
                ),
            )
        )
    return tuple(result)


def first_component_overlap(
    left: tuple[ComponentBounds, ...], right: tuple[ComponentBounds, ...]
) -> tuple[str, str] | None:
    for a in left:
        for b in right:
            if boxes_overlap(a.bounds, b.bounds):
                return a.name, b.name
    return None


def contact_bounds(obj: CatalogPose) -> ObjectBounds | None:
    """Envelope of the solid parts touching the bottom plane, not upper overhang."""
    parts = [
        part.bounds.footprint
        for part in component_bounds(obj)
        if abs(part.bounds.vertical[0] - obj.position.y) <= TOLERANCE_M
    ]
    if not parts:
        return None
    c, s = cos(obj.rotation_y_rad), sin(obj.rotation_y_rad)
    u, v = (c, -s), (s, c)
    projections = [
        (min(p.project(axis)[0] for p in parts), max(p.project(axis)[1] for p in parts))
        for axis in (u, v)
    ]
    x, z = ((lo + hi) / 2 for lo, hi in projections)
    return ObjectBounds(
        Rectangle(
            (x * u[0] + z * v[0], x * u[1] + z * v[1]),
            (u, v),
            (
                (projections[0][1] - projections[0][0]) / 2,
                (projections[1][1] - projections[1][0]) / 2,
            ),
        ),
        (obj.position.y, obj.position.y),
    )


def catalog_supports(child: CatalogPose, support: CatalogPose) -> bool:
    contact = contact_bounds(child)
    if contact is None:
        return False
    surfaces = object_surfaces(support.asset_id, support.placement)
    if ASSET_CATALOG[child.asset_id].object_type == 'countertop':
        # One structural slab may bear on multiple coplanar areas of this SAME
        # approved host. Every real underside component must be supported; an
        # L's empty bay and the space between unrelated cabinets stay empty.
        selected = next((s for s in surfaces if s.name == child.support_surface_id), None)
        if child.support_surface_id is not None and selected is None:
            return False
        height = selected.y if selected else max((s.y for s in surfaces), default=-1)
        areas = [catalog_surface_bounds(s, support.dimensions_m, support.position, support.rotation_y_rad)
                 for s in surfaces if abs(s.y-height) <= TOLERANCE_M]
        contacts = [p.bounds for p in component_bounds(child) if abs(p.bounds.vertical[0]-child.position.y) <= TOLERANCE_M]
        return bool(contacts) and all(any(surface_is_supported(part, area) for area in areas) for part in contacts)
    if child.support_surface_id is not None:
        surfaces = tuple(s for s in surfaces if s.name == child.support_surface_id)
    elif surfaces:
        # Backward-compatible null selection means the uppermost declared areas.
        top = max(s.y for s in surfaces)
        surfaces = tuple(s for s in surfaces if s.y == top)
    return any(
        surface_is_supported(
            contact,
            catalog_surface_bounds(
                s,
                support.dimensions_m,
                support.position,
                support.rotation_y_rad,
            ),
        )
        for s in surfaces
    )
