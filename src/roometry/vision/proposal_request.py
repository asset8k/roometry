"""Text-only structured scene request, reusing the bounded Responses transport."""

import json

from openai.types.responses.response_create_params import (
    ResponseCreateParamsNonStreaming,
)

from roometry.config import Settings
from roometry.scenes.assets import ASSET_REGISTRY
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
            }
        )
    context = {
        "observations": analysis.observations.model_dump(mode="json"),
        "input_options": analysis.input_options.model_dump(mode="json"),
        "room_dimensions": measurements.dimensions.model_dump(mode="json"),
        "estimated_axes": measurements.estimated,
        "asset_bindings": bindings,
        "previous_draft": draft.model_dump(mode="json") if draft else None,
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
        "max_output_tokens": settings.analysis_max_output_tokens,
        "store": False,
        "tools": [],
        "truncation": "disabled",
    }
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
