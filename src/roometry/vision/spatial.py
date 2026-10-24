"""Planar proximity for relationships, independent of solid collision checks."""

from math import acos, cos, degrees, hypot, sin

from roometry.scenes.schemas import RoomScene, Wall


def planar_bounds(entity_id: str, scene: RoomScene, wall_map: dict[str, Wall]):
    for obj in scene.objects:
        if obj.id == entity_id:
            c, s = abs(cos(obj.rotation_y_rad)), abs(sin(obj.rotation_y_rad))
            hx = (c * obj.dimensions_m.width + s * obj.dimensions_m.depth) / 2
            hz = (s * obj.dimensions_m.width + c * obj.dimensions_m.depth) / 2
            return obj.position.x - hx, obj.position.x + hx, obj.position.z - hz, obj.position.z + hz
    for opening in scene.openings:
        if opening.id == entity_id:
            start, end = opening.offset_m, opening.offset_m + opening.width_m
            if opening.wall in {"north", "south"}:
                z = 0. if opening.wall == "north" else scene.room.length_m
                return start, end, z, z
            x = 0. if opening.wall == "west" else scene.room.width_m
            return x, x, start, end
    wall = wall_map.get(entity_id)
    if wall in {"north", "south"}:
        z = 0. if wall == "north" else scene.room.length_m
        return 0., scene.room.width_m, z, z
    if wall in {"west", "east"}:
        x = 0. if wall == "west" else scene.room.width_m
        return x, x, 0., scene.room.length_m
    return None


def planar_gap(left: str, right: str, scene: RoomScene, wall_map: dict[str, Wall]) -> float:
    a, b = planar_bounds(left, scene, wall_map), planar_bounds(right, scene, wall_map)
    if a is None or b is None:
        return float("inf")
    return hypot(max(0., a[0] - b[1], b[0] - a[1]), max(0., a[2] - b[3], b[2] - a[3]))


POSITION_TOLERANCE_M = .5
FACING_TOLERANCE_DEG = 15.


def relationship_limit(scene, relation, *, corrective=False):
    """Broad reconstruction bands, with a dead band before requesting edits.

    A half-meter discrepancy alone is not a correction objective. Physical
    collision, attachment and clearance checks remain independent and unchanged.
    """
    base = 45. if relation == "faces" else .4 if relation == "against_wall" else max(.8, .15 * min(scene.room.width_m, scene.room.length_m))
    return base if corrective else base + (FACING_TOLERANCE_DEG if relation == "faces" else POSITION_TOLERANCE_M)


def relationship_error(scene, subject_id, target_id, relation):
    walls = {item.observation_id: item.wall for item in scene.wall_mapping}
    if relation != "faces":
        return planar_gap(subject_id, target_id, scene, walls)
    subject = next((obj for obj in scene.objects if obj.id == subject_id), None)
    bounds = planar_bounds(target_id, scene, walls)
    if subject is None or bounds is None:
        return float("inf")
    dx, dz = (bounds[0] + bounds[1]) / 2 - subject.position.x, (bounds[2] + bounds[3]) / 2 - subject.position.z
    distance = hypot(dx, dz)
    if distance <= .05:
        return float("inf")
    dot = (sin(subject.rotation_y_rad) * dx + cos(subject.rotation_y_rad) * dz) / distance
    return degrees(acos(max(-1., min(1., dot))))
