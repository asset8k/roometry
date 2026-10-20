"""Resolve approximate attachment poses without resizing or relaxing validation."""

from math import cos, hypot, sin

from roometry.scenes.assets import ASSET_REGISTRY
from roometry.scenes.geometry import (
    TOLERANCE_M,
    WALL_ROTATIONS_RAD,
    ObjectBounds,
    Rectangle,
    boxes_overlap,
    intervals_overlap,
)
from roometry.uploads.schemas import InputDimensions
from roometry.vision.proposal_schemas import DraftObject, SceneDraft
from roometry.vision.schemas import RoomAnalysisResponse

# Corrections apply only to generated estimates, never to scene edits or sizes.
MAX_ATTACHMENT_SHIFT_M = 0.25
MAX_DECOR_SHIFT_M = 0.75
PORTABLE_DECOR = frozenset(
    {"plant", "floor_lamp", "fan", "bin", "basket", "storage_box", "speaker"}
)


def draft_bounds(obj: DraftObject) -> ObjectBounds:
    c, s = cos(obj.rotation_y_rad), sin(obj.rotation_y_rad)
    return ObjectBounds(
        Rectangle(
            (obj.position.x, obj.position.z),
            ((c, -s), (s, c)),
            (obj.dimensions_m.width / 2, obj.dimensions_m.depth / 2),
        ),
        (obj.position.y, obj.position.y + obj.dimensions_m.height),
    )


def anchor_placements(
    analysis: RoomAnalysisResponse, room: InputDimensions, draft: SceneDraft
) -> list[DraftObject]:
    objects = [obj.model_copy(deep=True) for obj in draft.objects]
    width, length, height = room.width_m, room.length_m, room.height_m
    if width is None or length is None or height is None:
        return objects
    original = {obj.id: obj for obj in draft.objects}
    observations = {obj.id: obj for obj in analysis.observations.objects}
    for obj in objects:
        if obj.placement == "floor":
            obj.position.y = 0.0
            source = observations.get(obj.id)
            asset = ASSET_REGISTRY.get(f"{source.type}_standard_01") if source else None
            if (
                source is not None
                and asset is not None
                and not asset.fixed_by_default
                and source.appears_fixed is not True
            ):
                # Fit small boundary overshoots before transforming children.
                # Do not rescue grossly misplaced furniture or installed fixtures.
                box = draft_bounds(obj).footprint
                x_span, z_span = box.project((1, 0)), box.project((0, 1))
                half_x, half_z = (
                    (x_span[1] - x_span[0]) / 2,
                    (z_span[1] - z_span[0]) / 2,
                )
                if 2 * half_x <= width and 2 * half_z <= length:
                    x = min(max(obj.position.x, half_x), width - half_x)
                    z = min(max(obj.position.z, half_z), length - half_z)
                    if (
                        hypot(x - obj.position.x, z - obj.position.z)
                        <= MAX_ATTACHMENT_SHIFT_M
                    ):
                        obj.position.x, obj.position.z = x, z
        elif obj.placement == "ceiling":
            obj.position.y = height - obj.dimensions_m.height
        elif obj.placement == "wall" and obj.wall is not None:
            obj.rotation_y_rad = WALL_ROTATIONS_RAD[obj.wall]
            depth = obj.dimensions_m.depth / 2
            if obj.wall == "north":
                obj.position.z = depth
            elif obj.wall == "south":
                obj.position.z = length - depth
            elif obj.wall == "west":
                obj.position.x = depth
            else:
                obj.position.x = width - depth
            source = observations.get(obj.id)
            if source is not None and source.type == "window_covering":
                # Curtains are decorative attachments; fit their estimated pose,
                # never their dimensions, to the host wall and room height.
                along = "x" if obj.wall in {"north", "south"} else "z"
                span = width if along == "x" else length
                half = obj.dimensions_m.width / 2
                if 2 * half <= span and obj.dimensions_m.height <= height:
                    previous = getattr(obj.position, along)
                    fitted = min(max(previous, half), span - half)
                    fitted_y = min(obj.position.y, height - obj.dimensions_m.height)
                    if (
                        hypot(fitted - previous, fitted_y - obj.position.y)
                        <= MAX_ATTACHMENT_SHIFT_M
                    ):
                        setattr(obj.position, along, fitted)
                        obj.position.y = fitted_y

    by_id = {obj.id: obj for obj in objects}
    eligible: list[tuple[DraftObject, DraftObject]] = []
    for obj in objects:
        if obj.placement != "surface" or obj.support_object_id is None:
            continue
        support = by_id.get(obj.support_object_id)
        source = observations.get(obj.support_object_id)
        asset = ASSET_REGISTRY.get(f"{source.type}_standard_01") if source else None
        if (
            support is None
            or support.placement not in {"floor", "wall"}
            or asset is None
            or not asset.supplies_surface
        ):
            continue
        before = original[support.id]
        # Preserve the child's local pose when its host is snapped to a wall.
        delta = support.rotation_y_rad - before.rotation_y_rad
        c, s = cos(delta), sin(delta)
        dx, dz = obj.position.x - before.position.x, obj.position.z - before.position.z
        obj.position.x = support.position.x + c * dx + s * dz
        obj.position.z = support.position.z - s * dx + c * dz
        obj.rotation_y_rad += delta
        obj.position.y = support.position.y + support.dimensions_m.height
        relative = obj.rotation_y_rad - support.rotation_y_rad
        c, s = abs(cos(relative)), abs(sin(relative))
        rotated_width = c * obj.dimensions_m.width + s * obj.dimensions_m.depth
        rotated_depth = s * obj.dimensions_m.width + c * obj.dimensions_m.depth
        if (
            (
                rotated_width > support.dimensions_m.width + TOLERANCE_M
                or rotated_depth > support.dimensions_m.depth + TOLERANCE_M
            )
            and obj.dimensions_m.width <= support.dimensions_m.width
            and obj.dimensions_m.depth <= support.dimensions_m.depth
        ):
            # A wide TV/frame needs the support's orientation, not a smaller size.
            obj.rotation_y_rad = support.rotation_y_rad
        eligible.append((obj, support))

    def inside(obj: DraftObject) -> bool:
        box = draft_bounds(obj)
        return (
            all(
                -TOLERANCE_M <= x <= width + TOLERANCE_M
                and -TOLERANCE_M <= z <= length + TOLERANCE_M
                for x, z in box.footprint.corners
            )
            and box.vertical[0] >= 0
            and box.vertical[1] <= height + TOLERANCE_M
        )

    bounds = {obj.id: draft_bounds(obj) for obj in objects}
    for obj, support in sorted(eligible, key=lambda pair: pair[0].id):
        outer = bounds[support.id].footprint
        child = bounds[obj.id].footprint
        u, v = outer.axes
        dx, dz = obj.position.x - outer.center[0], obj.position.z - outer.center[1]
        local_x, local_z = dx * u[0] + dz * u[1], dx * v[0] + dz * v[1]
        x_span, z_span = child.project(u), child.project(v)
        child_x, child_z = (x_span[1] - x_span[0]) / 2, (z_span[1] - z_span[0]) / 2
        limit_x, limit_z = (
            outer.half_extents[0] - child_x,
            outer.half_extents[1] - child_z,
        )
        if limit_x < 0 or limit_z < 0:
            continue  # An oversized object needs a new estimate, not a smaller box.
        xs = {min(max(local_x, -limit_x), limit_x)}
        zs = {min(max(local_z, -limit_z), limit_z)}
        # Try adjacent positions around siblings and nearby solid obstacles.
        for other in objects:
            if other.id in {obj.id, support.id} or (
                observations.get(other.id) is not None
                and observations[other.id].type == "rug"
            ):
                continue
            if not intervals_overlap(
                bounds[obj.id].vertical, bounds[other.id].vertical
            ):
                continue
            box = bounds[other.id].footprint
            along_x, along_z = box.project(u), box.project(v)
            center_x = outer.center[0] * u[0] + outer.center[1] * u[1]
            center_z = outer.center[0] * v[0] + outer.center[1] * v[1]
            xs.update(
                (along_x[0] - center_x - child_x, along_x[1] - center_x + child_x)
            )
            zs.update(
                (along_z[0] - center_z - child_z, along_z[1] - center_z + child_z)
            )
        previous_x, previous_z = obj.position.x, obj.position.z
        # Bound work even for the maximum 200 objects sharing one surface.
        nearby_x = sorted(xs, key=lambda x: (abs(x - local_x), x))[:8]
        nearby_z = sorted(zs, key=lambda z: (abs(z - local_z), z))[:8]
        candidates = sorted(
            ((x, z) for x in nearby_x for z in nearby_z),
            key=lambda point: (hypot(point[0] - local_x, point[1] - local_z), point),
        )[:64]
        for x, z in candidates:
            if (
                abs(x) > limit_x
                or abs(z) > limit_z
                or hypot(x - local_x, z - local_z) > MAX_ATTACHMENT_SHIFT_M
            ):
                continue
            obj.position.x = outer.center[0] + x * u[0] + z * v[0]
            obj.position.z = outer.center[1] + x * u[1] + z * v[1]
            if inside(obj) and not any(
                boxes_overlap(draft_bounds(obj), bounds[other.id])
                for other in objects
                if other.id not in {obj.id, support.id}
                and (
                    observations.get(other.id) is None
                    or observations[other.id].type != "rug"
                )
            ):
                break
        else:
            obj.position.x, obj.position.z = previous_x, previous_z
        bounds[obj.id] = draft_bounds(obj)

    # Small portable decor is especially uncertain in a single photo. Resolve
    # nearby conflicts while keeping the main furniture and every object size.
    for obj in sorted(objects, key=lambda item: item.id):
        source = observations.get(obj.id)
        if (
            obj.placement != "floor"
            or source is None
            or source.type not in PORTABLE_DECOR
            or source.appears_fixed is True
        ):
            continue
        obstacles = [
            other
            for other in objects
            if other.id != obj.id
            and (
                observations.get(other.id) is None
                or observations[other.id].type != "rug"
            )
        ]
        if inside(obj) and not any(
            boxes_overlap(bounds[obj.id], bounds[other.id]) for other in obstacles
        ):
            continue
        previous_x, previous_z = obj.position.x, obj.position.z
        x_span, z_span = (
            bounds[obj.id].footprint.project((1, 0)),
            bounds[obj.id].footprint.project((0, 1)),
        )
        half_x, half_z = (x_span[1] - x_span[0]) / 2, (z_span[1] - z_span[0]) / 2
        xs, zs = {previous_x}, {previous_z}
        for other in obstacles:
            if not intervals_overlap(
                bounds[obj.id].vertical, bounds[other.id].vertical
            ):
                continue
            x_span, z_span = (
                bounds[other.id].footprint.project((1, 0)),
                bounds[other.id].footprint.project((0, 1)),
            )
            xs.update((x_span[0] - half_x, x_span[1] + half_x))
            zs.update((z_span[0] - half_z, z_span[1] + half_z))
        nearby_x = sorted(xs, key=lambda x: (abs(x - previous_x), x))[:8]
        nearby_z = sorted(zs, key=lambda z: (abs(z - previous_z), z))[:8]
        candidates = sorted(
            ((x, z) for x in nearby_x for z in nearby_z),
            key=lambda point: (
                hypot(point[0] - previous_x, point[1] - previous_z),
                point,
            ),
        )[:64]
        for x, z in candidates:
            if hypot(x - previous_x, z - previous_z) > MAX_DECOR_SHIFT_M:
                continue
            obj.position.x, obj.position.z = x, z
            box = draft_bounds(obj)
            if inside(obj) and not any(
                boxes_overlap(box, bounds[other.id]) for other in obstacles
            ):
                break
        else:
            obj.position.x, obj.position.z = previous_x, previous_z
        bounds[obj.id] = draft_bounds(obj)
    return objects
