"""Quarantine malformed poses for known observations, not whole room drafts.

Core wall mapping, identity attribution, envelope and collection limits remain
strict. Omitted poses become explicit unresolved entities during partial assembly;
an omitted repair upsert leaves the previous validated pose unchanged.
"""

from copy import deepcopy

from pydantic import ValidationError

from roometry.scenes.schemas import AppearanceCorrection, EstimateCorrection, InterpretationCorrection
from roometry.vision.proposal_schemas import DraftObject, DraftOpening, SceneDraft
from roometry.vision.proposals import notes
from roometry.vision.context_budget import photo_references


def normalize_mounting(draft, analysis):
    """A floor object's wall association is evidence, not a mounting reference.

    Only a validated compass value on a known floor pose is redundant. Invalid
    values, supports, opening links, placements and identities remain checked.
    """
    known = {obj.id for obj in analysis.observations.objects}
    ids = [obj.id for obj in draft.objects if obj.id in known and obj.placement == 'floor'
           and obj.wall is not None and obj.support_object_id is None
           and obj.support_surface_id is None and obj.opening_id is None]
    if not ids:
        return draft
    result = draft.model_copy(deep=True)
    for obj in result.objects:
        if obj.id in ids:
            obj.wall = None
    result.warnings = notes(result.warnings, [
        f"Redundant floor mounting wall references were cleared for {len(ids)} objects; observed wall associations are preserved in the photo analysis."
    ])
    return result


def parse_scene_draft(decoded, analysis):
    decoded = photo_references(decoded, analysis.observations.image_ids, decode=True)
    try:
        return normalize_mounting(SceneDraft.model_validate(decoded), analysis)
    except ValidationError as original:
        if not isinstance(decoded, dict):
            raise
        recovered = deepcopy(decoded)
        fields = (
            ("objects", DraftObject, 200), ("openings", DraftOpening, 32),
            ("estimate_corrections", EstimateCorrection, 200),
            ("interpretation_corrections", InterpretationCorrection, 200),
            ("appearance_corrections", AppearanceCorrection, 200),
        )
        for field, _, maximum in fields:
            value = decoded.get(field, [])
            if not isinstance(value, list) or len(value) > maximum:
                raise original
            recovered[field] = []
        for field in ("objects", "openings"):
            expected = {item.id for item in getattr(analysis.observations, field)}
            values = decoded.get(field, [])
            if any(not isinstance(item, dict) or not isinstance(item.get("id"), str) or item["id"] not in expected for item in values):
                raise original
            if len({item["id"] for item in values}) != len(values):
                raise original
        # Don't hide unknown envelope fields, broken wall assignments, bad
        # narrative fields, or a missing required object/opening collection.
        if "objects" not in decoded or "openings" not in decoded:
            raise original
        SceneDraft.model_validate(recovered)
        warnings = []
        for field, model, _ in fields:
            for index, raw in enumerate(decoded.get(field, []), 1):
                try:
                    value = model.model_validate(raw)
                except ValidationError:
                    warnings.append(f"Invalid {field.replace('_', ' ')} entry {index} was ignored; valid entries and original observations were retained.")
                    continue
                recovered[field].append(value.model_dump())
        recovered["warnings"] = notes(warnings, recovered["warnings"])
        return normalize_mounting(SceneDraft.model_validate(recovered), analysis)
