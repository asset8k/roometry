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


def parse_scene_draft(decoded, analysis):
    decoded = photo_references(decoded, analysis.observations.image_ids, decode=True)
    try:
        return SceneDraft.model_validate(decoded)
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
        return SceneDraft.model_validate(recovered)
