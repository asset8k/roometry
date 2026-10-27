"""Small assembly descriptions and precise repair coordinates from shared geometry."""

from itertools import combinations

from roometry.scenes.collision import component_bounds, first_component_overlap


def assembly_features(asset):
    """Describe components already present, avoiding duplicate fitted fixtures.

    Names come from trusted recipes, not model prose. This adds no collision
    exceptions: a countertop cutout is empty only where its parts are empty.
    """
    names = [part.name for part in asset.parts]
    features = []
    if any(part.shape == 'basin' or 'sink_' in part.name or 'bowl_' in part.name
           for part in asset.parts):
        features.append('basin')
    if any('tap_' in name or 'gooseneck_' in name for name in names):
        features.append('faucet')
    if any('worktop' in name or name.startswith('deck_') or 'draining_board' in name for name in names):
        features.append('worktop')
    if any('burner' in name for name in names):
        features.append('cooktop')
    return features


def component_feedback(objects, issues):
    """Only send solids actually implicated in a pairwise repair conflict."""
    by_id = {obj.id: obj for obj in objects}
    result = []
    seen = set()
    for issue in issues:
        if issue.code != 'geometry':
            continue
        for left_id, right_id in combinations(issue.entity_ids, 2):
            pair = tuple(sorted((left_id, right_id)))
            if pair in seen or left_id not in by_id or right_id not in by_id:
                continue
            seen.add(pair)
            left, right = (component_bounds(by_id[key]) for key in (left_id, right_id))
            conflict = first_component_overlap(left, right)
            if conflict is None:
                continue
            entries = []
            for key, parts, name in zip((left_id, right_id), (left, right), conflict):
                box = next(part.bounds for part in parts if part.name == name)
                entries.append({'object_id': key, 'part': name,
                    'x_m': list(box.footprint.project((1, 0))),
                    'y_m': list(box.vertical),
                    'z_m': list(box.footprint.project((0, 1)))})
            result.append({'conflicting_components': entries})
            if len(result) >= 16:
                return result
    return result


def construction_sequence(analysis):
    """Stable dependency order: shell and main objects before their accessories."""
    from roometry.vision.importance import importance

    objects = sorted(analysis.observations.objects, key=lambda obj: (importance(obj), obj.id))
    remaining = {obj.id: obj for obj in objects}
    sequence = []
    while remaining:
        ready = [obj for obj in remaining.values() if obj.support_object_id not in remaining]
        if not ready:
            break  # Invalid cycles are rejected by observation validation.
        for obj in ready:
            sequence.append(obj.id)
            del remaining[obj.id]
    return sequence
