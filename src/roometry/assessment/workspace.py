"""All declared workstations, with photo-backed chair and device requirements."""

from roometry.assessment.schemas import AssessmentCard
from roometry.scenes.scoring import ScoreMetric


def belongs_to_workspace(objects, child, desk):
    seen, key = set(), child
    while key in objects and key not in seen and key != desk:
        seen.add(key)
        key = objects[key].support_object_id
    return key == desk


def workspace_card(scene, report, evidence, confidence):
    def unknown(message, status='unassessed'):
        return AssessmentCard(status=status, score=None, confidence='low', source='geometry', explanation=message)

    objects = {o.id:o for o in scene.objects}
    if (evidence.missing_workspace and not evidence.workspace_ids) or evidence.workspace_ids - objects.keys():
        return unknown('An observed workstation is missing. Complete the reconstruction before rating workspace.')
    if any(child not in objects or parent not in objects for child,parent in (*evidence.chair_pairs, *evidence.workspace_devices)):
        return unknown('An observed workstation chair or display is missing. Restore it before rating workspace.')
    if any(not belongs_to_workspace(objects,child,desk) for child,desk in evidence.workspace_devices):
        return unknown('An observed display is detached from its workstation. Correct its support before rating workspace.')
    if not any(o.type == 'desk' for o in scene.objects):
        return unknown('No dedicated workspace was identified. You can choose to create one later.', 'not_applicable')
    if not report.workstations:
        return unknown('There is not enough geometry to assess the workstation.')
    pairs = dict((desk,chair) for chair,desk in evidence.chair_pairs)
    values, tops, rooms, clearances, caps, usable, areas = [], [], [], [], [], 0, 0.
    for station in report.workstations:
        p = report.policy
        height_ok = p.minimum_worktop_height_m <= station.worktop_height_m <= p.maximum_worktop_height_m
        size = min(1., station.worktop_width_m / p.minimum_worktop_width_m) * min(1., station.worktop_depth_m / p.minimum_worktop_depth_m)
        chair_ok = station.permitted_chair_id is not None and (
            station.object_id not in pairs or station.permitted_chair_id == pairs[station.object_id])
        # A fitting unrelated chair cannot stand in for an explicitly paired one.
        top, room = (4*size if height_ok else 0.), (3. if station.approach_inside_room else 0.)
        clear = 3. if station.approach_clear and chair_ok else 0.
        cap = 3. if not height_ok or not station.approach_inside_room else 5. if station.blocking_object_ids or not chair_ok else 10.
        values.append(min(cap, top+room+clear)); tops.append(top); rooms.append(room); clearances.append(clear); caps.append(cap)
        if height_ok and station.approach_clear and chair_ok:
            usable += 1
            areas += station.worktop_width_m * station.worktop_depth_m
    n = len(values)
    explanation = (f'{usable} of {n} workstations have usable worktop height, a fitting chair and unobstructed working-side space. '
        'The rating averages every workstation; missing or misplaced chairs and obstructed approaches reduce it. '
        'Lighting, posture and desktop clutter are not measured.')
    return AssessmentCard(status='rated', score=round(sum(values)/n,1), confidence=confidence,
        source='geometry', explanation=explanation, entity_ids=[s.object_id for s in report.workstations], metrics=[
            ScoreMetric(key=key,value=float(value),unit=unit) for key,value,unit in (
                ('assessed_workstations',n,'count'), ('usable_workstation_count',usable,'count'),
                ('usable_worktop_area',areas,'m2'), ('worktop_points',sum(tops)/n,'count'),
                ('working_side_room_points',sum(rooms)/n,'count'), ('clear_approach_points',sum(clearances)/n,'count'),
                ('usability_cap',min(caps),'count'))])
