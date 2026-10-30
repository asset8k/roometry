"""Explicit workstation relationships; proximity alone never establishes a pair."""

from math import cos, sin

from roometry.scenes.dependencies import support_closure
from roometry.vision.spatial import relationship_error, relationship_limit


def workstation_pairs(analysis, objects):
    present = {obj.id for obj in objects}
    sources = {obj.id: obj for obj in analysis.observations.objects}
    choices = {}
    for relation in analysis.observations.relationships:
        child, parent = sources.get(relation.subject_id), sources.get(relation.target_id)
        if (relation.kind == 'faces' and relation.confidence == 'high' and relation.alternative_target_id is None
            and child and parent and child.type == 'chair' and parent.type == 'desk'
            and child.id in present and parent.id in present):
            choices.setdefault(child.id, set()).add(parent.id)
    return {child: next(iter(parents)) for child, parents in choices.items() if len(parents) == 1}


def reconstruction_group(analysis, objects, roots):
    pairs = workstation_pairs(analysis, objects)
    # Missing supported observations still belong to a restored parent's scope.
    # Their identities come from photos, never from generated mesh inventories.
    linked = {obj.id: obj for obj in [*analysis.observations.objects, *objects]}
    return support_closure(list(linked.values()), set(roots) | {key for key, parent in pairs.items() if parent in roots})


def carry_review_groups(analysis, scene, previous, merged, findings):
    """Complete rigid follower poses after a reviewed parent moves.

    Independently reviewed followers keep their requested pose. Existing flags,
    dimensions, models and appearance never change through this completion.
    Protected followers veto a parent transform rather than being abandoned.
    """
    result = merged.model_copy(deep=True)
    old, new = ({obj.id: obj for obj in draft.objects} for draft in (previous, result))
    protected = {obj.id for obj in scene.objects if obj.locked or obj.provenance == 'user_added'
                 or (obj.fixed and obj.placement == 'floor')}
    independent = {key for finding in findings if finding.kind in {'position', 'orientation', 'support', 'identity'}
                   for key in finding.entity_ids}
    changed = [key for key in independent & old.keys() & new.keys()
               if old[key].position != new[key].position or old[key].rotation_y_rad != new[key].rotation_y_rad]
    for key in sorted(changed):
        followers = reconstruction_group(analysis, previous.objects, {key}) - {key}
        if followers & protected:
            raise ValueError('The reviewed transform would move a protected follower.')
        prior, revised = old[key], new[key]
        delta = revised.rotation_y_rad - prior.rotation_y_rad
        c, s = cos(delta), sin(delta)
        for follower in followers - independent:
            if follower not in new or follower not in old:
                continue
            original, candidate = old[follower], new[follower]
            x, z = original.position.x - prior.position.x, original.position.z - prior.position.z
            candidate.position.x = revised.position.x + c*x + s*z
            candidate.position.y = original.position.y + revised.position.y - prior.position.y
            candidate.position.z = revised.position.z - s*x + c*z
            candidate.rotation_y_rad = original.rotation_y_rad + delta
    return result


def workstation_regressions(analysis, before, after):
    failures = []
    for chair, desk in workstation_pairs(analysis, before.objects).items():
        for relation in ('near', 'faces'):
            if (relationship_error(before, chair, desk, relation) <= relationship_limit(before, relation)
                and relationship_error(after, chair, desk, relation) > relationship_limit(after, relation)):
                failures.append((chair, desk))
    return failures
