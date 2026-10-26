"""Pure measurement resolution and authoritative scene assembly."""

from dataclasses import dataclass, field

from pydantic import ValidationError

from roometry.scenes.assets import ASSET_REGISTRY
from roometry.scenes.catalog import ASSET_CATALOG, candidates, dimensions_for
from roometry.scenes.schemas import Opening, RoomDimensions, RoomScene, SceneObject, WallReference
from roometry.scenes.validation import GeometryReport, validate_geometry
from roometry.uploads.schemas import InputDimensions
from roometry.vision.attachments import anchor_placements
from roometry.vision.catalog import bind_catalog
from roometry.vision.corrections import correction_errors
from roometry.vision.fidelity import evaluate_fidelity
from roometry.vision.interpretations import resolve_interpretations
from roometry.vision.appearance import resolve_appearance
from roometry.vision.wall_axes import wall_axis, AXIS_WALLS
from roometry.vision.proposal_schemas import (
    AXES,
    DimensionName,
    ProposalIssue,
    SceneDraft,
)
from roometry.vision.schemas import RoomAnalysisResponse


@dataclass(frozen=True)
class Measurements:
    dimensions: InputDimensions
    to_confirm: list[DimensionName]
    estimated: list[DimensionName]
    assumed: list[DimensionName] = field(default_factory=list)

    @property
    def assumption_notes(self) -> list[str]:
        return [
            f"Assumed room {axis.removesuffix('_m')}: {getattr(self.dimensions, axis):g} m. "
            "The photos do not establish this size; it is a planning starting size, not a photo measurement."
            for axis in self.assumed
        ]


# Explicitly disclosed when the user chooses estimates. Never treated as photo
# evidence, saved user measurements, or permission to override a known axis.
ASSUMED_ROOM_DIMENSIONS = {"width_m": 4., "length_m": 4., "height_m": 2.6}


def resolve_measurements(
    analysis: RoomAnalysisResponse,
    confirmed: dict[DimensionName, float],
    allow_estimates: bool,
    *,
    assume_missing: bool = False,
) -> Measurements:
    original = analysis.input_options.dimensions
    estimates = analysis.observations.estimated_dimensions
    values: dict[str, object] = {}
    trusted: set[DimensionName] = set()
    assumed: list[DimensionName] = []
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
        if value is None and allow_estimates and assume_missing:
            value = ASSUMED_ROOM_DIMENSIONS[axis]
            assumed.append(axis)
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
    return Measurements(InputDimensions.model_validate(values), pending, estimated, assumed)


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
        elif obj.placement is not None and not candidates(obj.type, obj.placement):
            issues.append(
                issue(
                    "unsupported_placement",
                    "No catalog model can represent this observed placement.",
                    obj.id,
                )
            )
    return issues


def notes(*groups: list[str]) -> list[str]:
    unique = list(dict.fromkeys(note for group in groups for note in group))
    if len(unique) > 50:
        return unique[:49] + [
            "Additional uncertainty notes were omitted because the note limit was reached."
        ]
    return unique


def assemble(
    analysis: RoomAnalysisResponse, measurements: Measurements, draft: SceneDraft,
    *, require_fidelity: bool = True, recover_local: bool = False,
) -> tuple[RoomScene | None, GeometryReport | None, list[ProposalIssue]]:
    original_analysis = analysis
    analysis, interpretation_errors = resolve_interpretations(analysis, draft.interpretation_corrections)
    if interpretation_errors:
        return None, None, [issue("placement_mismatch", message, entity_id) for entity_id, message in interpretation_errors][:50]
    analysis, appearance_errors = resolve_appearance(analysis, draft.appearance_corrections)
    if appearance_errors:
        return None, None, [issue("fidelity", message, entity_id) for entity_id, message in appearance_errors][:50]
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
    if analysis.wall_axes is not None:
        for binding in analysis.wall_axes.bindings:
            assigned = wall_map.get(binding.observation_id)
            if assigned is not None and wall_axis(assigned) != binding.axis:
                issues.append(issue("wall_mapping", f"Map {binding.observation_id} to {' or '.join(AXIS_WALLS[binding.axis])}: this wall spans the {getattr(measurements.dimensions, binding.axis):g} m measured side. Preserve room width and length.", binding.observation_id))
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

    # Resolve the measured shell before catalog binding or furniture anchoring.
    # An impossible window must be repaired against its real wall span, rather
    # than forcing furniture around a shell that cannot exist.
    shell_report, shell_issues = validate_shell(analysis, measurements, draft)
    if shell_issues and shell_report is None:
        return None, None, shell_issues

    bound, issues = bind_catalog(analysis, draft)
    if bound is None:
        return None, shell_report if shell_issues else None, (shell_issues + issues)[:50]

    for entity_id, message in correction_errors(analysis, draft):
        issues.append(issue("dimension_mismatch", message, entity_id))
    object_observations = {obj.id: obj for obj in obs.objects}
    opening_observations = {opening.id: opening for opening in obs.openings}
    objects, openings = [], []
    anchored_objects = anchor_placements(analysis, measurements.dimensions, bound)
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
        asset_id = obj.asset_id
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
                "color": source.color or ASSET_CATALOG[asset_id].color,
                "material_colors": source.material_colors,
                "fixed": asset.fixed_by_default
                or source.appears_fixed is True
                or obj.placement in {"wall", "ceiling"},
                "locked": False,
                "provenance": "photo_observed",
                "measurements_source": "catalog"
                if source.estimated_dimensions_m is None
                and (
                    obj.dimensions_m.width,
                    obj.dimensions_m.height,
                    obj.dimensions_m.depth,
                )
                == dimensions_for(asset_id, obj.placement)
                else "estimated",
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
        *measurements.assumption_notes,
        "Positions, orientations, and opening geometry are approximate photo estimates. Object sizes use recorded estimates, supported model estimates, or catalog defaults."
    ]
    if anchored_objects != bound.objects:
        assumptions.append(
            "Estimated placement was resolved geometrically: furniture boundary overshoots and attachment poses may be adjusted by up to 0.25 m, and movable floor decor by up to 0.75 m. Surface items may be aligned with their support's rotation to fit. Object sizes and room measurements were preserved."
        )
    for obj in bound.objects:
        original = next(item for item in draft.objects if item.id == obj.id)
        if original.dimensions_m is None:
            known = object_observations[obj.id].estimated_dimensions_m
            axes = [
                axis
                for axis in ("width", "depth", "height")
                if known is None or getattr(known, axis) is None
            ]
            if axes:
                assumptions.append(
                    f"{obj.id}: unknown size axes use {obj.asset_id} catalog defaults ({', '.join(axes)})."
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
        "Door clearance and sampled walking routes are advisory planning checks. Full headroom, overlapping openings, and room-specific safety are not checked."
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
            finishes=obs.finishes.finishes if obs.finishes else None,
            wall_axis_bindings=analysis.wall_axes.bindings if analysis.wall_axes else [],
            wall_mapping=[WallReference(observation_id=item.observation_id, wall=item.wall) for item in draft.wall_assignments],
            estimate_corrections=draft.estimate_corrections,
            interpretation_corrections=draft.interpretation_corrections,
            appearance_corrections=draft.appearance_corrections,
            objects=parsed_objects,
            openings=parsed_openings,
            assumptions=notes(assumptions, obs.assumptions, draft.assumptions),
            warnings=notes(warnings, obs.warnings, draft.warnings, analysis.wall_axes.warnings if analysis.wall_axes else []),
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
    if recover_local and report.errors:
        from roometry.vision.local_placement import recover_local_placements
        scene, report = recover_local_placements(analysis, scene, report)
    fidelity = evaluate_fidelity(original_analysis, scene, wall_map)
    fidelity_problems = [
        issue("fidelity", check.message, *check.entity_ids)
        for check in fidelity.checks
        if check.required and not check.passed and check.kind != "coverage"
    ]
    geometry_problems = [
        issue("geometry", f"{error.code}: {error.message}", *error.entity_ids)
        for error in report.errors
    ]
    if geometry_problems:
        # The repair needs both classes of feedback in the same attempt. Waiting
        # for every collision to be fixed used to hide relationship errors until
        # the bounded repair allowance had already been consumed.
        fidelity_feedback = fidelity_problems[:16]
        return None, report, geometry_problems[:50 - len(fidelity_feedback)] + fidelity_feedback
    if require_fidelity and fidelity_problems:
        return None, report, fidelity_problems[:50]
    # A provisional preview must still pass every schema and geometry check.
    # Fidelity failures stay explicit; they are not reasons to delete furniture.
    return scene, report, fidelity_problems[:50]


def validate_shell(analysis: RoomAnalysisResponse, measurements: Measurements, draft: SceneDraft) -> tuple[GeometryReport | None, list[ProposalIssue]]:
    """Check openings in an empty measured shell, before furniture is considered."""
    sources = {opening.id: opening for opening in analysis.observations.openings}
    mapping = {item.observation_id: item.wall for item in draft.wall_assignments}
    parsed = []
    problems = []
    for opening in draft.openings:
        source = sources.get(opening.id)
        if source is None:
            continue  # Inventory checks explain invented identities first.
        if source.wall_id is not None and opening.wall != mapping.get(source.wall_id):
            problems.append(issue("wall_mapping", f"Use wall {mapping.get(source.wall_id)} assigned to observation {source.wall_id}.", opening.id))
        try:
            parsed.append(Opening.model_validate({
                **opening.model_dump(), "type": source.type,
                "swing": "unknown" if source.type == "door" else "none",
                "hinge": "unknown" if source.type == "door" else None,
                "provenance": "photo_observed", "measurements_source": "estimated",
            }))
        except ValidationError:
            problems.append(issue("scene_schema", "Check opening dimensions and sill height; doors require sill_height_m=0.", opening.id))
    if problems:
        return None, problems[:50]
    shell = RoomScene(schema_version=1, room=RoomDimensions(
        room_type=analysis.input_options.room_type or analysis.observations.room_type or "other",
        **measurements.dimensions.model_dump(),
    ), openings=parsed, objects=[], assumptions=[], warnings=[])
    report = validate_geometry(shell)
    # Geometry failures still receive furniture/fidelity diagnostics below, so
    # the sole repair sees every known failure together rather than piecemeal.
    return report, [issue("geometry", f"{error.code}: {error.message}", *error.entity_ids)
                    for error in report.errors][:50]
