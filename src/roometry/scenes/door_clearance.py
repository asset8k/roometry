"""Advisory room-side door checks against catalog solid components.

An access zone is a planning heuristic, not an accessibility/code assessment.
Known inward single-leaf doors sweep a quarter disk. Outward sweeps require
geometry outside this room, and unknown swings are never silently assumed.
"""

from math import hypot
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from roometry.scenes.assets import ASSET_REGISTRY
from roometry.scenes.collision import component_bounds
from roometry.scenes.geometry import (
    TOLERANCE_M,
    Point2,
    Rectangle,
    intervals_overlap,
    opening_inside_wall,
    rectangles_overlap,
)
from roometry.scenes.schemas import Identifier, Opening, RoomDimensions, RoomScene

DoorClearanceRule = Literal[
    "door_access_blocked",
    "door_swing_blocked",
    "door_swing_unknown",
    "door_swing_outside_room",
    "door_clearance_unassessed",
]


class DoorClearancePolicy(BaseModel):
    """MVP heuristics in meters; callers can supply a different bounded policy."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, revalidate_instances="always")

    access_depth_m: float = Field(default=0.8, gt=0, le=5, allow_inf_nan=False)
    floor_gap_m: float = Field(default=0.02, ge=0, le=0.1, allow_inf_nan=False)


class DoorClearanceIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: DoorClearanceRule
    entity_ids: tuple[Identifier, ...]
    message: str


class DoorCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    door_id: Identifier
    access_status: Literal["clear", "blocked", "unassessed"]
    swing_status: Literal[
        "clear", "blocked", "unknown", "not_applicable", "outside_room", "unassessed"
    ]
    access_object_ids: tuple[Identifier, ...] = ()
    swing_object_ids: tuple[Identifier, ...] = ()


class DoorClearanceReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: Literal["door-clearance-v1"] = "door-clearance-v1"
    policy: DoorClearancePolicy
    doors: tuple[DoorCheck, ...]
    issues: tuple[DoorClearanceIssue, ...]


def _wall_frame(door: Opening, room: RoomDimensions) -> tuple[Point2, Point2, Point2]:
    """Wall origin, direction of increasing offset, inward normal in world X/Z."""
    return {
        "north": ((0., 0.), (1., 0.), (0., 1.)),
        "south": ((0., room.length_m), (1., 0.), (0., -1.)),
        "west": ((0., 0.), (0., 1.), (1., 0.)),
        "east": ((room.width_m, 0.), (0., 1.), (-1., 0.)),
    }[door.wall]


def _clip_positive(points: list[Point2], axis: int) -> list[Point2]:
    """Clip a convex polygon to one of the quarter disk's two half planes."""
    clipped: list[Point2] = []
    for a, b in zip(points, points[1:] + points[:1]):
        a_in, b_in = a[axis] >= 0, b[axis] >= 0
        if a_in != b_in:
            t = a[axis] / (a[axis] - b[axis])
            clipped.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
        if b_in:
            clipped.append(b)
    return clipped


def _sweep_overlaps(footprint: Rectangle, hinge: Point2, along: Point2, inward: Point2, radius: float) -> bool:
    """Positive intersection with a 90-degree sweep, without a square-box proxy.

    In the hinge frame the swept region is u>=0, v>=0, u²+v²<=radius².
    Clip the component rectangle to that quadrant, then find its closest point
    to the hinge. Edge/arc touching within the geometry tolerance is allowed.
    """
    polygon = []
    for x, z in footprint.corners:
        dx, dz = x - hinge[0], z - hinge[1]
        polygon.append((dx * along[0] + dz * along[1], dx * inward[0] + dz * inward[1]))
    polygon = _clip_positive(_clip_positive(polygon, 0), 1)
    if len(polygon) < 3 or any(max(p[i] for p in polygon) - min(p[i] for p in polygon) <= TOLERANCE_M for i in (0, 1)):
        return False
    twice_area = abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(polygon, polygon[1:] + polygon[:1])))
    if twice_area <= TOLERANCE_M ** 2:
        return False
    nearest = float("inf")
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        dx, dz = b[0] - a[0], b[1] - a[1]
        length_sq = dx * dx + dz * dz
        t = max(0., min(1., -(a[0] * dx + a[1] * dz) / length_sq)) if length_sq else 0.
        nearest = min(nearest, hypot(a[0] + t * dx, a[1] + t * dz))
    return nearest < radius - TOLERANCE_M


def check_door_clearance(scene: RoomScene, policy: DoorClearancePolicy | None = None) -> DoorClearanceReport:
    """Return deterministic advisory findings without editing/deleting furniture.

    The full door-width access rectangle extends `access_depth_m` into the room.
    An intersecting solid part above `floor_gap_m` and below the door head blocks
    it. Rugs are ignored; snagging, handles, frame thickness, double leaves,
    sliding-door travel, and external corridors are not modeled.
    """
    scene = RoomScene.model_validate(scene)
    policy = DoorClearancePolicy.model_validate(policy or DoorClearancePolicy())
    doors = sorted((o for o in scene.openings if o.type == "door"), key=lambda o: o.id)
    components = {
        obj.id: component_bounds(obj)
        for obj in sorted(scene.objects, key=lambda obj: obj.id)
        if ASSET_REGISTRY[obj.asset_id].object_type != "rug"
    } if doors else {}
    checks: list[DoorCheck] = []
    issues: list[DoorClearanceIssue] = []
    for door in doors:
        if not opening_inside_wall(door, scene.room) or door.height_m <= policy.floor_gap_m:
            checks.append(DoorCheck(door_id=door.id, access_status="unassessed", swing_status="unassessed"))
            issues.append(DoorClearanceIssue(code="door_clearance_unassessed", entity_ids=(door.id,), message="Door clearance was not assessed: resolve the opening bounds or a door height below the configured floor gap."))
            continue
        origin, tangent, inward = _wall_frame(door, scene.room)
        offset = door.offset_m + door.width_m / 2
        zone = Rectangle(
            (origin[0] + tangent[0] * offset + inward[0] * policy.access_depth_m / 2,
             origin[1] + tangent[1] * offset + inward[1] * policy.access_depth_m / 2),
            (tangent, inward), (door.width_m / 2, policy.access_depth_m / 2),
        )
        hinge_offset = door.offset_m + (door.width_m if door.hinge == "max_offset" else 0.)
        hinge = (origin[0] + tangent[0] * hinge_offset, origin[1] + tangent[1] * hinge_offset)
        along: Point2 = (-tangent[0], -tangent[1]) if door.hinge == "max_offset" else tangent
        known_inward = door.swing == "inward" and door.hinge in {"min_offset", "max_offset"}
        access_ids, swing_ids = [], []
        for object_id, parts in components.items():
            vertical_parts = [p.bounds.footprint for p in parts if intervals_overlap(p.bounds.vertical, (policy.floor_gap_m, door.height_m))]
            if any(rectangles_overlap(zone, footprint) for footprint in vertical_parts):
                access_ids.append(object_id)
            if known_inward and any(_sweep_overlaps(footprint, hinge, along, inward, door.width_m) for footprint in vertical_parts):
                swing_ids.append(object_id)
        depth_available = scene.room.length_m if door.wall in {"north", "south"} else scene.room.width_m
        boundary_blocked = depth_available < policy.access_depth_m - TOLERANCE_M
        access_blocked = bool(access_ids) or boundary_blocked
        if access_blocked:
            message = f"Keep the door-width access zone clear for {policy.access_depth_m:g} m inside the room."
            if boundary_blocked:
                message += " The room itself is too shallow for this zone."
            issues.append(DoorClearanceIssue(code="door_access_blocked", entity_ids=(door.id, *access_ids), message=message))
        swing_status: Literal["clear", "blocked", "unknown", "not_applicable", "outside_room"]
        if known_inward:
            swing_boundary_blocked = depth_available < door.width_m - TOLERANCE_M
            swing_status = "blocked" if swing_ids or swing_boundary_blocked else "clear"
            if swing_ids or swing_boundary_blocked:
                message = "Keep this door's estimated 90-degree inward opening sweep clear."
                if swing_boundary_blocked:
                    message += " The sweep extends beyond the room's opposite wall."
                issues.append(DoorClearanceIssue(code="door_swing_blocked", entity_ids=(door.id, *swing_ids), message=message))
        elif door.swing == "none":
            swing_status = "not_applicable"
        elif door.swing == "outward":
            swing_status = "outside_room"
            issues.append(DoorClearanceIssue(code="door_swing_outside_room", entity_ids=(door.id,), message="The outward swing lies outside this room and was not assessed; room-side access was checked."))
        else:
            swing_status = "unknown"
            issues.append(DoorClearanceIssue(code="door_swing_unknown", entity_ids=(door.id,), message="Door swing direction or hinge is unknown. Swing clearance was not assessed; room-side access was checked."))
        checks.append(DoorCheck(door_id=door.id, access_status="blocked" if access_blocked else "clear", swing_status=swing_status, access_object_ids=tuple(access_ids), swing_object_ids=tuple(swing_ids)))
    return DoorClearanceReport(policy=policy, doors=tuple(checks), issues=tuple(issues))
