"""Request-only photo content and strict provider schemas; never checkpoint bytes."""

import base64
from copy import deepcopy

from openai.types.responses import ResponseInputContentParam
from pydantic import BaseModel

from roometry.vision.errors import AnalysisFailure
from roometry.vision.schemas import ReconstructionConstraints, RoomAnalysisResponse


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


def apply_constraints(analysis: RoomAnalysisResponse, constraints: ReconstructionConstraints) -> RoomAnalysisResponse:
    """User choices override hypotheses only; never invent observations or images."""
    effective = analysis.model_copy(deep=True)
    by_id = {obj.id: obj for obj in effective.observations.objects}
    for object_id, selected in constraints.object_types.items():
        source = by_id.get(object_id)
        if source is None or source.alternative_type is None or selected not in {source.type, source.alternative_type, None}:
            raise ValueError("Identity answer must choose a recorded hypothesis.")
        source.type = selected
        source.alternative_type = None
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
    return RoomAnalysisResponse.model_validate(effective.model_dump())
