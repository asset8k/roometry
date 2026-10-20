"""Bounded diagnostics: schema paths/codes and provider IDs, never raw model data."""

import re
from functools import lru_cache

from openai.types.responses import Response
from pydantic import BaseModel, ValidationError


@lru_cache
def _field_names(model: type[BaseModel]) -> frozenset[str]:
    fields: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, dict):
            properties = value.get("properties")
            if isinstance(properties, dict):
                fields.update(properties)
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(model.model_json_schema())
    return frozenset(fields)


_CONSISTENCY_RULES = {
    "Use null when no room dimensions can be estimated.": "unknown_room_dimensions",
    "Use null when no object dimensions can be estimated.": "unknown_object_dimensions",
    "Analysis image IDs must be unique.": "duplicate_image_id",
    "Unknown room type requires null confidence, and vice versa.": "room_type_confidence",
    "Observation IDs must be unique across all entity kinds.": "duplicate_entity_id",
    "Evidence must reference distinct analysis image IDs.": "image_reference",
    "Wall references must identify an observed wall.": "wall_reference",
    "Object support references must exist and be acyclic.": "support_reference",
    "A support reference requires surface placement.": "support_placement",
    "A door sill is unknown or at floor level.": "door_sill",
    "Possible duplicate pairs must be distinct and unique.": "duplicate_pair",
    "Possible duplicates must reference observed entities.": "duplicate_reference",
    "Possible duplicates must refer to the same entity kind.": "duplicate_kind",
}


def validation_diagnostics(
    error: ValidationError, model: type[BaseModel]
) -> dict[str, object]:
    fields = _field_names(model)
    issues = []
    for failure in error.errors(
        include_input=False, include_context=False, include_url=False
    )[:12]:
        path = (
            ".".join(
                str(part) if type(part) is int else part if part in fields else "?"
                for part in failure["loc"][:8]
            )
            or "$"
        )
        code = failure["type"]
        if not re.fullmatch(r"[a-z_]{1,64}", code):
            code = "schema_error"
        rule = _CONSISTENCY_RULES.get(failure["msg"].removeprefix("Value error, "))
        issues.append({"path": path, "code": code, **({"rule": rule} if rule else {})})
    return {"reason": "schema_validation", "issues": issues}


def provider_id(value: object) -> str | None:
    return (
        value
        if isinstance(value, str)
        and re.fullmatch(r"(?:req|resp)_[A-Za-z0-9_-]{1,160}", value)
        else None
    )


def response_diagnostics(response: Response | None) -> dict[str, object]:
    status = getattr(response, "status", None)
    incomplete = getattr(getattr(response, "incomplete_details", None), "reason", None)
    return {
        "response_id": provider_id(getattr(response, "id", None)),
        "provider_request_id": provider_id(getattr(response, "_request_id", None)),
        "status": status
        if isinstance(status, str)
        and status
        in {"completed", "failed", "incomplete", "cancelled", "queued", "in_progress"}
        else None,
        "incomplete_reason": incomplete
        if isinstance(incomplete, str)
        and incomplete in {"max_output_tokens", "content_filter"}
        else None,
    }
