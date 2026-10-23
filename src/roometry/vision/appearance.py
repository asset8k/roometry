"""Audited color/finish hypotheses; source photos and user preferences stay intact."""

import re

from pydantic import ValidationError

from roometry.scenes.schemas import AppearanceCorrection, MaterialColors, RoomFinishes
from roometry.vision.schemas import FinishObservation, RoomAnalysisResponse


def resolve_appearance(analysis: RoomAnalysisResponse, records: list[AppearanceCorrection]) -> tuple[RoomAnalysisResponse, list[tuple[str, str]]]:
    if not records:
        return analysis, []
    effective = analysis.model_copy(deep=True)
    sources = {obj.id: obj for obj in analysis.observations.objects}
    targets = {obj.id: obj for obj in effective.observations.objects}
    seen, errors = set(), []
    for record in records:
        key = record.entity_id, record.field
        source = sources.get(record.entity_id)
        room_field = record.field in {"wall_color", "floor_color", "floor_pattern"}
        if key in seen or (room_field != (record.entity_id is None)) or (not room_field and source is None):
            errors.append((record.entity_id or "room", "Appearance revisions must name a distinct valid object or room finish field."))
            continue
        seen.add(key)
        views = analysis.observations.image_ids if room_field else source.source_image_ids
        if len(set(record.source_image_ids)) != len(record.source_image_ids) or not set(record.source_image_ids) <= set(views):
            errors.append((record.entity_id or "room", "Appearance corrections require the original photo evidence."))
            continue
        if room_field:
            old = getattr(analysis.observations.finishes.finishes, record.field) if analysis.observations.finishes else None
        else:
            old = source.color if record.field == "color" else getattr(source.material_colors, record.field, None)
        valid = record.revised_value in {"wood", "tile", "plain"} if record.field == "floor_pattern" else re.fullmatch(r"#[0-9a-fA-F]{6}", record.revised_value)
        if not valid or record.previous_value != old or record.revised_value == old:
            errors.append((record.entity_id or "room", "Record an exact before/after appearance revision with a supported color or floor pattern."))
            continue
        if room_field:
            if effective.observations.finishes is None:
                effective.observations.finishes = FinishObservation(finishes=RoomFinishes(), confidence="medium", source_image_ids=record.source_image_ids, evidence=record.reason)
            setattr(effective.observations.finishes.finishes, record.field, record.revised_value)
        elif record.field == "color":
            targets[record.entity_id].color = record.revised_value
        else:
            target = targets[record.entity_id]
            if target.material_colors is None:
                target.material_colors = MaterialColors()
            setattr(target.material_colors, record.field, record.revised_value)
    if not errors:
        try:
            effective = RoomAnalysisResponse.model_validate(effective.model_dump())
        except ValidationError:
            errors.append(("room", "Appearance corrections must preserve valid observations."))
    return (analysis, errors) if errors else (effective, [])
