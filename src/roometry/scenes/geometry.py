"""Deterministic bounding-box geometry for structurally validated scene data.

World axes match Three.js: Y is up, and positive Y rotation maps local +Z
onto (sin(angle), cos(angle)) in the X/Z plane. All distances are meters.
These functions describe solid boxes, not the empty space inside furniture.
"""

from dataclasses import dataclass
from math import cos, remainder, sin, tau

from roometry.scenes.furniture import SupportSurface
from roometry.scenes.schemas import (
    ObjectDimensions,
    Opening,
    Position,
    RoomDimensions,
    SceneObject,
    Wall,
)

TOLERANCE_M = 0.000001
# This is an angular tolerance in radians, separate from the distance tolerance.
ROTATION_TOLERANCE_RAD = 0.000001

Point2 = tuple[float, float]
Interval = tuple[float, float]

WALL_ROTATIONS_RAD: dict[Wall, float] = {
    "north": 0.0,
    "east": tau * 0.75,
    "south": tau * 0.5,
    "west": tau * 0.25,
}


def _dot(a: Point2, b: Point2) -> float:
    return a[0] * b[0] + a[1] * b[1]


@dataclass(frozen=True)
class Rectangle:
    """An oriented rectangle with two orthonormal axes and positive extents."""

    center: Point2
    axes: tuple[Point2, Point2]
    half_extents: Point2

    @property
    def corners(self) -> tuple[Point2, ...]:
        u, v = self.axes
        width, depth = self.half_extents
        return tuple(
            (
                self.center[0] + x * width * u[0] + z * depth * v[0],
                self.center[1] + x * width * u[1] + z * depth * v[1],
            )
            for x, z in ((-1, -1), (1, -1), (1, 1), (-1, 1))
        )

    def project(self, axis: Point2) -> Interval:
        center = _dot(self.center, axis)
        radius = sum(
            extent * abs(_dot(local_axis, axis))
            for extent, local_axis in zip(self.half_extents, self.axes)
        )
        return center - radius, center + radius


@dataclass(frozen=True)
class ObjectBounds:
    footprint: Rectangle
    vertical: Interval


def catalog_surface_bounds(
    surface: SupportSurface,
    dimensions: ObjectDimensions,
    position: Position,
    rotation: float,
) -> ObjectBounds:
    """Transform a catalog support rectangle to a zero-thickness world plane."""
    c, s = cos(rotation), sin(rotation)
    x = (surface.x_min + surface.x_max) / 2 * dimensions.width
    z = (surface.z_min + surface.z_max) / 2 * dimensions.depth
    y = position.y + surface.y * dimensions.height
    return ObjectBounds(
        Rectangle(
            (position.x + c * x + s * z, position.z - s * x + c * z),
            ((c, -s), (s, c)),
            (
                (surface.x_max - surface.x_min) * dimensions.width / 2,
                (surface.z_max - surface.z_min) * dimensions.depth / 2,
            ),
        ),
        (y, y),
    )


def object_bounds(obj: SceneObject) -> ObjectBounds:
    """Build a rotated box from the world-space bottom-center pivot."""
    c, s = cos(obj.rotation_y_rad), sin(obj.rotation_y_rad)
    return ObjectBounds(
        footprint=Rectangle(
            center=(obj.position.x, obj.position.z),
            axes=((c, -s), (s, c)),
            half_extents=(obj.dimensions_m.width / 2, obj.dimensions_m.depth / 2),
        ),
        vertical=(obj.position.y, obj.position.y + obj.dimensions_m.height),
    )


def intervals_overlap(a: Interval, b: Interval) -> bool:
    """Require positive penetration beyond tolerance; exact touching is allowed.

    Use the minimum separating translation, including when one interval contains
    the other, rather than only the length of their intersection.
    """
    return min(a[1] - b[0], b[1] - a[0]) > TOLERANCE_M


def rectangles_overlap(a: Rectangle, b: Rectangle) -> bool:
    """Separating-axis test on both rectangles' unit axes, not world AABBs."""
    return all(
        intervals_overlap(a.project(axis), b.project(axis))
        for axis in (*a.axes, *b.axes)
    )


def rectangle_contains(outer: Rectangle, inner: Rectangle) -> bool:
    for point in inner.corners:
        offset = point[0] - outer.center[0], point[1] - outer.center[1]
        if any(
            abs(_dot(offset, axis)) > extent + TOLERANCE_M
            for axis, extent in zip(outer.axes, outer.half_extents)
        ):
            return False
    return True


def bounds_inside_room(bounds: ObjectBounds, room: RoomDimensions) -> bool:
    room_floor = Rectangle(
        center=(room.width_m / 2, room.length_m / 2),
        axes=((1.0, 0.0), (0.0, 1.0)),
        half_extents=(room.width_m / 2, room.length_m / 2),
    )
    return (
        rectangle_contains(room_floor, bounds.footprint)
        and bounds.vertical[0] >= -TOLERANCE_M
        and bounds.vertical[1] <= room.height_m + TOLERANCE_M
    )


def boxes_overlap(a: ObjectBounds, b: ObjectBounds) -> bool:
    return intervals_overlap(a.vertical, b.vertical) and rectangles_overlap(
        a.footprint, b.footprint
    )


def surface_is_supported(child: ObjectBounds, support: ObjectBounds) -> bool:
    return abs(
        child.vertical[0] - support.vertical[1]
    ) <= TOLERANCE_M and rectangle_contains(support.footprint, child.footprint)


def wall_span(room: RoomDimensions, wall: Wall) -> float:
    return room.width_m if wall in {"north", "south"} else room.length_m


def opening_inside_wall(opening: Opening, room: RoomDimensions) -> bool:
    return (
        opening.offset_m >= -TOLERANCE_M
        and opening.offset_m + opening.width_m
        <= wall_span(room, opening.wall) + TOLERANCE_M
        and opening.sill_height_m >= -TOLERANCE_M
        and opening.sill_height_m + opening.height_m <= room.height_m + TOLERANCE_M
    )


def wall_is_attached(
    obj: SceneObject, bounds: ObjectBounds, room: RoomDimensions
) -> bool:
    """Check inward facing and the entire local back edge touching the host wall."""
    wall = obj.wall
    if wall is None:
        return False
    angle_error = abs(remainder(obj.rotation_y_rad - WALL_ROTATIONS_RAD[wall], tau))
    if angle_error > ROTATION_TOLERANCE_RAD:
        return False
    back_edge = bounds.footprint.corners[:2]
    match wall:
        case "north":
            return all(abs(z) <= TOLERANCE_M for _, z in back_edge)
        case "south":
            return all(abs(z - room.length_m) <= TOLERANCE_M for _, z in back_edge)
        case "west":
            return all(abs(x) <= TOLERANCE_M for x, _ in back_edge)
        case "east":
            return all(abs(x - room.width_m) <= TOLERANCE_M for x, _ in back_edge)


def wall_object_overlaps_opening(bounds: ObjectBounds, opening: Opening) -> bool:
    """Compare along-wall and vertical intervals; caller checks wall attachment."""
    axis = (1.0, 0.0) if opening.wall in {"north", "south"} else (0.0, 1.0)
    return intervals_overlap(
        bounds.footprint.project(axis),
        (opening.offset_m, opening.offset_m + opening.width_m),
    ) and intervals_overlap(
        bounds.vertical,
        (opening.sill_height_m, opening.sill_height_m + opening.height_m),
    )
