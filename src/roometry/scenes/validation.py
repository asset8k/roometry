"""Baseline spatial checks, separate from schemas and scene-edit authorization."""

from itertools import combinations
from typing import Literal

from pydantic import BaseModel, ConfigDict, computed_field

from roometry.scenes.assets import ASSET_REGISTRY
from roometry.scenes.geometry import (
    ROTATION_TOLERANCE_RAD,
    TOLERANCE_M,
    bounds_inside_room,
    boxes_overlap,
    object_bounds,
    opening_inside_wall,
    rectangles_overlap,
    surface_is_supported,
    wall_is_attached,
    wall_object_overlaps_opening,
)
from roometry.scenes.schemas import RoomScene

GeometryRule = Literal[
    "object_outside_room", "object_overlap", "rug_overlap", "surface_support",
    "wall_attachment", "ceiling_attachment", "opening_outside_wall",
    "wall_opening_overlap", "baseline_scope",
]


class GeometryIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: GeometryRule
    entity_ids: tuple[str, ...]
    message: str


class GeometryReport(BaseModel):
    """`valid` means these baseline checks passed, not full scene acceptance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: Literal["baseline-v1"] = "baseline-v1"
    tolerance_m: float = TOLERANCE_M
    rotation_tolerance_rad: float = ROTATION_TOLERANCE_RAD
    errors: tuple[GeometryIssue, ...]
    warnings: tuple[GeometryIssue, ...]

    @computed_field
    @property
    def valid(self) -> bool:
        return not self.errors


def validate_geometry(scene: RoomScene) -> GeometryReport:
    """Revalidate structure, then report geometry failures without mutating input.

    Invalid structure raises Pydantic ValidationError, rather than masquerading
    as a spatial failure. No trusted previous scene or ownership is established
    here. Fixed/locked preservation belongs to the later mutation layer.
    """
    scene = RoomScene.model_validate(scene)
    objects = sorted(scene.objects, key=lambda obj: obj.id)
    openings = sorted(scene.openings, key=lambda opening: opening.id)
    bounds = {obj.id: object_bounds(obj) for obj in objects}
    errors: list[GeometryIssue] = []

    def fail(code: GeometryRule, ids: tuple[str, ...], message: str) -> None:
        errors.append(GeometryIssue(code=code, entity_ids=ids, message=message))

    for opening in openings:
        if not opening_inside_wall(opening, scene.room):
            fail(
                "opening_outside_wall", (opening.id,),
                "Opening extends beyond its wall's width or height.",
            )

    for obj in objects:
        box = bounds[obj.id]
        if not bounds_inside_room(box, scene.room):
            fail(
                "object_outside_room", (obj.id,),
                "Object's rotated bounds extend outside the room.",
            )

        if obj.placement == "surface":
            # Structure has already guaranteed a non-null, eligible support ID.
            support_id = obj.support_object_id
            if support_id is None:
                raise ValueError("Surface object requires a support_object_id")
            if not surface_is_supported(box, bounds[support_id]):
                fail(
                    "surface_support", (obj.id, support_id),
                    "Object must touch its support's top and fit within its rotated footprint.",
                )
        elif obj.placement == "ceiling":
            if abs(box.vertical[1] - scene.room.height_m) > TOLERANCE_M:
                fail(
                    "ceiling_attachment", (obj.id,), "Object's top must touch the ceiling.",
                )
        elif obj.placement == "wall":
            if not wall_is_attached(obj, box, scene.room):
                fail(
                    "wall_attachment", (obj.id,),
                    "Object must face inward with its entire back edge touching its host wall.",
                )
                continue
            asset = ASSET_REGISTRY[obj.asset_id]
            for opening in openings:
                if opening.wall != obj.wall:
                    continue
                # Exception comes from trusted asset metadata and the validated
                # window link, never from a user-supplied collision flag.
                if asset.object_type == "window_covering" and obj.opening_id == opening.id:
                    continue
                if wall_object_overlaps_opening(box, opening):
                    fail(
                        "wall_opening_overlap", (obj.id, opening.id),
                        "Wall-mounted object overlaps a door or unrelated window.",
                    )

    for left, right in combinations(objects, 2):
        left_asset = ASSET_REGISTRY[left.asset_id]
        right_asset = ASSET_REGISTRY[right.asset_id]
        if left_asset.object_type == "rug" or right_asset.object_type == "rug":
            # Rugs are decorative layers, but overlapping rug footprints are
            # forbidden even when their small vertical intervals only touch.
            if left_asset.object_type == right_asset.object_type == "rug":
                if rectangles_overlap(bounds[left.id].footprint, bounds[right.id].footprint):
                    fail(
                        "rug_overlap", (left.id, right.id),
                        "Rug footprints must not overlap each other.",
                    )
            continue
        if boxes_overlap(bounds[left.id], bounds[right.id]):
            fail(
                "object_overlap", (left.id, right.id), "Objects' solid bounding boxes overlap.",
            )

    return GeometryReport(
        errors=tuple(errors),
        warnings=(
            GeometryIssue(
                code="baseline_scope",
                entity_ids=(),
                message=(
                    "Checks use conservative solid bounding boxes. Door swings, walking/headroom "
                    "clearance, overlapping openings, room-specific rules, budgets, and preservation "
                    "of authorized previous state are not checked. A passing baseline is not full acceptance."
                ),
            ),
        ),
    )
