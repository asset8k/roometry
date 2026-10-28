"""Compact planning feedback from the existing geometry checks, no new solver."""

from itertools import combinations
from math import hypot

from roometry.scenes.geometry import object_bounds
from roometry.scenes.validation import validate_geometry


def planning_context(scene):
    report = validate_geometry(scene)
    walking = report.walking_space
    preferred = walking.policy.preferred_width_m if walking else .8
    floors = [o for o in scene.objects if o.placement == 'floor' and o.type != 'rug']
    boxes = {}
    for obj in floors:
        area = object_bounds(obj).footprint
        boxes[obj.id] = area.project((1, 0)), area.project((0, 1))
    gaps = []
    for a, b in combinations(floors, 2):
        if all(o.fixed or o.locked for o in (a, b)):
            continue
        ax, az = boxes[a.id]; bx, bz = boxes[b.id]
        dx = max(0., ax[0] - bx[1], bx[0] - ax[1])
        dz = max(0., az[0] - bz[1], bz[0] - az[1])
        gap = hypot(dx, dz)
        if .05 < gap < preferred:
            gaps.append({'object_ids': [a.id, b.id], 'bounding_gap_m': round(gap, 3)})
    return {
        'door_clearance': report.door_clearance.model_dump(mode='json') if report.door_clearance else None,
        'walking_space': walking.model_dump(mode='json') if walking else None,
        'nearby_gaps': sorted(gaps, key=lambda item: (item['bounding_gap_m'], item['object_ids']))[:12],
        'gap_interpretation': 'Local bounding-envelope gaps are planning hints, not proof of blocked routes. Component geometry and route validation remain authoritative. Resolving one gap may require moving both members of a furniture group.',
    }
