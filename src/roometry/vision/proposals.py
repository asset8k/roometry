"""Pure measurement resolution and authoritative scene assembly."""

from dataclasses import dataclass
from math import isclose

from pydantic import ValidationError

from roometry.scenes.assets import ASSET_REGISTRY
from roometry.scenes.geometry import WALL_ROTATIONS_RAD
from roometry.scenes.schemas import Opening, RoomDimensions, RoomScene, SceneObject
from roometry.scenes.validation import GeometryReport, validate_geometry
from roometry.uploads.schemas import InputDimensions
from roometry.vision.proposal_schemas import (
    AXES,
    DimensionName,
    DraftObject,
    ProposalIssue,
    SceneDraft,
)
from roometry.vision.schemas import RoomAnalysisResponse


@dataclass(frozen=True)
class Measurements:
    dimensions: InputDimensions
    to_confirm: list[DimensionName]
    estimated: list[DimensionName]


def resolve_measurements(
    analysis: RoomAnalysisResponse,
    confirmed: dict[DimensionName, float],
    allow_estimates: bool,
) -> Measurements:
    original = analysis.input_options.dimensions
    estimates = analysis.observations.estimated_dimensions
    values: dict[str, object] = {}
    trusted: set[DimensionName] = set()
    for axis in AXES:
        value = getattr(original, axis, None)
        if (
            value is not None
            and original is not None
            and original.measurements_source == "user_confirmed"
        ):
            trusted.add(axis)
        if axis in confirmed:
            value = confirmed[axis]
            trusted.add(axis)
        if value is None:
            value = getattr(estimates, axis, None)
        values[axis] = value
    estimated: list[DimensionName] = [
        axis for axis in AXES if values[axis] is not None and axis not in trusted
    ]
    pending: list[DimensionName] = [
        axis
        for axis in AXES
        if values[axis] is None or (axis not in trusted and not allow_estimates)
    ]
    values["measurements_source"] = (
        "estimated" if estimated or not trusted else "user_confirmed"
    )
    return Measurements(InputDimensions.model_validate(values), pending, estimated)


def issue(code: str, message: str, *ids: str) -> ProposalIssue:
    return ProposalIssue.model_validate(
        {"code": code, "message": message, "entity_ids": list(ids)}
    )


def preflight(analysis: RoomAnalysisResponse) -> list[ProposalIssue]:
    obs = analysis.observations
    issues = []
    if obs.room_shape == "non_rectangular":
        issues.append(
            issue(
                "unsupported_room_shape",
                "The current scene contract requires a rectangular room; confirm a rectangular area before continuing.",
            )
        )
    if not (obs.objects or obs.openings or obs.walls or obs.floor_visible is True):
        issues.append(
            issue(
                "insufficient_evidence",
                "The photos do not establish a room layout. Add clearer room photos.",
            )
        )
    for obj in obs.objects:
        if obj.type is None:
            issues.append(
                issue(
                    "unknown_object",
                    "An observed object needs an approved category before reconstruction.",
                    obj.id,
                )
            )
        elif (
            obj.placement is not None
            and obj.placement
            not in ASSET_REGISTRY[f"{obj.type}_standard_01"].placements
        ):
            issues.append(
                issue(
                    "unsupported_placement",
                    "The standard asset cannot represent this observed placement.",
                    obj.id,
                )
            )
    return issues[:50]


def notes(*groups: list[str]) -> list[str]:
    unique = list(dict.fromkeys(note for group in groups for note in group))
    if len(unique) > 50:
        return unique[:49] + [
            "Additional uncertainty notes were omitted because the note limit was reached."
        ]
    return unique


def _anchor_placements(
    analysis: RoomAnalysisResponse, measurements: Measurements, draft: SceneDraft
) -> list[DraftObject]:
    """Derive attachment coordinates; leave free layout coordinates for validation.

    These values follow the declared placement, not a model's approximate math.
    Do not move objects sideways to hide collisions or change their dimensions.
    """
    objects = [obj.model_copy(deep=True) for obj in draft.objects]
    room = measurements.dimensions
    for obj in objects:
        if obj.placement == "floor":
            obj.position.y = 0.0
        elif obj.placement == "ceiling" and room.height_m is not None:
            obj.position.y = room.height_m - obj.dimensions_m.height
        elif obj.placement == "wall" and obj.wall is not None:
            obj.rotation_y_rad = WALL_ROTATIONS_RAD[obj.wall]
            depth = obj.dimensions_m.depth / 2
            if obj.wall == "north":
                obj.position.z = depth
            elif obj.wall == "south" and room.length_m is not None:
                obj.position.z = room.length_m - depth
            elif obj.wall == "west":
                obj.position.x = depth
            elif obj.wall == "east" and room.width_m is not None:
                obj.position.x = room.width_m - depth
    by_id = {obj.id: obj for obj in objects}
    observations = {obj.id: obj for obj in analysis.observations.objects}
    for obj in objects:
        if obj.placement != "surface" or obj.support_object_id is None:
            continue
        support = by_id.get(obj.support_object_id)
        source = observations.get(obj.support_object_id)
        asset = ASSET_REGISTRY.get(f"{source.type}_standard_01") if source else None
        if (
            support is not None
            and support.placement in {"floor", "wall"}
            and asset is not None
            and asset.supplies_surface
        ):
            obj.position.y = support.position.y + support.dimensions_m.height
    return objects


def assemble(
    analysis: RoomAnalysisResponse, measurements: Measurements, draft: SceneDraft
) -> tuple[RoomScene | None, GeometryReport | None, list[ProposalIssue]]:
    obs = analysis.observations
    issues: list[ProposalIssue] = []
    expected_walls = {wall.id for wall in obs.walls}
    wall_map = {item.observation_id: item.wall for item in draft.wall_assignments}
    if (
        set(wall_map) != expected_walls
        or len(wall_map) != len(draft.wall_assignments)
        or len(set(wall_map.values())) != len(wall_map)
    ):
        issues.append(
            issue(
                "wall_mapping",
                "Map every observed wall exactly once to a distinct scene wall.",
            )
        )
    for candidates, observed in (
        (draft.objects, obs.objects),
        (draft.openings, obs.openings),
    ):
        if {item.id for item in candidates} != {item.id for item in observed} or len(
            candidates
        ) != len(observed):
            issues.append(
                issue(
                    "identity_mismatch",
                    "Preserve every observation ID exactly once; do not add, delete, or merge entities.",
                )
            )
    if issues:
        return None, None, issues

    object_observations = {obj.id: obj for obj in obs.objects}
    opening_observations = {opening.id: opening for opening in obs.openings}
    objects, openings = [], []
    anchored_objects = _anchor_placements(analysis, measurements, draft)
    for obj in anchored_objects:
        source = object_observations[obj.id]
        if source.placement is not None and obj.placement != source.placement:
            issues.append(
                issue(
                    "placement_mismatch",
                    f"Preserve the observed placement: {source.placement}.",
                    obj.id,
                )
            )
        if (
            source.support_object_id is not None
            and obj.support_object_id != source.support_object_id
        ):
            issues.append(
                issue(
                    "support_mismatch",
                    f"Preserve the observed support_object_id: {source.support_object_id}.",
                    obj.id,
                )
            )
        if (
            obj.placement == "wall"
            and source.wall_id is not None
            and obj.wall != wall_map.get(source.wall_id)
        ):
            issues.append(
                issue(
                    "wall_mapping",
                    f"Use wall {wall_map[source.wall_id]} assigned to observation {source.wall_id}.",
                    obj.id,
                )
            )
        if source.estimated_dimensions_m is not None:
            for axis in ("width", "depth", "height"):
                known = getattr(source.estimated_dimensions_m, axis)
                if known is not None and not isclose(
                    getattr(obj.dimensions_m, axis), known, abs_tol=1e-6, rel_tol=0
                ):
                    issues.append(
                        issue(
                            "dimension_mismatch",
                            "Preserve recorded object estimates; do not shrink objects to pass geometry checks.",
                            obj.id,
                        )
                    )
        asset_id = f"{source.type}_standard_01"
        asset = ASSET_REGISTRY[asset_id]
        if obj.placement not in asset.placements:
            issues.append(
                issue(
                    "unsupported_placement",
                    "This asset requires placement: "
                    + ", ".join(sorted(asset.placements))
                    + ".",
                    obj.id,
                )
            )
        objects.append(
            {
                **obj.model_dump(),
                "type": source.type,
                "asset_id": asset_id,
                "color": source.color or "#808080",
                "fixed": asset.fixed_by_default
                or source.appears_fixed is True
                or obj.placement in {"wall", "ceiling"},
                "locked": False,
                "provenance": "photo_observed",
                "measurements_source": "estimated",
            }
        )
    for opening in draft.openings:
        source = opening_observations[opening.id]
        if source.wall_id is not None and opening.wall != wall_map.get(source.wall_id):
            issues.append(
                issue(
                    "wall_mapping",
                    f"Use wall {wall_map[source.wall_id]} assigned to observation {source.wall_id}.",
                    opening.id,
                )
            )
        for field in ("width_m", "height_m", "sill_height_m"):
            known = getattr(source, f"estimated_{field}")
            if known is not None and not isclose(
                getattr(opening, field), known, abs_tol=1e-6, rel_tol=0
            ):
                issues.append(
                    issue(
                        "dimension_mismatch",
                        "Preserve recorded opening estimates.",
                        opening.id,
                    )
                )
        openings.append(
            {
                **opening.model_dump(),
                "type": source.type,
                "swing": "unknown" if source.type == "door" else "none",
                "hinge": "unknown" if source.type == "door" else None,
                "provenance": "photo_observed",
                "measurements_source": "estimated",
            }
        )
    if issues:
        return None, None, issues[:50]
    assumptions = [
        "Positions, orientations, object sizes, and opening geometry are approximate photo estimates."
    ]
    if anchored_objects != draft.objects:
        assumptions.append(
            "Floor, ceiling, wall, and support attachment coordinates were derived from their declared relationships."
        )
    if measurements.estimated:
        assumptions.append(
            "Unconfirmed room dimensions: " + ", ".join(measurements.estimated) + "."
        )
    if obs.room_shape == "unknown":
        assumptions.append(
            "A rectangular room is assumed; the photos do not establish its shape."
        )
    warnings = [
        "Baseline geometry does not check door swings, walking clearance, headroom, overlapping openings, or room-specific safety."
    ]
    if obs.possible_duplicates:
        warnings.append(
            "Possible duplicate observations remain separate objects; their identities need human review."
        )
    warnings.extend(obs.unknowns)
    parsed_objects = []
    for raw in objects:
        try:
            parsed_objects.append(SceneObject.model_validate(raw))
        except ValidationError:
            issues.append(
                issue(
                    "support_mismatch"
                    if raw["placement"] == "surface"
                    and raw["support_object_id"] is None
                    else "scene_schema",
                    "This covering requires opening_id naming an observed window or door."
                    if raw["type"] == "window_covering" and raw["opening_id"] is None
                    else "Check this object's asset placement, required wall/opening reference, and support_object_id. Surface objects require an observed supporting object.",
                    raw["id"],
                )
            )
    if issues:
        return None, None, issues[:50]
    by_id = {obj.id: obj for obj in parsed_objects}
    for obj in parsed_objects:
        if obj.placement == "surface" and obj.support_object_id is not None:
            support = by_id.get(obj.support_object_id)
            if (
                support is None
                or support.placement not in {"floor", "wall"}
                or not ASSET_REGISTRY[support.asset_id].supplies_surface
            ):
                issues.append(
                    issue(
                        "scene_schema",
                        "Surface objects must reference an observed floor or wall object that supplies an approved supporting surface; windows and other surface objects cannot supply support.",
                        obj.id,
                    )
                )
    if issues:
        return None, None, issues[:50]
    parsed_openings = []
    for raw in openings:
        try:
            parsed_openings.append(Opening.model_validate(raw))
        except ValidationError:
            issues.append(
                issue(
                    "scene_schema",
                    "Check this opening's dimensions and sill height. Doors require sill_height_m=0.",
                    raw["id"],
                )
            )
    if issues:
        return None, None, issues[:50]
    opening_by_id = {opening.id: opening for opening in parsed_openings}
    for obj in parsed_objects:
        if obj.type != "window_covering":
            continue
        linked = (
            opening_by_id.get(obj.opening_id) if obj.opening_id is not None else None
        )
        if linked is None:
            issues.append(
                issue(
                    "scene_schema",
                    "This covering must reference an observed window or door using opening_id.",
                    obj.id,
                )
            )
        elif obj.wall != linked.wall:
            issues.append(
                issue(
                    "wall_mapping",
                    f"This covering must use the linked opening's wall: {linked.wall}.",
                    obj.id,
                    linked.id,
                )
            )
    if issues:
        return None, None, issues[:50]
    try:
        scene = RoomScene(
            schema_version=1,
            room=RoomDimensions(
                room_type=analysis.input_options.room_type or obs.room_type or "other",
                **measurements.dimensions.model_dump(),
            ),
            objects=parsed_objects,
            openings=parsed_openings,
            assumptions=notes(assumptions, obs.assumptions, draft.assumptions),
            warnings=notes(warnings, obs.warnings, draft.warnings),
        )
    except ValidationError:
        # Never echo arbitrary model text or raw Pydantic inputs in feedback.
        return (
            None,
            None,
            [
                issue(
                    "scene_schema",
                    "The assembled scene violates asset, placement, opening, or support constraints.",
                )
            ],
        )
    report = validate_geometry(scene)
    if not report.valid:
        return (
            None,
            report,
            [
                issue("geometry", f"{error.code}: {error.message}", *error.entity_ids)
                for error in report.errors[:50]
            ],
        )
    return scene, report, []
