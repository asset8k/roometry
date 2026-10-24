"""Compact request views; original observations and validation remain intact."""

from copy import deepcopy
from itertools import combinations
import re

from roometry.scenes.catalog import dimensions_for


def select_photo_ids(observations, target=3, mandatory=()):
    """Smallest complementary set covering every recorded entity and relation.

    Analysis and final visual review still see all original images. This is an
    exact bounded set cover (at most ten images), not dropping arbitrary views.
    Unattributed evidence falls back to all images. Three views are preferred;
    unique evidence can require more. Repair citations are always included.
    """
    ids = observations.image_ids
    must = set(mandatory)
    if not must <= set(ids):
        raise ValueError("Repair photo evidence must belong to the input.")
    if len(ids) <= target:
        return list(ids)
    requirements = []
    for item in [*observations.walls, *observations.objects, *observations.openings, *observations.relationships]:
        sources = set(item.source_image_ids) & set(ids)
        if not sources:
            return list(ids)
        requirements.append(sources)
        span = getattr(item, "span", None)
        if span: requirements.append(set(span.source_image_ids))
    if observations.finishes:
        requirements.append(set(observations.finishes.source_image_ids))
    for size in range(min(target, len(ids)), len(ids) + 1):
        for choice in combinations(ids, size):
            chosen = set(choice)
            if must <= chosen and all(chosen & sources for sources in requirements):
                return list(choice)
    return list(ids)


def _terms(text):
    return set(re.findall(r"[a-z]+", text.lower().replace('_', ' '))) - {"a", "the", "with", "and", "on", "of", "in", "standard"}


def shortlist(models, observation, limit=4):
    # Retain all uncertain interpretations. Only redundant variant choices for
    # confidently classified objects are narrowed; server binding stays exact.
    if len(models) <= limit or observation.confidence != "high" or observation.alternative_type:
        return models
    words = _terms(observation.label)
    return sorted(models, key=lambda m: (-len(words & _terms(m['label'] + ' ' + m['asset_id'])), m['asset_id']))[:limit]


def compact_context(context, analysis, *, editable=None):
    result = deepcopy(context)
    definitions = {}
    observed = {o.id: o for o in analysis.observations.objects}
    previous = {o['id']: o for o in (result.get('previous_draft') or {}).get('objects', [])}
    support_ids = {o.support_object_id for o in analysis.observations.objects if o.support_object_id}
    unknown_support = any(o.placement == 'surface' and not o.support_object_id for o in analysis.observations.objects)
    support_models = {m['asset_id'] for b in result['asset_bindings'] if b['id'] in support_ids for m in b['models']}
    support_models.update(item['asset_id'] for item in result['hypothesis_alternatives'] if item['id'] in support_ids)

    def register(model):
        key = model['asset_id']
        existing = definitions.setdefault(key, {**model, 'configurations': {}})
        existing.pop('asset_id', None)  # Dictionary key is the single identity.
        existing.pop('appearance_regions', None)  # Detailed regions are checked in visual review.
        existing['appearance_fields'] = sorted(set(existing.get('appearance_fields', [])) | set(model.get('appearance_fields', [])))
        for placement, config in model['configurations'].items():
            w, h, d = dimensions_for(key, placement)
            surfaces = [{field: round(value, 6) if isinstance(value, float) else value for field, value in surface.items()
                         if field not in {'part', 'purpose'}} for surface in config['support_surfaces']]
            existing['configurations'][placement] = {'default_dimensions_m': {'width': w, 'height': h, 'depth': d},
                'support_surfaces': surfaces if unknown_support or key in support_models else []}
        return {'asset_id': key, 'placements': sorted(model['configurations'])}

    for binding in result['asset_bindings']:
        options = shortlist(binding['models'], observed[binding['id']])
        if editable is not None and binding['id'] not in editable and binding['id'] in previous:
            selected = previous[binding['id']]['asset_id']
            options = [model for model in options if model['asset_id'] == selected] or binding['models']
        binding['models'] = [register(model) for model in options]
    for alternative in result['hypothesis_alternatives']:
        key, placement = alternative['asset_id'], alternative['placement']
        register({'asset_id': key, 'label': alternative['label'], 'configurations': {placement: {
            'default_dimensions_m': alternative['default_dimensions_m'], 'support_surfaces': alternative['support_surfaces']}}})
        alternative.pop('default_dimensions_m')
        alternative.pop('support_surfaces')
        alternative.pop('label')
    result['asset_models'] = definitions
    result['observations'] = analysis.observations.model_dump(mode='json', exclude_none=True)
    result['input_options'] = analysis.input_options.model_dump(mode='json', exclude_none=True,
        exclude={'dimensions', 'goals', 'goal_text', 'style', 'budget'})
    # Full checked and raw frames remain available for repairs: no collision
    # neighbour or merge base is lost just to reduce prompt length.
    return result


def compact_schema(schema, context):
    """Drop redundant titles and restrict decoding to supplied catalog choices."""
    result = deepcopy(schema)
    def visit(value):
        if isinstance(value, dict):
            value.pop('title', None)
            for item in value.values(): visit(item)
        elif isinstance(value, list):
            for item in value: visit(item)
    visit(result)
    allowed = sorted(context['asset_models'])
    if allowed:
        result['$defs']['DraftObject']['properties']['asset_id'] = {'anyOf': [{'type': 'string', 'enum': allowed}, {'type': 'null'}]}
    return result


def photo_references(value, image_ids, *, decode=False):
    """Short request-only photo names; IDs in stored observations never change.

    Replacing only typed photo-reference fields avoids changing object identities
    or interpreting arbitrary user text. Unknown references remain invalid.
    """
    mapping = {key: f'photo_{index + 1}' for index, key in enumerate(image_ids)}
    if decode: mapping = {alias: key for key, alias in mapping.items()}
    if isinstance(value, dict):
        return {key: [mapping.get(item, item) if isinstance(item, str) else item for item in entry]
                if key in {'image_ids', 'source_image_ids', 'attached_image_ids'} and isinstance(entry, list)
                else photo_references(entry, image_ids, decode=decode) for key, entry in value.items()}
    if isinstance(value, list):
        return [photo_references(item, image_ids, decode=decode) for item in value]
    return value


def photo_reference_schema(schema, image_ids):
    result = deepcopy(schema)
    def visit(value):
        if isinstance(value, dict):
            refs = value.get('properties', {}).get('source_image_ids')
            if refs is not None:
                refs['items'] = {'type': 'string', 'enum': [f'photo_{i+1}' for i in range(len(image_ids))]}
            for entry in value.values(): visit(entry)
        elif isinstance(value, list):
            for entry in value: visit(entry)
    visit(result)
    return result
