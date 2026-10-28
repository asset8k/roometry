"""Bounded local redesign search; every offered patch passes the real policy.

Coarse occupancy ranks options only. Component collision, supports, entrances,
walking routes, utility, trade-offs and measured gain decide eligibility. No
photos, measurements, fixed objects, locks or scoring thresholds are changed.
"""

from collections import deque
from dataclasses import dataclass
from itertools import combinations, zip_longest
from math import cos, sin, pi, ceil
from time import monotonic

from roometry.assessment.evidence import evidence_for_run
from roometry.optimization.inventory import apply_inventory, catalog_object
from roometry.optimization.policy import evaluate, meaningful_gain
from roometry.optimization.schemas import CandidateProposal, CatalogAddition
from roometry.scenes.assets import ASSET_REGISTRY
from roometry.scenes.door_clearance import check_door_clearance
from roometry.scenes.walking_space import check_walking_space, WalkingPolicy
from roometry.scenes.catalog import ASSET_CATALOG, object_surfaces
from roometry.scenes.collision import component_bounds, first_component_overlap
from roometry.scenes.geometry import (
    bounds_inside_room,
    boxes_overlap,
    object_bounds,
    intervals_overlap,
    Rectangle,
    ObjectBounds,
    rectangle_contains,
    wall_object_overlaps_opening,
)
from roometry.scenes.mutations import apply_edit, EditRejected
from roometry.scenes.edit_schemas import MoveObject, RotateObject
from roometry.scenes.schemas import RoomScene
from roometry.scenes.scoring import STORAGE_TYPES
from roometry.scenes.navigation import point_rectangle_distance


@dataclass(frozen=True)
class PlacementSearch:
    proposals: tuple[CandidateProposal, ...] = ()
    checked: int = 0
    exhausted: bool = False


def patch_between(base: RoomScene, target: RoomScene, focus: str) -> CandidateProposal:
    """Describe complete support-group transforms once, retaining original IDs."""
    old = {o.id: o for o in base.objects}
    changes, replacements, additions = [], [], []
    for obj in target.objects:
        previous = old.get(obj.id)
        pose = {"x": obj.position.x, "z": obj.position.z}
        if previous is None:
            additions.append(
                dict(
                    object_id=obj.id,
                    asset_id=obj.asset_id,
                    position=pose,
                    rotation_y_rad=obj.rotation_y_rad,
                    color=obj.color,
                    placement=obj.placement,
                    wall=obj.wall,
                    elevation_m=obj.position.y if obj.placement == "wall" else None,
                    support_object_id=obj.support_object_id,
                    support_surface_id=obj.support_surface_id,
                )
            )
        elif previous.asset_id != obj.asset_id:
            replacements.append(
                dict(
                    object_id=obj.id,
                    asset_id=obj.asset_id,
                    position=pose,
                    rotation_y_rad=obj.rotation_y_rad,
                    color=obj.color,
                )
            )
        elif obj.placement == "floor" and (
            previous.position != obj.position
            or previous.rotation_y_rad != obj.rotation_y_rad
        ):
            changes.append(
                dict(
                    object_id=obj.id,
                    position=pose,
                    rotation_y_rad=obj.rotation_y_rad,
                    color=None,
                    material_colors=None,
                )
            )
    names = [
        o.id.replace("_", " ")
        for o in target.objects
        if o.id not in old
        or o.asset_id != old[o.id].asset_id
        or (
            o.placement == "floor"
            and (
                o.position != old[o.id].position
                or o.rotation_y_rad != old[o.id].rotation_y_rad
            )
        )
    ]
    return CandidateProposal.model_validate(
        dict(
            changes=changes,
            replacements=replacements,
            additions=additions,
            removals=[],
            summary=(
                f"{'Improve storage access and capacity' if focus == 'storage' else 'Open connected walking space'} by adjusting "
                + ", ".join(names)
                + "."
            )[:500],
            suggestions=[],
            finish=True,
        )
    )


def movable(scene, obj):
    return (
        obj.placement == "floor"
        and obj.type != "rug"
        and not obj.fixed
        and not obj.locked
        and not any(
            (o.fixed or o.locked) and o.support_object_id == obj.id
            for o in scene.objects
        )
    )


def perimeter_storage(scene, obj):
    """New ordinary storage belongs against a wall, facing into the room.

    A freestanding divider is a different design choice. Do not game capacity
    by inserting an ordinary tall bookcase into the middle of a usable room.
    Allow a small stand-off for skirting and observed wall panels.
    """
    s, c = sin(obj.rotation_y_rad), cos(obj.rotation_y_rad)
    back_x = obj.position.x - s * obj.dimensions_m.depth / 2
    back_z = obj.position.z - c * obj.dimensions_m.depth / 2
    wall = next(
        (
            name
            for name, close in (
                ("north", c > 0.99 and abs(back_z) <= 0.2),
                ("south", c < -0.99 and abs(back_z - scene.room.length_m) <= 0.2),
                ("west", s > 0.99 and abs(back_x) <= 0.2),
                ("east", s < -0.99 and abs(back_x - scene.room.width_m) <= 0.2),
            )
            if close
        ),
        None,
    )
    if wall is None:
        return False
    # A floor cabinet may fit geometrically but still cover the observed window.
    bounds = object_bounds(obj)
    return not any(
        o.wall == wall and wall_object_overlaps_opening(bounds, o)
        for o in scene.openings
    )


def spatially_valid(scene):
    """Cheap component filter, never an acceptance check."""
    boxes = {o.id: object_bounds(o) for o in scene.objects}
    if any(not bounds_inside_room(box, scene.room) for box in boxes.values()):
        return False
    parts = {}
    for a, b in combinations(scene.objects, 2):
        if a.type == "rug" or b.type == "rug":
            continue
        if boxes_overlap(boxes[a.id], boxes[b.id]):
            if a.id not in parts:
                parts[a.id] = component_bounds(a)
            if b.id not in parts:
                parts[b.id] = component_bounds(b)
            left, right = parts[a.id], parts[b.id]
            if first_component_overlap(left, right):
                return False
    return True


class Ranking:
    def __init__(self, scene, focus, excluded):
        self.focus, self.excluded = focus, excluded
        self.room = Rectangle(
            (scene.room.width_m / 2, scene.room.length_m / 2),
            ((1.0, 0.0), (0.0, 1.0)),
            (scene.room.width_m / 2, scene.room.length_m / 2),
        )
        self.nx, self.nz = (
            ceil(scene.room.width_m / 0.25),
            ceil(scene.room.length_m / 0.25),
        )
        self.dx, self.dz = scene.room.width_m / self.nx, scene.room.length_m / self.nz
        self.points = [
            (i, j, ((i + 0.5) * self.dx, (j + 0.5) * self.dz))
            for j in range(self.nz)
            for i in range(self.nx)
            if 0.4 <= (i + 0.5) * self.dx <= scene.room.width_m - 0.4
            and 0.4 <= (j + 0.5) * self.dz <= scene.room.length_m - 0.4
        ]
        self.cells, self.solids = {}, {}
        self.storage = (
            Ranking(scene, "storage", excluded) if focus == "openness" else None
        )
        self.storage_floor = self.storage(scene) - 1.5 if self.storage else 0.0
        self.doors = check_door_clearance(scene)

    def components(self, obj):
        key = (
            obj.asset_id,
            obj.placement,
            tuple(obj.dimensions_m.model_dump().values()),
            tuple(obj.position.model_dump().values()),
            obj.rotation_y_rad,
        )
        if key not in self.solids:
            self.solids[key] = tuple(
                p.bounds
                for p in component_bounds(obj)
                if intervals_overlap(p.bounds.vertical, (0.02, 1.8))
            )
        return key, self.solids[key]

    def __call__(self, scene):
        solids = {o.id: self.components(o) for o in scene.objects if o.type != "rug"}
        if self.focus == "storage":
            containers = [
                o
                for o in scene.objects
                if o.type in STORAGE_TYPES
                and o.placement in {"floor", "wall"}
                and o.id not in self.excluded
            ]
            if not containers:
                return 0.0
            accessible = 0
            for obj in containers:
                c, s = cos(obj.rotation_y_rad), sin(obj.rotation_y_rad)
                offset = obj.dimensions_m.depth / 2 + 0.3
                zone = Rectangle(
                    (obj.position.x + s * offset, obj.position.z + c * offset),
                    ((c, -s), (s, c)),
                    (min(0.6, obj.dimensions_m.width) / 2, 0.3),
                )
                box = ObjectBounds(zone, (0.02, 1.8))
                if rectangle_contains(self.room, zone) and not any(
                    boxes_overlap(box, p)
                    for key, (_, parts) in solids.items()
                    if key != obj.id
                    for p in parts
                ):
                    accessible += 1
            volume = sum(
                o.dimensions_m.width * o.dimensions_m.height * o.dimensions_m.depth
                for o in containers
            )
            return 7 * accessible / len(containers) + 3 * volume / (
                volume + 0.075 * scene.room.width_m * scene.room.length_m
            )
        if self.storage(scene) < self.storage_floor - 0.05:
            return -1.0
        before_doors = {d.door_id: d for d in self.doors.doors}
        if any(
            d.access_status == "blocked"
            and before_doors[d.door_id].access_status == "clear"
            for d in check_door_clearance(scene).doors
        ):
            return -1.0
        blocked = set()
        for key, parts in solids.values():
            if key not in self.cells:
                self.cells[key] = {
                    j * self.nx + i
                    for i, j, p in self.points
                    if any(
                        point_rectangle_distance(p, part.footprint) < 0.4
                        for part in parts
                    )
                }
            blocked.update(self.cells[key])
        free = {j * self.nx + i for i, j, _ in self.points} - blocked
        count = len(free)
        main = 0
        while free:
            queue = deque([free.pop()])
            size = 0
            while queue:
                n = queue.popleft()
                size += 1
                for m in (n - 1, n + 1, n - self.nx, n + self.nx):
                    if m in free and (
                        m // self.nx == n // self.nx or abs(m - n) == self.nx
                    ):
                        free.remove(m)
                        queue.append(m)
            main = max(main, size)
        value = 0.65 * min(
            1.0,
            main * self.dx * self.dz / (0.4 * scene.room.width_m * scene.room.length_m),
        ) + 0.35 * (main / count if count else 0)
        # Largest empty region alone rewards space that the entrance cannot
        # reach. Use continuous route edges even in the coarse ranking pass.
        routes = check_walking_space(scene, WalkingPolicy(cell_size_m=0.25))
        if routes.status == "blocked":
            value = min(value, 0.3)
        elif routes.status == "narrow":
            value = min(value, 0.6)
        return value


def poses(scene, obj):
    angles = [obj.rotation_y_rad]
    if obj.type in STORAGE_TYPES | {"bed", "sofa", "desk", "table"}:
        angles += [0.0, pi / 2, pi, 3 * pi / 2]
    elif (
        obj.type in {"coffee_table", "armchair"}
        and abs(obj.dimensions_m.width - obj.dimensions_m.depth) > 0.2
    ):
        angles += [obj.rotation_y_rad + pi / 2]
    batches = []
    for angle in dict.fromkeys(angles):
        c, s = abs(cos(angle)), abs(sin(angle))
        hx = (c * obj.dimensions_m.width + s * obj.dimensions_m.depth) / 2 + 0.015
        hz = (s * obj.dimensions_m.width + c * obj.dimensions_m.depth) / 2 + 0.015
        xs = {
            round(obj.position.x, 4),
            hx,
            scene.room.width_m - hx,
            scene.room.width_m / 2,
        }
        zs = {
            round(obj.position.z, 4),
            hz,
            scene.room.length_m - hz,
            scene.room.length_m / 2,
        }
        for delta in (-0.4, -0.2, 0.2, 0.4):
            xs.add(obj.position.x + delta)
            zs.add(obj.position.z + delta)
        # Perimeter positions include obstacle edges, not a room-specific template.
        for other in scene.objects:
            if other.id == obj.id or other.type == "rug":
                continue
            box = object_bounds(other).footprint
            x, z = box.project((1, 0)), box.project((0, 1))
            xs.update((x[0] - hx - 0.02, x[1] + hx + 0.02))
            zs.update((z[0] - hz - 0.02, z[1] + hz + 0.02))
        candidates = {
            (x, z)
            for x in xs
            for z in zs
            if hx <= x <= scene.room.width_m - hx
            and hz <= z <= scene.room.length_m - hz
            and (
                abs(x - obj.position.x) < 0.01
                or abs(z - obj.position.z) < 0.01
                or x in {hx, scene.room.width_m - hx}
                or z in {hz, scene.room.length_m - hz}
            )
        }
        nearest = sorted(
            candidates,
            key=lambda p: (
                (p[0] - obj.position.x) ** 2 + (p[1] - obj.position.z) ** 2,
                p,
            ),
        )
        perimeter = sorted(
            p
            for p in candidates
            if p[0] in {hx, scene.room.width_m - hx}
            or p[1] in {hz, scene.room.length_m - hz}
        )
        # Include distant wall gaps early; local-only nudges cannot open a room
        # whose table/shelving needs to move to the opposite side.
        spread = perimeter[:: max(1, len(perimeter) // 16)]
        ordered = list(
            dict.fromkeys(
                p
                for pair in zip_longest(nearest[:50], spread)
                for p in pair
                if p is not None
            )
        )
        batches.append(
            [
                (x, z, angle)
                for x, z in ordered
                if abs(x - obj.position.x) + abs(z - obj.position.z) > 1e-4
                or abs(angle - obj.rotation_y_rad) > 1e-4
            ]
        )
    for group in zip_longest(*batches):
        yield from (pose for pose in group if pose is not None)


def move_group(scene, obj, pose):
    x, z, angle = pose
    result = scene
    chairs = []
    if obj.type == "desk":
        chairs = [
            o
            for o in scene.objects
            if o.type == "chair"
            and movable(scene, o)
            and (o.position.x - obj.position.x) ** 2
            + (o.position.z - obj.position.z) ** 2
            < 1.5**2
        ]
        chairs = sorted(
            chairs,
            key=lambda o: (
                (o.position.x - obj.position.x) ** 2
                + (o.position.z - obj.position.z) ** 2
            ),
        )[:1]
    result = apply_edit(
        result,
        MoveObject(operation="move", object_id=obj.id, position={"x": x, "z": z}),
    )
    result = apply_edit(
        result, RotateObject(operation="rotate", object_id=obj.id, rotation_y_rad=angle)
    )
    for chair in chairs:
        dx, dz = chair.position.x - obj.position.x, chair.position.z - obj.position.z
        a = angle - obj.rotation_y_rad
        result = apply_edit(
            result,
            MoveObject(
                operation="move",
                object_id=chair.id,
                position={
                    "x": x + cos(a) * dx + sin(a) * dz,
                    "z": z - sin(a) * dx + cos(a) * dz,
                },
            ),
        )
        result = apply_edit(
            result,
            RotateObject(
                operation="rotate",
                object_id=chair.id,
                rotation_y_rad=chair.rotation_y_rad + a,
            ),
        )
    return result


def compact_variants(scene, obj):
    """Real catalogue replacements, never shrink the observed measurements."""
    if obj.type not in {"sofa", "bed", "table", "coffee_table", "desk", "armchair"}:
        return []
    area = obj.dimensions_m.width * obj.dimensions_m.depth
    options = [
        a
        for a in ASSET_CATALOG.values()
        if a.object_type == obj.type
        and a.asset_id != obj.asset_id
        and "floor" in ASSET_REGISTRY[a.asset_id].placements
        and not ASSET_REGISTRY[a.asset_id].fixed_by_default
        and 0.75 * area <= a.dimensions_m[0] * a.dimensions_m[2] < area - 0.15
    ]
    options.sort(key=lambda a: (a.dimensions_m[0] * a.dimensions_m[2], a.asset_id))
    result = []
    for asset in options[:2]:
        proposal = CandidateProposal(
            changes=[],
            replacements=[{"object_id": obj.id, "asset_id": asset.asset_id}],
            summary="Use a compact model of the same furniture function.",
            suggestions=[],
        )
        try:
            result.append(apply_inventory(scene, proposal))
        except ValueError:
            continue
    return result


def plan_placements(
    run, source, *, max_seconds=12.0, max_checked=1400, max_evaluated=12
):
    """Offer at most three independently valid complete patches, with no paid calls.

    Search is capped by time and candidate count. Only a few finalists receive
    the expensive, authoritative checks; coarse scores never authorize an edit.
    Chat instructions are deliberately excluded from this goal-specific search.
    """
    base = run.result_scene or run.source_scene
    if (
        run.instruction
        or run.focus not in {"openness", "storage"}
        or len(base.objects) > 60
        or base.room.width_m * base.room.length_m > 150
    ):
        return PlacementSearch()
    deadline = monotonic() + max_seconds
    rank = Ranking(base, run.focus, evidence_for_run(source).excluded_storage_ids)
    checked = 0
    seen, best, beam = set(), [], [base]
    for depth in range(2):
        choices = []

        def consider(candidate):
            nonlocal checked
            checked += 1
            if not spatially_valid(candidate):
                return
            key = candidate.model_dump_json()
            if key in seen:
                return
            seen.add(key)
            score = rank(candidate)
            # Prefer a small coherent intervention when measured gains tie.
            changes = sum(a != b for a, b in zip(base.objects, candidate.objects))
            choices.append((score, -changes, key, candidate))
            choices.sort(key=lambda x: (-x[0], -x[1], x[2]))
            grouped, kept = {}, []
            for entry in choices:
                group = tuple(
                    (o.id, o.asset_id)
                    for a, o in zip(base.objects, entry[3].objects)
                    if a != o
                )
                group += tuple(
                    (o.id, o.asset_id) for o in entry[3].objects[len(base.objects) :]
                )
                if grouped.get(group, 0) < 3:
                    grouped[group] = grouped.get(group, 0) + 1
                    kept.append(entry)
            choices[:] = kept[:24]

        streams = []
        for scene in beam:
            roots = [o for o in scene.objects if movable(scene, o)]
            roots.sort(
                key=lambda o: (
                    o.type not in STORAGE_TYPES
                    if run.focus == "storage"
                    else o.type not in {"bed", "sofa", "table", "desk", "coffee_table"},
                    -o.dimensions_m.width * o.dimensions_m.depth,
                    o.id,
                )
            )
            # Round-robin prevents a large object's rotations from consuming the
            # entire allowance before smaller pieces or additions are explored.
            for obj in roots[:12]:

                def moves(obj=obj, scene=scene):
                    variants = (
                        compact_variants(scene, obj) if run.focus == "openness" else []
                    )
                    for variant in variants:
                        yield variant
                    variants = [scene, *variants]
                    batches = [
                        poses(
                            variant, next(o for o in variant.objects if o.id == obj.id)
                        )
                        for variant in variants
                    ]
                    for batch in zip_longest(*batches):
                        for variant, pose in zip(variants, batch):
                            if pose is None:
                                continue
                            replacement = next(
                                o for o in variant.objects if o.id == obj.id
                            )
                            try:
                                yield move_group(variant, replacement, pose)
                            except (ValueError, EditRejected):
                                continue

                streams.append(iter(moves()))
            if run.focus == "storage":
                assets = [
                    a
                    for a in ASSET_CATALOG.values()
                    if a.object_type in {"shelf", "dresser", "sideboard"}
                    # A coat rack is catalogued under shelves for discovery,
                    # but is not a cabinet-capacity addition for this search.
                    and (
                        a.object_type != "shelf" or object_surfaces(a.asset_id, "floor")
                    )
                    and "floor" in ASSET_REGISTRY[a.asset_id].placements
                    and not ASSET_REGISTRY[a.asset_id].fixed_by_default
                ]
                assets.sort(
                    key=lambda a: (a.dimensions_m[0] * a.dimensions_m[2], a.asset_id)
                )
                for asset in assets[:4]:

                    def additions(asset=asset, scene=scene):
                        key, index = "storage_addition", 1
                        ids = {o.id for o in scene.objects} | {
                            o.id for o in scene.openings
                        }
                        while key in ids:
                            key = f"storage_addition_{index}"
                            index += 1
                        obj = catalog_object(
                            CatalogAddition(
                                object_id=key,
                                asset_id=asset.asset_id,
                                position={
                                    "x": scene.room.width_m / 2,
                                    "z": scene.room.length_m / 2,
                                },
                                rotation_y_rad=0.0,
                            ),
                            scene.room,
                            {},
                        )
                        for x, z, angle in poses(scene, obj):
                            item = obj.model_copy(deep=True)
                            item.position.x, item.position.z, item.rotation_y_rad = (
                                x,
                                z,
                                angle,
                            )
                            if not perimeter_storage(scene, item):
                                continue
                            yield scene.model_copy(
                                update={"objects": [*scene.objects, item]}
                            )

                    streams.append(iter(additions()))
        # Reserve half the allowance for coordinated two-object arrangements.
        allowance = max_checked // 2 if depth == 0 else max_checked
        while streams and checked < allowance and monotonic() < deadline:
            active = []
            for stream in streams:
                if checked >= allowance or monotonic() >= deadline:
                    break
                candidate = next(stream, None)
                if candidate is not None:
                    consider(candidate)
                    active.append(stream)
            streams = active
        best.extend(choices)
        best.sort(key=lambda x: (-x[0], -x[1], x[2]))
        best = best[:32]
        beam = []
        groups = set()
        for item in choices:
            group = tuple(o.id for a, o in zip(base.objects, item[3].objects) if a != o)
            group += tuple(o.id for o in item[3].objects[len(base.objects) :])
            if group not in groups:
                groups.add(group)
                beam.append(item[3])
            if len(beam) == 4:
                break
        if monotonic() >= deadline or checked >= max_checked:
            break
    accepted = []
    signatures = set()
    for _, _, _, scene in best[:max_evaluated]:
        if monotonic() >= deadline + max_seconds:
            break
        try:
            proposal = patch_between(base, scene, run.focus)
            evaluation = evaluate(run, proposal, source)
            if (
                evaluation.issues
                or not evaluation.changed
                or not meaningful_gain(run, evaluation, source)
            ):
                continue
            # Do not offer three millimetre variants of the same intervention.
            signature = tuple(
                (
                    o.id,
                    o.asset_id,
                    round(o.position.x),
                    round(o.position.z),
                    round(o.rotation_y_rad, 1),
                )
                for o in evaluation.scene.objects
            )
            if signature in signatures:
                continue
            signatures.add(signature)
            accepted.append((evaluation.after_score, proposal))
            if len(accepted) >= 3:
                break
        except (ValueError, EditRejected):
            continue
    accepted.sort(key=lambda p: -p[0])
    return PlacementSearch(
        tuple(p for _, p in accepted[:3]),
        checked,
        monotonic() >= deadline or checked >= max_checked,
    )
