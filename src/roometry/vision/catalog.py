"""Bind untrusted scene choices to approved models before anchoring their poses."""

from math import cos, sin
from typing import Literal

from roometry.scenes.assets import Placement
from roometry.scenes.catalog import (
    ASSET_CATALOG,
    candidates,
    dimensions_for,
    object_surfaces,
)
from roometry.scenes.collision import contact_bounds
from roometry.scenes.furniture import SupportSurface
from roometry.scenes.schemas import ObjectDimensions
from roometry.vision.proposal_schemas import (
    BoundDraft,
    BoundObject,
    ProposalIssue,
    SceneDraft,
)
from roometry.vision.schemas import ObjectObservation, RoomAnalysisResponse
from roometry.vision.context_budget import shortlist
from roometry.vision.features import required_features, retains_features

BindingIssue = Literal[
    "identity_mismatch",
    "asset_mismatch",
    "unsupported_placement",
    "scene_schema",
    "support_mismatch",
]


def dimension_defaults(
    observation: ObjectObservation, asset_id: str, placement: Placement
) -> ObjectDimensions:
    """Catalog sizes fill unknown axes; recorded estimates retain precedence."""
    w, h, d = dimensions_for(asset_id, placement)
    sizes = {"width": w, "height": h, "depth": d}
    if observation.estimated_dimensions_m is not None:
        sizes.update(observation.estimated_dimensions_m.model_dump(exclude_none=True))
    return ObjectDimensions.model_validate(sizes)


def bind_catalog(
    analysis: RoomAnalysisResponse, draft: SceneDraft
) -> tuple[BoundDraft | None, list[ProposalIssue]]:
    observations = {obj.id: obj for obj in analysis.observations.objects}
    objects: list[BoundObject] = []
    issues: list[ProposalIssue] = []

    def fail(code: BindingIssue, message: str, obj_id: str) -> None:
        issues.append(ProposalIssue(code=code, message=message, entity_ids=[obj_id]))

    for obj in draft.objects:
        source = observations.get(obj.id)
        if source is None or source.type is None:
            fail(
                "identity_mismatch",
                "Use an existing classified observation ID.",
                obj.id,
            )
            continue
        options = candidates(source.type, obj.placement)
        required = required_features(source, analysis.observations.objects)
        options = [a for a in options if retains_features(a, source, analysis.observations.objects)]
        if not options:
            fail(
                "unsupported_placement",
                (f"Choose a compatible model retaining the observed components: {', '.join(sorted(required))}."
                 if required else "No model of this type supports the requested placement."),
                obj.id,
            )
            continue
        if obj.asset_id is None:
            # A support must actually offer object areas. Some legacy standard
            # fixtures contain a visible top but deliberately declare no support.
            children = [item for item in draft.objects if item.support_object_id == obj.id]
            supporting = [a for a in options if object_surfaces(a.asset_id, obj.placement)]
            eligible = supporting if children and supporting else options
            ranked = shortlist([
                {"asset_id": a.asset_id, "label": a.label,
                 "configurations": {obj.placement: {}}} for a in eligible
            ], source)
            asset = ASSET_CATALOG[ranked[0]["asset_id"]]
        else:
            asset = ASSET_CATALOG.get(obj.asset_id)
        if asset is None or asset.object_type != source.type:
            fail(
                "asset_mismatch",
                "Choose an approved model matching this observation's type and placement.",
                obj.id,
            )
            continue
        if not retains_features(asset, source, analysis.observations.objects):
            fail("asset_mismatch", f"The selected model drops observed components: {', '.join(sorted(required))}. Choose a fitted assembly that includes them or retain their separately observed fixture IDs.", obj.id)
            continue
        if asset not in options:
            fail(
                "unsupported_placement",
                "The selected model cannot represent this placement.",
                obj.id,
            )
            continue
        if obj.support_surface_id is not None and obj.placement != "surface":
            fail(
                "scene_schema",
                "Only surface items can select a supporting surface.",
                obj.id,
            )
            continue
        if obj.dimensions_m is None:
            dimensions = dimension_defaults(source, asset.asset_id, obj.placement)
        else:
            dimensions = obj.dimensions_m
        objects.append(
            BoundObject.model_validate(
                {
                    **obj.model_dump(),
                    "asset_id": asset.asset_id,
                    "dimensions_m": dimensions.model_dump(),
                }
            )
        )
    if issues:
        return None, issues[:50]

    by_id = {obj.id: obj for obj in objects}
    for obj in objects:
        if obj.placement != "surface" or obj.support_object_id is None:
            continue
        support = by_id.get(obj.support_object_id)
        if support is None:
            continue  # The scene's existing reference checks explain this failure.
        surfaces = object_surfaces(support.asset_id, support.placement)
        if obj.support_surface_id is not None:
            if not any(s.name == obj.support_surface_id for s in surfaces):
                fail(
                    "support_mismatch",
                    "Choose an object surface declared by the selected support model.",
                    obj.id,
                )
        elif surfaces:
            c, s = cos(support.rotation_y_rad), sin(support.rotation_y_rad)
            dx, dz = (
                obj.position.x - support.position.x,
                obj.position.z - support.position.z,
            )
            x, z = c * dx - s * dz, s * dx + c * dz
            host_size = support.dimensions_m
            base = contact_bounds(obj)
            if base is None:
                continue
            base_width, base_depth = (2 * half for half in base.footprint.half_extents)
            relative = obj.rotation_y_rad - support.rotation_y_rad
            c, s = abs(cos(relative)), abs(sin(relative))
            child_width = c * base_width + s * base_depth
            child_depth = s * base_width + c * base_depth
            # A desk's work surface stays below an integrated hutch. Default
            # tabletop devices to that surface, not the highest shelf board.
            worktops = [area for area in surfaces if area.part == "worktop"] if ASSET_CATALOG[support.asset_id].object_type == "desk" else []

            def fits(area: SupportSurface) -> bool:
                width = (area.x_max - area.x_min) * host_size.width
                depth = (area.z_max - area.z_min) * host_size.depth
                return (child_width <= width and child_depth <= depth) or (
                    base_width <= width and base_depth <= depth
                )

            # Default to the uppermost usable area; tie-break by proximity, then ID.
            def rank(area: SupportSurface) -> tuple[float, float, str]:
                center_x = (area.x_min + area.x_max) / 2 * host_size.width
                center_z = (area.z_min + area.z_max) / 2 * host_size.depth
                return -area.y, (x - center_x) ** 2 + (z - center_z) ** 2, area.name

            # Never choose an undersized top ledge while a lower shelf fits.
            # Keep desks on their worktop rather than their integrated hutch.
            usable = [area for area in surfaces if fits(area)]
            chosen = min([area for area in worktops if fits(area)] or usable or worktops or surfaces, key=rank)
            obj.support_surface_id = chosen.name
    if issues:
        return None, issues[:50]
    return BoundDraft.model_validate(
        {
            **draft.model_dump(exclude={"objects"}),
            "objects": [obj.model_dump() for obj in objects],
        }
    ), []
