"""Planar proximity for relationships, independent of solid collision checks."""

from math import cos, hypot, sin

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
