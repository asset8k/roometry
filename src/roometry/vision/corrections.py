"""Validate every size revision against the original photo hypothesis."""

from math import isclose

from roometry.vision.proposal_schemas import SceneDraft, DraftObject, DraftOpening
from roometry.vision.schemas import RoomAnalysisResponse, ObjectObservation, OpeningObservation


def correction_errors(analysis: RoomAnalysisResponse, draft: SceneDraft) -> list[tuple[str, str]]:
    sources = {o.id: o for o in [*analysis.observations.objects, *analysis.observations.openings]}
    proposed = {o.id: o for o in [*draft.objects, *draft.openings]}
    records = {}
    errors = []
    for record in draft.estimate_corrections:
        key = record.entity_id, record.field
        source, target = sources.get(record.entity_id), proposed.get(record.entity_id)
        if key in records or source is None or target is None:
            errors.append((record.entity_id, "Size corrections must identify a distinct field on an existing observation."))
            continue
        records[key] = record
        references = record.source_image_ids
        if len(set(references)) != len(references) or not set(references) <= set(source.source_image_ids):
            errors.append((record.entity_id, "Size corrections require evidence from this object's original photo views."))
    consumed = set()
    for entity_id, target in proposed.items():
        source = sources.get(entity_id)
        if source is None:
            continue
        is_object = isinstance(source, ObjectObservation)
        fields = ("width", "depth", "height") if is_object else ("width_m", "height_m", "sill_height_m")
        for field in fields:
            if isinstance(source, ObjectObservation) and isinstance(target, DraftObject):
                known = getattr(source.estimated_dimensions_m, field, None)
                revised = getattr(target.dimensions_m, field, None)
            elif isinstance(source, OpeningObservation) and isinstance(target, DraftOpening):
                known = getattr(source, "estimated_" + field)
                revised = getattr(target, field)
            else:
                errors.append((entity_id, "Correction entity kind does not match the original observation."))
                continue
            if known is None or revised is None or isclose(known, revised, abs_tol=1e-6, rel_tol=0):
                continue
            key = entity_id, field
            correction = records.get(key)
            consumed.add(key)
            if correction is None or not isclose(correction.previous_m, known, abs_tol=1e-6) or not isclose(correction.revised_m, revised, abs_tol=1e-6):
                errors.append((entity_id, "Preserve recorded estimates or provide an exact before/after correction with photo evidence and a reason."))
            elif known > 0 and not .5 <= revised / known <= 2:
                errors.append((entity_id, "This revision exceeds the bounded photo-estimate correction range (0.5–2 times); request better evidence instead."))
    for key in records.keys() - consumed:
        errors.append((key[0], "A correction must correspond to an actual changed photo estimate; room measurements cannot be corrected here."))
    return errors
