"""Audited hypothesis corrections; never mutate the owned source observations.

An item being fixed does not imply wall mounting. Attachment confidence is
separate from detection confidence. Explicit high-confidence attachments and
human-confirmed identities remain protected, even during reconstruction repair.
"""

from pydantic import ValidationError

from roometry.scenes.catalog import candidates
from roometry.scenes.schemas import InterpretationCorrection
from roometry.vision.schemas import RoomAnalysisResponse


def resolve_interpretations(
    analysis: RoomAnalysisResponse, records: list[InterpretationCorrection],
) -> tuple[RoomAnalysisResponse, list[tuple[str, str]]]:
    if not records:
        return analysis, []
    effective = analysis.model_copy(deep=True)
    sources = {obj.id: obj for obj in analysis.observations.objects}
    targets = {obj.id: obj for obj in effective.observations.objects}
    seen = set()
    errors = []
    for record in records:
        source = sources.get(record.entity_id)
        key = record.entity_id, record.field
        message = None
        if source is None or key in seen:
            message = "Interpretation corrections must name a distinct field on an observed object."
        elif (not set(record.source_image_ids) <= set(source.source_image_ids)
              or len(set(record.source_image_ids)) != len(record.source_image_ids)):
            message = "Use photo evidence from this object's recorded views."
        elif (getattr(source, record.field) != record.previous_value
              or record.previous_value == record.revised_value):
            message = "Record the exact original hypothesis and an actual revision."
        elif record.field == "type":
            if (source.confidence == "high" or source.alternative_type is None
                    or record.revised_value != source.alternative_type):
                message = "Only an uncertain recorded alternative type can replace a photo category; preserve confirmed identities."
        elif source.placement_confidence == "high":
            message = "Preserve this explicitly established attachment."
        elif record.field == "placement" and record.revised_value not in {"floor", "wall", "ceiling", "surface"}:
            message = "Choose an approved placement."
        elif record.field == "support_object_id" and record.revised_value is not None and (
            record.revised_value not in sources or record.revised_value == source.id
        ):
            message = "A revised support must be another observed object."
        if message is not None:
            errors.append((record.entity_id, message))
            continue
        seen.add(key)
        setattr(targets[record.entity_id], record.field, record.revised_value)

    # A high-confidence support relation is stronger than an uncertain placement
    # field. Lower-confidence relations are reconciled with the audited support.
    for relation in list(effective.observations.relationships):
        if relation.kind != "supported_by" or relation.subject_id not in targets:
            continue
        target = targets[relation.subject_id]
        if relation.target_id == target.support_object_id:
            continue
        if relation.confidence == "high":
            errors.append((target.id, "Preserve the established support relationship."))
        else:
            # Do not invent evidence for the new support; the correction record
            # establishes it (or its removal), and fidelity checks the reference.
            effective.observations.relationships.remove(relation)
    for entity_id in {record.entity_id for record in records} & targets.keys():
        obj = targets[entity_id]
        if obj.type is None or not candidates(obj.type, obj.placement):
            errors.append((entity_id, "The revised interpretation needs a compatible catalog model."))
    if not errors:
        try:
            effective = RoomAnalysisResponse.model_validate(effective.model_dump())
        except ValidationError:
            errors.extend((entity_id, "The revised attachment must preserve valid observation references and support relationships.")
                          for entity_id in sorted({r.entity_id for r in records}))
    return (analysis, errors) if errors else (effective, [])
