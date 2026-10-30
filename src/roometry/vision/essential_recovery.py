"""Lean final construction allowance after output exhaustion; no extra calls."""

from roometry.vision.importance import importance

MAX_ESSENTIAL_OBJECTS = 32


def essential_analysis(analysis):
    objects = {obj.id: obj for obj in analysis.observations.objects}
    selected = set()
    for source in sorted(objects.values(), key=lambda obj: (importance(obj), obj.confidence == 'low', obj.id)):
        if importance(source) > 1 or source.type is None:
            continue
        group, key = set(), source.id
        while key in objects and key not in group:
            group.add(key)
            key = objects[key].support_object_id
        if len(selected | group) <= MAX_ESSENTIAL_OBJECTS:
            selected |= group
    result = analysis.model_copy(deep=True)
    result.observations.objects = [obj for obj in result.observations.objects if obj.id in selected]
    known = selected | {obj.id for obj in result.observations.openings} | {obj.id for obj in result.observations.walls}
    result.observations.relationships = [r for r in result.observations.relationships
        if {r.subject_id, r.target_id, *([r.alternative_target_id] if r.alternative_target_id else [])} <= known]
    result.observations.possible_duplicates = [p for p in result.observations.possible_duplicates if set(p.entity_ids) <= selected]
    return result, sorted(objects.keys() - selected)


def lean_schema(schema):
    definition = schema['$defs']['DraftObject']
    for field in ('dimensions_m', 'support_surface_id'):
        definition['properties'].pop(field)
    definition['required'] = list(definition['properties'])
    for field in ('estimate_corrections', 'interpretation_corrections', 'appearance_corrections'):
        schema['properties'][field] = {'type': 'array', 'items': {'type': 'null'}, 'maxItems': 0}
    for field in ('assumptions', 'warnings'):
        schema['properties'][field]['maxItems'] = 3


def expand_lean_draft(decoded):
    # Only request-omitted defaults are filled. No positions, categories,
    # support identities or missing objects can be manufactured here.
    if isinstance(decoded, dict) and isinstance(decoded.get('objects'), list):
        for obj in decoded['objects']:
            if isinstance(obj, dict):
                obj.setdefault('dimensions_m', None)
                obj.setdefault('support_surface_id', None)
    return decoded
