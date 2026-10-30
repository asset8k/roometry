"""Request-only photo content and strict provider schemas; never checkpoint bytes."""

import base64
from copy import deepcopy

from openai.types.responses import ResponseInputContentParam
from pydantic import BaseModel

from roometry.vision.errors import AnalysisFailure
from roometry.vision.schemas import MAX_NOTES, ReconstructionConstraints, RoomAnalysisResponse, WallAxisResolution


def strict_schema(model: type[BaseModel]) -> dict:
    schema = deepcopy(model.model_json_schema())

    def visit(value):
        if isinstance(value, dict):
            value.pop("default", None)
            if value.get("type") == "object" and "properties" in value:
                value["required"] = list(value["properties"])
                value["additionalProperties"] = False
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(schema)
    return schema


def photo_content(image_ids: list[str], images: list[bytes], max_bytes: int) -> list[ResponseInputContentParam]:
    if len(image_ids) != len(images):
        raise AnalysisFailure(503, "storage_unavailable", "The stored photos are unavailable. Upload them again.")
    if sum(4 * ((len(image) + 2) // 3) for image in images) > max_bytes:
        raise AnalysisFailure(413, "request_too_large", "The room photos exceed the construction request limit.")
    content: list[ResponseInputContentParam] = []
    for image_id, image in zip(image_ids, images):
        content.extend([
            {"type": "input_text", "text": f"Photo evidence ID: {image_id}"},
            {"type": "input_image", "detail": "high", "image_url": "data:image/jpeg;base64," + base64.b64encode(image).decode("ascii")},
        ])
    return content


def apply_constraints(analysis: RoomAnalysisResponse, constraints: ReconstructionConstraints, *, wall_axes: WallAxisResolution | None = None) -> RoomAnalysisResponse:
    """User choices override hypotheses only; never invent observations or images."""
    effective = analysis.model_copy(deep=True)
    if wall_axes is not None:
        effective.wall_axes = wall_axes.model_copy(deep=True)
    if constraints.wall_axis is not None and constraints.wall_axis.wall_id is not None and constraints.wall_axis.wall_id not in {w.id for w in analysis.observations.walls}:
        raise ValueError("The wall-axis answer must reference an observed wall.")
    by_id = {obj.id: obj for obj in effective.observations.objects}
    for object_id, selected in constraints.object_types.items():
        source = by_id.get(object_id)
        if source is None or source.alternative_type is None or selected not in {source.type, source.alternative_type, None}:
            raise ValueError("Identity answer must choose a recorded hypothesis.")
        if selected is not None:
            if selected != source.type:
                source.label = selected.replace("_", " ").capitalize() + " (user confirmed)"
            source.notes = [f"User confirmed category: {selected}. Earlier category hypotheses must not override this answer.", *source.notes][:MAX_NOTES]
        source.type = selected
        source.alternative_type = None
    if len(set(constraints.approximate_object_ids)) != len(constraints.approximate_object_ids):
        raise ValueError("Approximate identity choices must be unique.")
    for object_id in constraints.approximate_object_ids:
        source = by_id.get(object_id)
        if source is None or object_id in constraints.object_types:
            raise ValueError("Approximation must name an observed item without a confirmed or excluded category.")
        if source.type is None and source.alternative_type is not None:
            source.type, source.alternative_type = source.alternative_type, None
        source.notes = [
            "User requested the closest photo-based model; category and dimensions remain unconfirmed hypotheses.",
            *source.notes,
        ][:MAX_NOTES]
    for object_id, target in constraints.facing_targets.items():
        relations = [r for r in effective.observations.relationships if r.kind == "faces" and r.subject_id == object_id]
        if len(relations) != 1 or relations[0].alternative_target_id is None or target not in {relations[0].target_id, relations[0].alternative_target_id, None}:
            raise ValueError("Facing answer must choose a recorded target.")
        relation = relations[0]
        if target is None:
            effective.observations.relationships.remove(relation)
        else:
            relation.target_id = target
            relation.alternative_target_id = None
            relation.confidence = "high"
    # Also canonicalize cached pre-upgrade observations, after respecting any
    # explicit category choice. Stored source evidence is never rewritten.
    from roometry.vision.features import normalize_fixture_category
    for source in effective.observations.objects:
        if source.id not in constraints.object_types:
            raw = source.model_dump()
            normalize_fixture_category(raw, effective.observations.objects)
            for field in ("type", "notes"):
                setattr(source, field, raw[field])
    return RoomAnalysisResponse.model_validate(effective.model_dump())
