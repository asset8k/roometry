"""Small reconstruction-only pose recovery before deferring observed furniture.

No size, identity, rotation, support or room changes. Estimated installed-fixture
poses may be corrected during reconstruction; manual transforms remain protected.
Candidate translations must
clear their entire support group and pass the ordinary geometry, relationship
and clearance checks. Review repairs must supply their authorized identity scope;
this never runs for manual edits.
"""

from math import hypot

from roometry.scenes.clearance_acceptance import clearance_regressions
from roometry.scenes.collision import component_bounds, first_component_overlap
from roometry.scenes.geometry import bounds_inside_room, boxes_overlap, object_bounds
from roometry.scenes.validation import validate_geometry
from roometry.vision.fidelity import evaluate_fidelity
from roometry.vision.importance import importance
from roometry.vision.functional_groups import reconstruction_group

MAX_SHIFT_M = .5
MAX_CONFLICTS = 8
MAX_CHECKS = 24


def spatial_failures(analysis, scene):
    return {(c.kind, tuple(c.entity_ids), c.message) for c in evaluate_fidelity(analysis, scene).checks
            if not c.passed and c.kind in {'wall', 'facing', 'adjacency', 'support'}}


def recover_local_placements(analysis, scene, report, *, allowed_ids=None):
    if not report.errors:
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
                 and (allowed_ids is None or obj.id in allowed_ids)
                 and ((obj.placement == 'floor' and obj.type != 'rug' and (
                        not obj.fixed or obj.type in {'shower', 'bathtub', 'vanity', 'sink', 'toilet',
                            'kitchen_cabinet', 'kitchen_counter', 'wardrobe', 'cooker'}))
                      or (obj.placement == 'wall' and obj.type in {'tv', 'picture', 'mirror'})) and not obj.locked
                 and obj.provenance == 'photo_observed' and obj.id in sources
                 and (obj.placement == 'wall' or sources[obj.id].appears_fixed is not True or obj.type in {
                     'shower', 'bathtub', 'vanity', 'sink', 'toilet', 'kitchen_cabinet', 'kitchen_counter', 'wardrobe', 'cooker'})]
        roots.sort(key=lambda obj: (-importance(sources[obj.id]),
                                   {'low': 0, 'medium': 1, 'high': 2}[sources[obj.id].confidence], obj.id))
        before_failures = spatial_failures(analysis, scene)
        previous_errors = {(e.code, e.entity_ids) for e in report.errors}
        accepted = False
        for root in roots[:MAX_CONFLICTS]:
            group = reconstruction_group(analysis, scene.objects, {root.id})
            if any(obj.locked or (obj.fixed and obj.placement == 'floor') or obj.provenance != 'photo_observed' for obj in scene.objects
                   if obj.id in group and obj.id != root.id):
                continue
            box = object_bounds(root).footprint
            px, pz = box.project((1, 0)), box.project((0, 1))
            hx, hz = (px[1]-px[0])/2, (pz[1]-pz[0])/2
            xs = {root.position.x, min(max(root.position.x, hx), scene.room.width_m-hx)}
            zs = {root.position.z, min(max(root.position.z, hz), scene.room.length_m-hz)}
            ys = {root.position.y}
            others = [obj for obj in scene.objects if obj.id not in group]
            collisions = {key for error in report.errors if set(error.entity_ids) & group
                          for key in error.entity_ids if key not in group}
            for other in others:
                if other.id not in collisions:
                    continue
                areas = [object_bounds(other)]
                if root.placement == 'wall':
                    # A media wall is hollow. Moving beyond its whole outer
                    # box would discard the TV instead of fitting its bay.
                    areas = [part.bounds for part in component_bounds(other)
                             if boxes_overlap(object_bounds(root), part.bounds)][:8] or areas
                for area in areas:
                    x, z = area.footprint.project((1, 0)), area.footprint.project((0, 1))
                    xs.update((x[0]-hx-.005, x[1]+hx+.005))
                    zs.update((z[0]-hz-.005, z[1]+hz+.005))
                    if root.placement == 'wall':
                        low, high = area.vertical
                        ys.update((low-root.dimensions_m.height-.005, high+.005))
            if root.placement == 'wall':
                if root.wall in {'north', 'south'}:
                    zs = {root.position.z}
                else:
                    xs = {root.position.x}
                ys.add(min(max(root.position.y, 0.), scene.room.height_m-root.dimensions_m.height))
            elif sources[root.id].wall_id and (root.fixed or sources[root.id].appears_fixed):
                # Installed fixtures stay on their photographed wall. Correct
                # tiny boundary overshoots, then search along that wall only.
                wall = next((m.wall for m in scene.wall_mapping if m.observation_id == sources[root.id].wall_id), None)
                if wall in {'north', 'south'}:
                    zs = {min(max(root.position.z, hz), scene.room.length_m-hz)}
                elif wall in {'east', 'west'}:
                    xs = {min(max(root.position.x, hx), scene.room.width_m-hx)}
            candidates = sorted(((x, y, z) for x in xs for y in ys for z in zs),
                key=lambda point: (hypot(point[0]-root.position.x, point[1]-root.position.y, point[2]-root.position.z), point))[:64]
            solids = {obj.id: component_bounds(obj) for obj in others if obj.type != 'rug'}
            obstacle_bounds = {obj.id: object_bounds(obj) for obj in others if obj.type != 'rug'}
            for x, y, z in candidates:
                dx, dy, dz = x-root.position.x, y-root.position.y, z-root.position.z
                if not .000001 < hypot(dx, dy, dz) <= MAX_SHIFT_M:
                    continue
                candidate = scene.model_copy(deep=True)
                translated = [obj for obj in candidate.objects if obj.id in group]
                for obj in translated:
                    obj.position.x += dx
                    obj.position.y += dy
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
                notes.append(f"{root.id}: estimated {root.placement} position recovered locally by dx={dx:.3f} m, dy={dy:.3f} m, dz={dz:.3f} m; supported items followed and sizes, orientation and room measurements were preserved.")
                accepted = True
                break
            if accepted:
                break
        if not accepted or not report.errors:
            break
    if scene is not original:
        scene.assumptions = [*notes, *scene.assumptions][:50]
    return scene, report
