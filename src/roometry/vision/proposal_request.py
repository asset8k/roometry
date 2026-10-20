"""Text-only structured scene request, reusing the bounded Responses transport."""

import json

from openai.types.responses.response_create_params import (
    ResponseCreateParamsNonStreaming,
)

from roometry.config import Settings
from roometry.scenes.assets import ASSET_REGISTRY
from roometry.vision.attachments import anchor_placements
from roometry.vision.errors import AnalysisFailure
from roometry.vision.prompts import SCENE_PROPOSAL_INSTRUCTIONS
from roometry.vision.proposal_schemas import ProposalIssue, SceneDraft
from roometry.vision.proposals import Measurements
from roometry.vision.schemas import RoomAnalysisResponse


def build_proposal_request(
    settings: Settings,
    analysis: RoomAnalysisResponse,
    measurements: Measurements,
    draft: SceneDraft | None,
    issues: list[ProposalIssue],
) -> ResponseCreateParamsNonStreaming:
    bindings = []
    for obj in analysis.observations.objects:
        asset_id = f"{obj.type}_standard_01"
        asset = ASSET_REGISTRY[asset_id]
        bindings.append(
            {
                "id": obj.id,
                "asset_id": asset_id,
                "placements": sorted(asset.placements),
                "supplies_surface": asset.supplies_surface,
                **(
                    {
                        "opening_ids": [
                            opening.id
                            for opening in analysis.observations.openings
                            if obj.wall_id is None or opening.wall_id == obj.wall_id
                        ]
                    }
                    if obj.type == "window_covering"
                    else {}
                ),
            }
        )
    normalized = (
        draft.model_copy(
            update={
                "objects": anchor_placements(analysis, measurements.dimensions, draft)
            }
        )
        if draft is not None
        else None
    )
    by_id = {obj.id: obj for obj in normalized.objects} if normalized else {}
    attachment_constraints = []
    for obj in by_id.values():
        support = (
            by_id.get(obj.support_object_id)
            if obj.support_object_id is not None
            else None
        )
        if obj.placement == "surface" and support is not None:
            attachment_constraints.append(
                {
                    "object_id": obj.id,
                    "support_object_id": support.id,
                    "support_center": {
                        "x": support.position.x,
                        "z": support.position.z,
                    },
                    "support_rotation_y_rad": support.rotation_y_rad,
                    "support_top_y_m": support.position.y + support.dimensions_m.height,
                    "available_surface_m": {
                        "width": support.dimensions_m.width,
                        "depth": support.dimensions_m.depth,
                    },
                    "object_dimensions_m": obj.dimensions_m.model_dump(),
                }
            )
    context = {
        "observations": analysis.observations.model_dump(mode="json"),
        "input_options": analysis.input_options.model_dump(mode="json"),
        "room_dimensions": measurements.dimensions.model_dump(mode="json"),
        "estimated_axes": measurements.estimated,
        "asset_bindings": bindings,
        # Feedback describes the assembled poses, including server attachments,
        # so repair must see those same coordinates rather than the raw draft.
        "previous_draft": normalized.model_dump(mode="json") if normalized else None,
        "attachment_constraints": attachment_constraints,
        "validation_feedback": [item.model_dump(mode="json") for item in issues],
    }
    payload: ResponseCreateParamsNonStreaming = {
        "model": settings.model_name,
        "instructions": SCENE_PROPOSAL_INSTRUCTIONS,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": json.dumps(context, ensure_ascii=True),
                    }
                ],
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "room_scene_draft",
                "strict": True,
                "schema": SceneDraft.model_json_schema(),
            }
        },
        "max_output_tokens": settings.scene_max_output_tokens,
        "store": False,
        "tools": [],
        "truncation": "disabled",
    }
    if settings.scene_reasoning_effort != "default":
        payload["reasoning"] = {"effort": settings.scene_reasoning_effort}
    if (
        len(json.dumps(payload, ensure_ascii=True).encode())
        > settings.analysis_max_request_bytes
    ):
        raise AnalysisFailure(
            413,
            "request_too_large",
            "The observations exceed the scene proposal request limit.",
        )
    return payload
