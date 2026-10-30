"""Bounded reconstruction pose recovery from explicit photo relationships.

This is not layout optimization. Only failed, unambiguous high-confidence
relationships supply candidates; dimensions, identities and observations stay
unchanged. Every accepted pose must improve fidelity without new geometry or
clearance failures. No paid calls, random search or cross-room relocation.
"""

from math import atan2, cos, hypot, pi, sin

from roometry.scenes.clearance_acceptance import clearance_regressions
from roometry.scenes.geometry import WALL_ROTATIONS_RAD, object_bounds
from roometry.scenes.validation import validate_geometry
from roometry.vision.fidelity import evaluate_fidelity
from roometry.vision.importance import importance
from roometry.vision.functional_groups import reconstruction_group
from roometry.vision.spatial import planar_bounds, relationship_error, relationship_limit

MAX_WALL_SHIFT_M = .5
MAX_ROOTS = 12
MAX_CHECKS = 36


def failed_relations(analysis, scene):
    return {(check.kind, tuple(check.entity_ids), check.message)
            for check in evaluate_fidelity(analysis, scene).checks
            if not check.passed and (check.required or check.kind in {'wall', 'facing', 'adjacency', 'support'})
            and check.kind != 'coverage'}


def recover_evidence_placements(analysis, scene, report, *, allowed_ids=None):
    walls = {entry.observation_id: entry.wall for entry in scene.wall_mapping}
    sources = {obj.id: obj for obj in analysis.observations.objects}
    relations = [r for r in analysis.observations.relationships
                 if r.confidence == 'high' and r.alternative_target_id is None
                 and r.kind in {'faces', 'against_wall'}]
    subjects = {relation.subject_id for relation in relations}
    roots = sorted((obj for obj in scene.objects if obj.placement == 'floor'
                    and obj.provenance == 'photo_observed' and not obj.locked
                    and obj.id in sources and obj.id in subjects),
                   key=lambda obj: (importance(sources[obj.id]), obj.id))
    checks = 0
    for root_before in roots[:MAX_ROOTS]:
        if allowed_ids is not None and root_before.id not in allowed_ids:
            continue
        root = next(obj for obj in scene.objects if obj.id == root_before.id)
        evidence = [r for r in relations if r.subject_id == root.id]
        def objectives(candidate):
            return {r.id for r in evidence if relationship_error(candidate, r.subject_id, r.target_id, r.kind)
                    > relationship_limit(candidate, r.kind, corrective=r.kind == 'against_wall')}

        before_objectives = objectives(scene)
        if not before_objectives:
            continue
        wall = next((walls[r.target_id] for r in evidence
                     if r.kind == 'against_wall' and r.target_id in walls), None)
        angles = [root.rotation_y_rad]
        if wall:
            angles.append(WALL_ROTATIONS_RAD[wall])
        for relation in evidence:
            bounds = planar_bounds(relation.target_id, scene, walls)
            if relation.kind == 'faces' and bounds is not None:
                dx = (bounds[0] + bounds[1]) / 2 - root.position.x
                dz = (bounds[2] + bounds[3]) / 2 - root.position.z
                if hypot(dx, dz) > .05:
                    angles.append(atan2(dx, dz) % (2 * pi))
        group = reconstruction_group(analysis, scene.objects, {root.id})
        if any(obj.locked or obj.provenance != 'photo_observed'
               or (obj.id != root.id and obj.fixed and obj.placement == 'floor')
               for obj in scene.objects if obj.id in group):
            continue
        failures = failed_relations(analysis, scene)
        errors = {(error.code, error.entity_ids) for error in report.errors}
        for angle in dict.fromkeys(angles):
            if checks >= MAX_CHECKS:
                return scene, report
            candidate = scene.model_copy(deep=True)
            revised = next(obj for obj in candidate.objects if obj.id == root.id)
            revised.rotation_y_rad = angle
            if wall:
                box = object_bounds(revised).footprint
                xs, zs = box.project((1, 0)), box.project((0, 1))
                hx, hz = (xs[1] - xs[0]) / 2, (zs[1] - zs[0]) / 2
                if wall == 'north': revised.position.z = hz
                elif wall == 'south': revised.position.z = scene.room.length_m - hz
                elif wall == 'west': revised.position.x = hx
                else: revised.position.x = scene.room.width_m - hx
            dx, dz = revised.position.x - root.position.x, revised.position.z - root.position.z
            if hypot(dx, dz) > MAX_WALL_SHIFT_M:
                continue
            delta = angle - root.rotation_y_rad
            c, s = cos(delta), sin(delta)
            for obj in candidate.objects:
                if obj.id not in group or obj.id == root.id:
                    continue
                x, z = obj.position.x - root.position.x, obj.position.z - root.position.z
                obj.position.x = revised.position.x + c * x + s * z
                obj.position.z = revised.position.z - s * x + c * z
                obj.rotation_y_rad = (obj.rotation_y_rad + delta) % (2 * pi)
            checks += 1
            checked = validate_geometry(candidate)
            new_errors = {(error.code, error.entity_ids) for error in checked.errors}
            if (not new_errors <= errors or clearance_regressions(report, checked)
                    or any(set(error.entity_ids) & group for error in checked.errors)
                    or not failed_relations(analysis, candidate) <= failures
                    or not objectives(candidate) < before_objectives):
                continue
            candidate.assumptions = [
                f'{root.id}: estimated pose aligned to high-confidence photo relationships '
                f'({", ".join(r.id for r in evidence)}); shift={hypot(dx, dz):.3f} m, '
                'supported objects followed; sizes and room measurements preserved.',
                *candidate.assumptions,
            ][:50]
            scene, report = candidate, checked
            break
    return scene, report
