"""Photo-grounded structured construction using the bounded Responses transport."""

import json
from openai.types.responses import ResponseInputContentParam
from dataclasses import asdict

from openai.types.responses.response_create_params import (
    ResponseCreateParamsNonStreaming,
)

from roometry.config import Settings
from roometry.scenes.assets import ASSET_REGISTRY
from roometry.scenes.catalog import candidates, object_surfaces
from roometry.scenes.geometry import catalog_surface_bounds
from roometry.vision.attachments import anchor_placements
from roometry.vision.catalog import bind_catalog, dimension_defaults
from roometry.vision.errors import AnalysisFailure
from roometry.vision.evidence import photo_content, strict_schema
from roometry.vision.importance import importance
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
    images: list[bytes] | None = None,
) -> ResponseCreateParamsNonStreaming:
    bindings = []
    for obj in analysis.observations.objects:
        models = []
        options = candidates(obj.type, obj.placement) if obj.type is not None else ()
        for asset in options:
            configurations = {}
            for placement in sorted(ASSET_REGISTRY[asset.asset_id].placements):
                if obj.placement is not None and placement != obj.placement:
                    continue
                configurations[placement] = {
                    "default_dimensions_m": dimension_defaults(obj, asset.asset_id, placement).model_dump(),
                    "support_surfaces": [asdict(s) for s in object_surfaces(asset.asset_id, placement)],
                }
            models.append({"asset_id": asset.asset_id, "label": asset.label, "configurations": configurations})
        bindings.append(
            {
                "id": obj.id,
                "models": models,
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
    bound, _ = bind_catalog(analysis, draft) if draft is not None else (None, [])
    normalized = None
    if bound is not None:
        normalized = bound.model_copy(update={
            "objects": anchor_placements(analysis, measurements.dimensions, bound),
        })
    by_id = {obj.id: obj for obj in normalized.objects} if normalized else {}
    attachment_constraints = []
    for obj in by_id.values():
        support = (
            by_id.get(obj.support_object_id)
            if obj.support_object_id is not None
            else None
        )
        if obj.placement == "surface" and support is not None:
            surface = next((s for s in object_surfaces(support.asset_id, support.placement) if s.name == obj.support_surface_id), None)
            if surface is None:
                continue
            area = catalog_surface_bounds(surface, support.dimensions_m, support.position, support.rotation_y_rad)
            attachment_constraints.append(
                {
                    "object_id": obj.id,
                    "support_object_id": support.id,
                    "support_center": {
                        "x": support.position.x,
                        "z": support.position.z,
                    },
                    "support_rotation_y_rad": support.rotation_y_rad,
                    "support_surface_id": surface.name,
                    "support_top_y_m": area.vertical[1],
                    "available_surface_center": {"x": area.footprint.center[0], "z": area.footprint.center[1]},
                    "available_surface_m": {
                        "width": area.footprint.half_extents[0] * 2,
                        "depth": area.footprint.half_extents[1] * 2,
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
        "construction_order": {
            "anchors": [item.id for item in [*analysis.observations.openings, *analysis.observations.objects] if importance(item) == 0],
            "secondary": [item.id for item in analysis.observations.objects if importance(item) == 1],
            "accessories": [item.id for item in analysis.observations.objects if importance(item) > 1],
        },
        # Feedback describes the assembled poses, including server attachments,
        # so repair must see those same coordinates rather than the raw draft.
        "previous_draft": (normalized or draft).model_dump(mode="json") if draft is not None else None,
        "attachment_constraints": attachment_constraints,
        "validation_feedback": [item.model_dump(mode="json") for item in issues],
    }
    content: list[ResponseInputContentParam] = [{"type": "input_text", "text": json.dumps(context, ensure_ascii=True)}]
    if images is not None:
        content.extend(photo_content(analysis.observations.image_ids, images, settings.analysis_max_request_bytes))
    payload: ResponseCreateParamsNonStreaming = {
        "model": settings.model_name,
        "instructions": SCENE_PROPOSAL_INSTRUCTIONS,
        "input": [
            {
                "role": "user",
                "content": content,
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "room_scene_draft",
                "strict": True,
                "schema": strict_schema(SceneDraft),
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
