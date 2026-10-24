"""Small reconstruction-only pose recovery before deferring observed furniture.

No size, identity, rotation, support or room changes. Candidate translations must
clear their entire support group and pass the ordinary geometry, relationship
and clearance checks. This never runs for edits or visual-review patches.
"""

from math import hypot

from roometry.scenes.clearance_acceptance import clearance_regressions
from roometry.scenes.collision import component_bounds, first_component_overlap
from roometry.scenes.geometry import bounds_inside_room, boxes_overlap, object_bounds
from roometry.scenes.validation import validate_geometry
from roometry.vision.fidelity import evaluate_fidelity
from roometry.vision.importance import importance

MAX_SHIFT_M = .5
MAX_CONFLICTS = 8
MAX_CHECKS = 24


def spatial_failures(analysis, scene):
    return {(c.kind, tuple(c.entity_ids), c.message) for c in evaluate_fidelity(analysis, scene).checks
            if not c.passed and c.kind in {'wall', 'facing', 'adjacency', 'support'}}


def recover_local_placements(analysis, scene, report):
    if not report.errors or len(report.errors) > MAX_CONFLICTS:
        return scene, report
    original = scene
    sources = {obj.id: obj for obj in analysis.observations.objects}
    moved, checks, notes = set(), 0, []
    # A successful iteration resolves at least one conflict and a root is moved
    # once, so translations cannot accumulate beyond the original .5 m bound.
    for _ in range(MAX_CONFLICTS):
        affected = {key for error in report.errors if error.code in {'object_overlap', 'object_outside_room'}
                    for key in error.entity_ids}
        roots = [obj for obj in scene.objects if obj.id in affected and obj.id not in moved
                 and obj.placement == 'floor' and obj.type != 'rug' and not obj.fixed and not obj.locked
                 and obj.provenance == 'photo_observed' and obj.id in sources
                 and sources[obj.id].appears_fixed is not True]
        roots.sort(key=lambda obj: (-importance(sources[obj.id]),
                                   {'low': 0, 'medium': 1, 'high': 2}[sources[obj.id].confidence], obj.id))
        before_failures = spatial_failures(analysis, scene)
        previous_errors = {(e.code, e.entity_ids) for e in report.errors}
        accepted = False
        for root in roots[:MAX_CONFLICTS]:
            group = {root.id} | {obj.id for obj in scene.objects if obj.support_object_id == root.id}
            if any(obj.locked or obj.fixed or obj.provenance != 'photo_observed' for obj in scene.objects
                   if obj.id in group and obj.id != root.id):
                continue
            box = object_bounds(root).footprint
            px, pz = box.project((1, 0)), box.project((0, 1))
            hx, hz = (px[1]-px[0])/2, (pz[1]-pz[0])/2
            xs = {root.position.x, min(max(root.position.x, hx), scene.room.width_m-hx)}
            zs = {root.position.z, min(max(root.position.z, hz), scene.room.length_m-hz)}
            others = [obj for obj in scene.objects if obj.id not in group]
            collisions = {key for error in report.errors if set(error.entity_ids) & group
                          for key in error.entity_ids if key not in group}
            for other in others:
                if other.id not in collisions:
                    continue
                area = object_bounds(other).footprint
                x, z = area.project((1, 0)), area.project((0, 1))
                xs.update((x[0]-hx-.005, x[1]+hx+.005))
                zs.update((z[0]-hz-.005, z[1]+hz+.005))
            candidates = sorted(((x, z) for x in xs for z in zs),
                key=lambda point: (hypot(point[0]-root.position.x, point[1]-root.position.z), point))[:64]
            solids = {obj.id: component_bounds(obj) for obj in others if obj.type != 'rug'}
            obstacle_bounds = {obj.id: object_bounds(obj) for obj in others if obj.type != 'rug'}
            for x, z in candidates:
                dx, dz = x-root.position.x, z-root.position.z
                if not .000001 < hypot(dx, dz) <= MAX_SHIFT_M:
                    continue
                candidate = scene.model_copy(deep=True)
                translated = [obj for obj in candidate.objects if obj.id in group]
                for obj in translated:
                    obj.position.x += dx
                    obj.position.z += dz
                moved_bounds = {obj.id: object_bounds(obj) for obj in translated}
                if any(not bounds_inside_room(moved_bounds[obj.id], candidate.room) for obj in translated):
                    continue
                moved_solids = {obj.id: component_bounds(obj) for obj in translated if obj.type != 'rug'}
                if any(boxes_overlap(moved_bounds[key], obstacle_bounds[other])
                       and first_component_overlap(parts, obstacle) is not None
                       for key, parts in moved_solids.items() for other, obstacle in solids.items()):
                    continue
                if checks >= MAX_CHECKS:
                    if scene is not original:
                        scene.assumptions = [*notes, *scene.assumptions][:50]
                    return scene, report
                checks += 1
                revised = validate_geometry(candidate)
                errors = {(e.code, e.entity_ids) for e in revised.errors}
                if (len(errors) >= len(previous_errors) or not errors <= previous_errors
                    or any(set(e.entity_ids) & group for e in revised.errors)
                    or clearance_regressions(report, revised)
                    or not spatial_failures(analysis, candidate) <= before_failures):
                    continue
                scene, report = candidate, revised
                moved.update(group)
                notes.append(f"{root.id}: estimated floor position recovered locally by dx={dx:.3f} m, dz={dz:.3f} m; supported items followed and sizes, orientation and room measurements were preserved.")
                accepted = True
                break
            if accepted:
                break
        if not accepted or not report.errors:
            break
    if scene is not original:
        scene.assumptions = [*notes, *scene.assumptions][:50]
    return scene, report
