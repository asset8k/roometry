"""Explainable soft goal proxies, independent of photo fidelity and acceptance.

No learned taste rating, search, or mutation happens here. Scores describe the
supplied catalog scene and the echoed policy, not an objectively optimal room.
"""

from math import ceil, cos, floor, hypot, pi, sin
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from roometry.scenes.assets import ASSET_REGISTRY, ObjectType
from roometry.scenes.catalog import object_surfaces
from roometry.scenes.collision import ComponentBounds, component_bounds
from roometry.scenes.geometry import (
    TOLERANCE_M, ObjectBounds, Rectangle, boxes_overlap, intervals_overlap,
    object_bounds, rectangle_contains,
)
from roometry.scenes.navigation import grid_shape, point_rectangle_distance
from roometry.scenes.schemas import Identifier, RoomScene
from roometry.scenes.validation import GeometryReport, validate_geometry
from roometry.uploads.schemas import Goal

Score = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
Criterion = Literal["openness", "storage", "workspace", "aesthetics"]
MetricUnit = Literal["m", "m2", "m3", "count", "fraction"]
MAX_PROJECTION_TESTS = 1_000_000
STORAGE_TYPES: frozenset[ObjectType] = frozenset({
    "wardrobe", "dresser", "shelf", "tv_stand", "shoe_rack", "sideboard",
    "wall_shelf", "storage_box", "basket", "nightstand", "kitchen_cabinet",
    "wall_cabinet", "vanity",
})
GOAL_CRITERIA: dict[Goal, tuple[Criterion, ...]] = {
    "general_analysis": ("openness", "storage", "workspace", "aesthetics"),
    "more_open_space": ("openness",), "more_storage": ("storage",),
    "workspace": ("workspace",), "better_aesthetics": ("aesthetics",),
}


class ScoreModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, revalidate_instances="always")


class LayoutScoringPolicy(ScoreModel):
    cell_size_m: float = Field(default=.1, ge=.05, le=.5, allow_inf_nan=False)
    floor_gap_m: float = Field(default=.02, ge=0, le=.1, allow_inf_nan=False)
    clearance_height_m: float = Field(default=1.8, ge=.5, le=2.5, allow_inf_nan=False)
    storage_reference_m3: float = Field(default=1., gt=0, le=100., allow_inf_nan=False)
    minimum_worktop_width_m: float = Field(default=1., ge=.4, le=3., allow_inf_nan=False)
    minimum_worktop_depth_m: float = Field(default=.5, ge=.3, le=1.5, allow_inf_nan=False)
    minimum_worktop_height_m: float = Field(default=.6, ge=.3, le=1.5, allow_inf_nan=False)
    maximum_worktop_height_m: float = Field(default=.9, ge=.3, le=1.5, allow_inf_nan=False)
    chair_zone_width_m: float = Field(default=.6, ge=.4, le=1.5, allow_inf_nan=False)
    chair_zone_depth_m: float = Field(default=.8, ge=.4, le=1.5, allow_inf_nan=False)
    chair_tuck_allowance_m: float = Field(default=.25, ge=0, le=.5, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered_heights(self) -> Self:
        if self.maximum_worktop_height_m < self.minimum_worktop_height_m:
            raise ValueError("Maximum worktop height must be at least the minimum height.")
        return self


class ScoreMetric(ScoreModel):
    key: str
    value: Annotated[float, Field(allow_inf_nan=False)]
    unit: MetricUnit


class CriterionScore(ScoreModel):
    status: Literal["scored", "unassessed"]
    score: Score | None
    explanation: str
    metrics: tuple[ScoreMetric, ...] = ()
    entity_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def score_matches_status(self) -> Self:
        if (self.status == "scored") != (self.score is not None):
            raise ValueError("Unassessed criteria must have a null score.")
        return self


class WorkstationScore(ScoreModel):
    object_id: Identifier
    worktop_width_m: float
    worktop_depth_m: float
    worktop_height_m: float
    approach_inside_room: bool
    approach_clear: bool
    permitted_chair_id: Identifier | None
    blocking_object_ids: tuple[Identifier, ...]
    score: Score


class ScoreLayoutRequest(ScoreModel):
    scene: RoomScene
    goals: Annotated[list[Goal], Field(max_length=5)] = Field(default_factory=lambda: ["general_analysis"])
    policy: LayoutScoringPolicy = Field(default_factory=LayoutScoringPolicy)

    @model_validator(mode="after")
    def unique_goals(self) -> Self:
        if len(self.goals) != len(set(self.goals)):
            raise ValueError("Scoring goals must be unique.")
        return self


class LayoutScoreReport(ScoreModel):
    policy_version: Literal["layout-score-v1"] = "layout-score-v1"
    policy: LayoutScoringPolicy
    requested_goals: tuple[Goal, ...]
    selected_criteria: tuple[Criterion, ...]
    geometry: GeometryReport
    openness: CriterionScore
    storage: CriterionScore
    workspace: CriterionScore
    aesthetics: CriterionScore
    workstations: tuple[WorkstationScore, ...]
    overall_score: Score | None
    warnings: tuple[str, ...]

    @field_validator("geometry", mode="before")
    @classmethod
    def read_serialized_geometry(cls, value: object) -> object:
        if isinstance(value, dict) and "valid" in value:
            if value["valid"] is not (not value.get("errors")):
                raise ValueError("Geometry validity must agree with its errors.")
            return {key: item for key, item in value.items() if key != "valid"}
        return value

    @model_validator(mode="after")
    def consistent_scores(self) -> Self:
        if not self.requested_goals or len(set(self.requested_goals)) != len(self.requested_goals):
            raise ValueError("Scored goals must be nonempty and unique.")
        expected = tuple(dict.fromkeys(key for goal in self.requested_goals for key in GOAL_CRITERIA[goal]))
        if expected != self.selected_criteria:
            raise ValueError("Selected criteria must match the requested goals.")
        all_scores = [self.openness, self.storage, self.workspace, self.aesthetics]
        if not self.geometry.valid and (any(c.score is not None for c in all_scores) or self.workstations):
            raise ValueError("Invalid geometry cannot receive layout scores.")
        values = [getattr(self, key).score for key in self.selected_criteria]
        average = sum(v for v in values if v is not None) / len(values) if all(v is not None for v in values) else None
        if (average is None) != (self.overall_score is None) or (
            average is not None and self.overall_score is not None and abs(average - self.overall_score) > 1e-8
        ):
            raise ValueError("Overall score must average all selected, assessed criteria or remain null.")
        return self


def _metric(key: str, value: float, unit: MetricUnit) -> ScoreMetric:
    return ScoreMetric(key=key, value=float(value), unit=unit)


def _unknown(reason: str) -> CriterionScore:
    return CriterionScore(status="unassessed", score=None, explanation=reason)


def _openness(scene: RoomScene, policy: LayoutScoringPolicy,
              solids: dict[str, tuple[ComponentBounds, ...]]) -> CriterionScore:
    nx, nz = grid_shape(scene.room.width_m, scene.room.length_m, policy.cell_size_m)
    dx, dz = scene.room.width_m / nx, scene.room.length_m / nz
    occupied = bytearray(nx * nz)
    work = 0
    footprints = dict.fromkeys(
        part.bounds.footprint for parts in solids.values() for part in parts
        if intervals_overlap(part.bounds.vertical, (policy.floor_gap_m, policy.clearance_height_m))
    )
    for rectangle in footprints:
        xs, zs = zip(*rectangle.corners)
        i0, i1 = max(0, floor(min(xs) / dx)), min(nx - 1, ceil(max(xs) / dx))
        j0, j1 = max(0, floor(min(zs) / dz)), min(nz - 1, ceil(max(zs) / dz))
        for j in range(j0, j1 + 1):
            for i in range(i0, i1 + 1):
                index = j * nx + i
                if occupied[index]:
                    continue
                work += 1
                if work > MAX_PROJECTION_TESTS:
                    return _unknown("The bounded floor-projection work limit was reached; openness was not scored.")
                if point_rectangle_distance(((i + .5) * dx, (j + .5) * dz), rectangle) <= TOLERANCE_M:
                    occupied[index] = 1
    floor_area = scene.room.width_m * scene.room.length_m
    fraction = 1 - sum(occupied) / len(occupied)
    return CriterionScore(status="scored", score=100 * fraction,
        explanation="100 × sampled unoccupied floor fraction. Uses the union of standing-height solid projections; this is independent of entrance reachability.",
        metrics=(_metric("floor_area", floor_area, "m2"), _metric("unoccupied_floor_area", floor_area * fraction, "m2"),
                 _metric("unoccupied_fraction", fraction, "fraction"), _metric("grid_step_x", dx, "m"),
                 _metric("grid_step_z", dz, "m"), _metric("grid_cells", nx * nz, "count")))


def _workspace(scene: RoomScene, policy: LayoutScoringPolicy,
               solids: dict[str, tuple[ComponentBounds, ...]]) -> tuple[CriterionScore, tuple[WorkstationScore, ...]]:
    room_floor = Rectangle((scene.room.width_m / 2, scene.room.length_m / 2), ((1., 0.), (0., 1.)),
                           (scene.room.width_m / 2, scene.room.length_m / 2))
    stations: list[WorkstationScore] = []
    for desk in sorted((o for o in scene.objects if o.type == "desk" and o.placement == "floor"), key=lambda o: o.id):
        surfaces = object_surfaces(desk.asset_id, desk.placement)
        if not surfaces:
            continue
        top = next((s for s in surfaces if s.part == "worktop"), None) or max(
            surfaces, key=lambda s: (s.y, (s.x_max - s.x_min) * (s.z_max - s.z_min)))
        width = (top.x_max - top.x_min) * desk.dimensions_m.width
        depth = (top.z_max - top.z_min) * desk.dimensions_m.depth
        height = desk.position.y + top.y * desk.dimensions_m.height
        c, s = cos(desk.rotation_y_rad), sin(desk.rotation_y_rad)

        def zone(tuck: float) -> Rectangle:
            offset = desk.dimensions_m.depth / 2 + (policy.chair_zone_depth_m - tuck) / 2
            return Rectangle((desk.position.x + s * offset, desk.position.z + c * offset),
                ((c, -s), (s, c)), (policy.chair_zone_width_m / 2, (policy.chair_zone_depth_m + tuck) / 2))

        approach = zone(0.)
        allowed_chair = None
        for chair in sorted(scene.objects, key=lambda o: o.id):
            if chair.type not in {"chair", "stool"} or chair.placement != "floor":
                continue
            dx, dz = desk.position.x - chair.position.x, desk.position.z - chair.position.z
            distance = hypot(dx, dz)
            facing = chair.type == "stool" or (distance > TOLERANCE_M and
                (sin(chair.rotation_y_rad) * dx + cos(chair.rotation_y_rad) * dz) / distance >= cos(pi / 4))
            if facing and rectangle_contains(zone(policy.chair_tuck_allowance_m), object_bounds(chair).footprint):
                allowed_chair = chair.id
                break
        inside = rectangle_contains(room_floor, approach)
        approach_box = ObjectBounds(approach, (policy.floor_gap_m, policy.clearance_height_m))
        blockers = tuple(key for key, parts in solids.items() if key not in {desk.id, allowed_chair} and any(boxes_overlap(approach_box, part.bounds) for part in parts))
        clear = inside and not blockers
        size_score = min(1., width / policy.minimum_worktop_width_m) * min(1., depth / policy.minimum_worktop_depth_m)
        height_ok = policy.minimum_worktop_height_m <= height <= policy.maximum_worktop_height_m
        stations.append(WorkstationScore(object_id=desk.id, worktop_width_m=width, worktop_depth_m=depth,
            worktop_height_m=height, approach_inside_room=inside, approach_clear=clear,
            permitted_chair_id=allowed_chair, blocking_object_ids=blockers,
            score=100 * size_score if clear and height_ok else 0.))
    value = max((station.score for station in stations), default=0.)
    result = CriterionScore(status="scored", score=value,
        explanation="Best declared desk: 100 × capped worktop width/depth ratios, or zero when height or chair clearance fails the policy. One fitting chair facing the desk is allowed. Does not assess desktop clutter, ergonomics, or entrance-to-desk reachability.",
        metrics=(_metric("assessed_workstations", len(stations), "count"),),
        entity_ids=tuple(station.object_id for station in stations))
    return result, tuple(stations)


def score_layout(scene: RoomScene, goals: list[Goal] | None = None,
                 policy: LayoutScoringPolicy | None = None) -> LayoutScoreReport:
    request = ScoreLayoutRequest(scene=scene, goals=goals if goals is not None else ["general_analysis"],
                                 policy=policy if policy is not None else LayoutScoringPolicy())
    scene, policy = request.scene, request.policy
    requested = tuple(request.goals) or ("general_analysis",)
    selected = tuple(dict.fromkeys(key for goal in requested for key in GOAL_CRITERIA[goal]))
    geometry = validate_geometry(scene)
    warnings = (
        "Scores are soft planning proxies, not photo fidelity, safety, or permission to remove observed/locked furniture. Geometry and clearance checks remain separate constraints.",
        "Compare scores only under the same policy. Averages use equal weights and are unset if any selected criterion is unassessed.",
        "Storage uses nominal outside volumes, not verified usable capacity. Openness is sampled; aesthetics requires explicit user criteria.",
    )
    if not geometry.valid:
        unknown = _unknown("Resolve component geometry errors before scoring this scene.")
        return LayoutScoreReport(policy=policy, requested_goals=requested, selected_criteria=selected,
            geometry=geometry, openness=unknown, storage=unknown, workspace=unknown,
            aesthetics=unknown, workstations=(), overall_score=None, warnings=warnings)
    solids = {o.id: component_bounds(o) for o in sorted(scene.objects, key=lambda o: o.id)
              if ASSET_REGISTRY[o.asset_id].object_type != "rug"}
    openness = _openness(scene, policy, solids)
    storage_objects = sorted((o for o in scene.objects if o.type in STORAGE_TYPES and o.placement in {"floor", "wall"}), key=lambda o: o.id)
    volume = sum(o.dimensions_m.width * o.dimensions_m.height * o.dimensions_m.depth for o in storage_objects)
    storage = CriterionScore(status="scored", score=100 * volume / (volume + policy.storage_reference_m3),
        explanation="100 × V / (V + storage_reference_m3), where V sums nominal outer volumes of standalone storage types. Surface-mounted containers are excluded to avoid rewarding stacked storage twice. This does not establish contents, internal capacity, or accessibility.",
        metrics=(_metric("nominal_storage_volume", volume, "m3"), _metric("storage_objects", len(storage_objects), "count")),
        entity_ids=tuple(o.id for o in storage_objects))
    workspace, stations = _workspace(scene, policy, solids)
    aesthetics = _unknown("Room geometry and free-text style do not determine personal taste. No measurable aesthetic preference has been supplied to this scorer.")
    scores = {"openness": openness, "storage": storage, "workspace": workspace, "aesthetics": aesthetics}
    values = [scores[key].score for key in selected]
    overall = sum(v for v in values if v is not None) / len(values) if all(v is not None for v in values) else None
    return LayoutScoreReport(policy=policy, requested_goals=requested, selected_criteria=selected,
        geometry=geometry, openness=openness, storage=storage, workspace=workspace,
        aesthetics=aesthetics, workstations=stations, overall_score=overall, warnings=warnings)
