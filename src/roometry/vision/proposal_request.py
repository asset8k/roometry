"""Photo-grounded structured construction using the bounded Responses transport."""

import json
from typing import Literal
from copy import deepcopy
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
from roometry.vision.interpretations import resolve_interpretations
from roometry.vision.recovery import repair_scope, wall_repair_allowed
from roometry.vision.prompts import SCENE_PROPOSAL_INSTRUCTIONS
from roometry.vision.proposal_schemas import BoundDraft, ProposalIssue, SceneDraft
from roometry.vision.proposals import Measurements
from roometry.vision.schemas import RoomAnalysisResponse
from roometry.vision.wall_axes import AXIS_WALLS
from roometry.vision.context_budget import compact_context, compact_schema, select_photo_ids, photo_references, photo_reference_schema
from roometry.vision.prompts import COMPACT_SCENE_INSTRUCTIONS
from roometry.vision.assembly_context import assembly_features, boundary_feedback, component_feedback, construction_sequence
from roometry.vision.features import required_features, retains_features


def build_proposal_request(
    settings: Settings,
    analysis: RoomAnalysisResponse,
    measurements: Measurements,
    draft: SceneDraft | None,
    issues: list[ProposalIssue],
    images: list[bytes] | None = None,
    *, operation: Literal["scene_proposal", "scene_repair"] = "scene_proposal", mandatory_image_ids=(),
    repair_entity_ids: set[str] | None = None,
) -> ResponseCreateParamsNonStreaming:
    editable = repair_scope(analysis, draft, issues) if operation == "scene_repair" and draft is not None else None
    if repair_entity_ids is not None:
        known = {item.id for item in [*analysis.observations.objects, *analysis.observations.openings]}
        if editable is None or not repair_entity_ids <= known:
            raise ValueError("Targeted repair must use observed identities and an existing draft.")
        editable = repair_entity_ids
    bindings = []
    alternatives = []
    for obj in analysis.observations.objects:
        models = []
        options = candidates(obj.type, obj.placement) if obj.type is not None else ()
        if obj.type == "window_covering" and not any(obj.wall_id is None or opening.wall_id == obj.wall_id for opening in analysis.observations.openings):
            options = [a for a in options if ASSET_REGISTRY[a.asset_id].allows_unlinked_covering]
        options = [a for a in options if retains_features(a, obj, analysis.observations.objects)]
        for asset in options:
            configurations = {}
            for placement in sorted(ASSET_REGISTRY[asset.asset_id].placements):
                if obj.placement is not None and placement != obj.placement:
                    continue
                configurations[placement] = {
                    "default_dimensions_m": dimension_defaults(obj, asset.asset_id, placement).model_dump(),
                    "support_surfaces": [asdict(s) for s in object_surfaces(asset.asset_id, placement)],
                }
            roles = {part.material for part in asset.parts if not part.placements or obj.placement is None or obj.placement in part.placements}
            fields = roles & {"wood", "metal", "linen", "inset", "rubber", "enamel"}
            if roles & {"primary", "primary_dark", "primary_metal", "primary_ceramic"}:
                fields.add("color")
            features = assembly_features(asset)
            models.append({"asset_id": asset.asset_id, "label": asset.label, "configurations": configurations,
                           **({"allows_unlinked_wall_drape": True} if ASSET_REGISTRY[asset.asset_id].allows_unlinked_covering else {}),
                           **({"included_components": features} if features else {}),
                           "appearance_fields": sorted(fields),
                           "appearance_regions": {field: [p.name for p in asset.parts if
                               (p.material.startswith("primary") if field == "color" else p.material == field)
                               and (not p.placements or obj.placement is None or obj.placement in p.placements)][:4]
                               for field in sorted(fields)}})
        bindings.append(
            {
                "id": obj.id,
                "models": models,
                "required_components": sorted(required_features(obj, analysis.observations.objects)),
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
        # Keep ordinary bindings exact, and explicitly separate uncertain
        # interpretations. Using an alternative requires an audited correction.
        types = [obj.type] if obj.type is not None else []
        if obj.confidence != "high" and obj.alternative_type is not None:
            types.append(obj.alternative_type)
        for object_type in dict.fromkeys(types):
            placement = obj.placement if obj.placement_confidence == "high" else None
            for asset in candidates(object_type, placement):
                for configuration in sorted(ASSET_REGISTRY[asset.asset_id].placements):
                    if placement is not None and placement != configuration:
                        continue
                    if object_type == obj.type and (obj.placement is None or configuration == obj.placement):
                        continue
                    alternatives.append({
                        "id": obj.id, "type": object_type, "placement": configuration,
                        "asset_id": asset.asset_id, "label": asset.label,
                        "default_dimensions_m": dimension_defaults(obj, asset.asset_id, configuration).model_dump(),
                        "support_surfaces": [asdict(s) for s in object_surfaces(asset.asset_id, configuration)],
                        "requires_interpretation_correction": True,
                    })
    effective, _ = resolve_interpretations(analysis, draft.interpretation_corrections) if draft is not None else (analysis, [])
    bound, _ = bind_catalog(effective, draft) if draft is not None else (None, [])
    if bound is None and draft is not None:
        # Feedback for valid furniture must survive an unrelated unsupported
        # accessory. These isolated bindings never bypass full scene assembly.
        entries = []
        for obj in draft.objects:
            single, _ = bind_catalog(effective, draft.model_copy(update={"objects": [obj]}))
            if single is not None:
                entries.extend(single.objects)
        bound = BoundDraft.model_validate({**draft.model_dump(), "objects": [o.model_dump() for o in entries]})
    normalized = None
    if bound is not None:
        normalized = bound.model_copy(update={
            "objects": anchor_placements(effective, measurements.dimensions, bound),
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
        "assumed_axes": measurements.assumed,
        "dimension_assumptions": measurements.assumption_notes,
        "dimension_estimate_evidence": measurements.estimate_notes,
        "wall_axes": analysis.wall_axes.model_dump(mode="json") if analysis.wall_axes else None,
        "asset_bindings": bindings,
        "hypothesis_alternatives": alternatives,
        "room_shell": {
            "width_m": measurements.dimensions.width_m,
            "length_m": measurements.dimensions.length_m,
            "height_m": measurements.dimensions.height_m,
            "walls": {wall: {"span_m": getattr(measurements.dimensions, axis), "offset_axis": offset_axis}
                      for wall, axis, offset_axis in (("north", "width_m", "x"), ("south", "width_m", "x"), ("west", "length_m", "z"), ("east", "length_m", "z"))},
            "opening_rules": "offset_m + width_m <= wall span; sill_height_m + height_m <= room height; doors have sill_height_m=0",
        },
        "construction_order": {
            "object_sequence": construction_sequence(analysis),
            "anchors": [item.id for item in [*analysis.observations.openings, *analysis.observations.objects] if importance(item) == 0],
            "secondary": [item.id for item in analysis.observations.objects if importance(item) == 1],
            "accessories": [item.id for item in analysis.observations.objects if importance(item) > 1],
        },
        # Feedback describes the assembled poses, including server attachments,
        # so repair must see those same coordinates rather than the raw draft.
        "previous_draft": (normalized or draft).model_dump(mode="json") if draft is not None else None,
        "raw_previous_draft": draft.model_dump(mode="json") if draft is not None else None,
        "repair": {
            "mode": "upsert", "deletions_allowed": False,
            "editable_entity_ids": sorted(editable),
            "missing_entity_ids": [item.id for item in [*analysis.observations.objects, *analysis.observations.openings]
                                   if item.id not in {entry.id for entry in [*draft.objects, *draft.openings]}
                                   and item.id in editable],
            "instruction": "Return only changed or restored observed entries. Omitted entries remain unchanged. Keep unaffected entries byte-for-byte if repeated. raw_previous_draft is the merge base; previous_draft shows attachment-adjusted coordinates used by geometry checks.",
        } if operation == "scene_repair" and draft is not None else None,
        "attachment_constraints": attachment_constraints,
        "component_conflicts": component_feedback(normalized.objects, issues) if normalized else [],
        "boundary_repairs": boundary_feedback(normalized.objects, measurements.dimensions) if normalized else [],
        "validation_feedback": [item.model_dump(mode="json") for item in issues],
    }
    schema = strict_schema(SceneDraft)
    # Strict Structured Outputs follows property order. Resolve shell first in
    # the single construction response; this does not add another Sol request.
    order = ("wall_assignments", "openings", "interpretation_corrections", "objects", "estimate_corrections", "appearance_corrections", "assumptions", "warnings")
    schema["properties"] = {key: schema["properties"][key] for key in order}
    schema["required"] = list(order)
    # Constrain each observed wall's axis at decoding time. Uniqueness and
    # topology still receive deterministic checks after decoding.
    if analysis.observations.walls:
        by_id = {b.observation_id: b for b in analysis.wall_axes.bindings} if analysis.wall_axes else {}
        variants = []
        for observed_wall in analysis.observations.walls:
            variant = deepcopy(schema["$defs"]["WallAssignment"])
            variant["properties"]["observation_id"]["enum"] = [observed_wall.id]
            if observed_wall.id in by_id:
                variant["properties"]["wall"]["enum"] = list(AXIS_WALLS[by_id[observed_wall.id].axis])
            variants.append(variant)
        schema["properties"]["wall_assignments"].update(items={"anyOf": variants}, minItems=0 if operation == "scene_repair" and draft is not None else len(variants), maxItems=len(variants))
    else:
        schema["properties"]["wall_assignments"]["maxItems"] = 0
    if editable is not None and not wall_repair_allowed(analysis, issues):
        schema["properties"]["wall_assignments"]["maxItems"] = 0
    object_ids = [obj.id for obj in analysis.observations.objects if editable is None or obj.id in editable]
    represented = {obj.id for obj in draft.objects} if draft is not None else set()
    failed_ids = {key for problem in issues for key in problem.entity_ids}
    major = {obj.id for obj in analysis.observations.objects if importance(obj) <= 1}
    required_objects = [binding["id"] for binding in bindings if binding["models"]
                        and (binding["id"] not in represented or binding["id"] in failed_ids & major)
                        and (editable is None or binding["id"] in editable)]
    context["required_object_ids"] = required_objects
    if context["repair"] is not None:
        context["repair"]["required_repair_entity_ids"] = required_objects
    context["inventory_rule"] = "Return each required_object_id exactly once. Preserve its observed components and supports; estimate uncertain poses within the confirmed shell. Unsupported observations remain explicit unresolved items."
    # A repair returning [] used to consume the only recovery call while
    # silently restoring none of the missing, representable fixtures.
    schema["properties"]["objects"]["minItems"] = len(required_objects)
    for axis, limit in (("x", measurements.dimensions.width_m), ("y", measurements.dimensions.height_m), ("z", measurements.dimensions.length_m)):
        schema["$defs"]["Position"]["properties"][axis].update(minimum=0, maximum=limit)
    schema["properties"]["objects"]["maxItems"] = len(object_ids)
    if object_ids:
        schema["$defs"]["DraftObject"]["properties"]["id"]["enum"] = object_ids
    else:
        schema["properties"]["objects"]["maxItems"] = 0
    if analysis.observations.openings:
        axes = {binding.observation_id: binding.axis for binding in analysis.wall_axes.bindings} if analysis.wall_axes else {}
        opening_variants = []
        for opening in analysis.observations.openings:
            if editable is not None and opening.id not in editable:
                continue
            variant = deepcopy(schema["$defs"]["DraftOpening"])
            props = variant["properties"]
            props["id"]["enum"] = [opening.id]
            if opening.wall_id in axes:
                axis = axes[opening.wall_id]
                span = getattr(measurements.dimensions, axis)
                props["wall"]["enum"] = list(AXIS_WALLS[axis])
                props["width_m"]["maximum"] = span
                props["offset_m"]["maximum"] = span
            props["height_m"]["maximum"] = measurements.dimensions.height_m
            props["sill_height_m"]["maximum"] = measurements.dimensions.height_m
            if opening.type == "door":
                props["sill_height_m"]["enum"] = [0]
            opening_variants.append(variant)
        if opening_variants:
            schema["properties"]["openings"]["items"] = {"anyOf": opening_variants}
        else:
            schema["properties"]["openings"]["maxItems"] = 0
    else:
        schema["properties"]["openings"]["maxItems"] = 0
    if settings.scene_compact_context:
        context = compact_context(context, analysis, editable=editable)
        schema = compact_schema(schema, context)
    selected_ids = analysis.observations.image_ids
    if images is not None and settings.scene_compact_context:
        selected_ids = select_photo_ids(analysis.observations, settings.scene_photo_target, mandatory_image_ids)
        context['photo_selection'] = {'attached_image_ids': selected_ids,
            'analyzed_image_count': len(analysis.observations.image_ids),
            'coverage': 'Every recorded entity, relation, wall span and finish has an attached source view; additional views are retained when needed.'}
    if settings.scene_compact_context:
        context = photo_references(context, analysis.observations.image_ids)
        schema = photo_reference_schema(schema, analysis.observations.image_ids)
        context['photo_reference_format'] = 'Use photo_N in source_image_ids; the server restores original upload IDs. Object and wall IDs are unchanged.'
    content: list[ResponseInputContentParam] = [{"type": "input_text", "text": json.dumps(context, ensure_ascii=True, separators=(',', ':'))}]
    if images is not None:
        if len(images) != len(analysis.observations.image_ids):
            raise AnalysisFailure(503, "storage_unavailable", "The stored photos are unavailable. Upload them again.")
        by_id = dict(zip(analysis.observations.image_ids, images))
        names = [f'photo_{analysis.observations.image_ids.index(key)+1}' for key in selected_ids] if settings.scene_compact_context else selected_ids
        content.extend(photo_content(names, [by_id[key] for key in selected_ids], settings.analysis_max_request_bytes))
    payload: ResponseCreateParamsNonStreaming = {
        "model": settings.model_route(operation).model,
        "instructions": COMPACT_SCENE_INSTRUCTIONS if settings.scene_compact_context else SCENE_PROPOSAL_INSTRUCTIONS,
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
                "schema": schema,
            }
        },
        "max_output_tokens": settings.scene_max_output_tokens,
        "store": False,
        "tools": [],
        "truncation": "disabled",
    }
    effort = settings.model_route(operation).reasoning_effort
    if effort != "default":
        payload["reasoning"] = {"effort": effort}
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
