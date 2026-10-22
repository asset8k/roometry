"""Advisory entrance connectivity and narrow passages; no layout optimization."""

from array import array
from dataclasses import dataclass
from math import ceil
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from roometry.scenes.assets import ASSET_REGISTRY
from roometry.scenes.collision import component_bounds
from roometry.scenes.geometry import Point2, Rectangle, TOLERANCE_M, intervals_overlap, opening_inside_wall
from roometry.scenes.navigation import (
    NavigationGrid, NavigationLimit, build_navigation_grid, segment_rectangle_distance,
)
from roometry.scenes.schemas import Identifier, Opening, RoomScene

WalkingRule = Literal[
    "entrance_route_blocked", "entrances_disconnected", "walking_space_disconnected",
    "narrow_passage", "walking_space_unassessed",
]
RouteStatus = Literal["clear", "narrow", "blocked", "unassessed"]
MAX_ENTRANCE_TESTS = 250_000


@dataclass
class _EntranceBudget:
    remaining: int = MAX_ENTRANCE_TESTS

    def clear_segment(self, a: Point2, b: Point2, footprints: tuple[Rectangle, ...], radius: float) -> bool:
        for rect in footprints:
            self.remaining -= 1
            if self.remaining < 0:
                raise NavigationLimit
            if segment_rectangle_distance(a, b, rect) + TOLERANCE_M < radius:
                return False
        return True


class WalkingPolicy(BaseModel):
    """Configurable MVP heuristics, not accessibility or evacuation standards."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, revalidate_instances="always")

    minimum_width_m: float = Field(default=.6, ge=.3, le=2., allow_inf_nan=False)
    preferred_width_m: float = Field(default=.8, ge=.3, le=3., allow_inf_nan=False)
    probe_height_m: float = Field(default=1.8, ge=.5, le=2.5, allow_inf_nan=False)
    floor_gap_m: float = Field(default=.02, ge=0., le=.1, allow_inf_nan=False)
    cell_size_m: float = Field(default=.1, ge=.05, le=.5, allow_inf_nan=False)
    significant_region_area_m2: float = Field(default=.5, ge=.1, le=10., allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered_widths(self) -> Self:
        if self.preferred_width_m < self.minimum_width_m:
            raise ValueError("Preferred walking width must be at least the minimum width.")
        return self


class WalkingIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: WalkingRule
    entity_ids: tuple[Identifier, ...]
    message: str


class EntranceRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    door_id: Identifier
    status: RouteStatus
    main_region_reachable: bool | None
    reachable_door_ids: tuple[Identifier, ...] = ()
    unreachable_door_ids: tuple[Identifier, ...] = ()


class WalkingReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    policy_version: Literal["walking-v1"] = "walking-v1"
    policy: WalkingPolicy
    status: RouteStatus
    grid_step_x_m: float | None
    grid_step_z_m: float | None
    grid_cells: int
    # These areas count possible walking-probe centers, not physical floor area.
    walkable_center_area_m2: float | None
    main_region_area_m2: float | None
    disconnected_center_area_m2: float | None
    significant_region_count: int
    entrances: tuple[EntranceRoute, ...]
    issues: tuple[WalkingIssue, ...]


def _entrance_regions(scene: RoomScene, door: Opening, radius: float, grid: NavigationGrid,
                      labels: array, footprints: tuple[Rectangle, ...], budget: _EntranceBudget) -> set[int]:
    """Connect the actual opening to samples; never teleport past a doorstep block.

    The probe enters perpendicular to the wall, then connects to the first grid
    row inside the room. Clamping along the opening also handles a door exactly
    as wide as the probe when its center falls between grid columns.
    """
    if door.width_m + TOLERANCE_M < 2 * radius:
        return set()
    low, high = door.offset_m + radius, door.offset_m + door.width_m - radius
    north_south = door.wall in {"north", "south"}
    step = grid.dz if north_south else grid.dx
    normal_index = max(0, ceil(radius / step - .5 - TOLERANCE_M))
    if normal_index >= (grid.nz if north_south else grid.nx):
        return set()
    result = set()
    for tangent_index in range(grid.nx if north_south else grid.nz):
        i = tangent_index if north_south else normal_index
        j = normal_index if north_south else tangent_index
        if door.wall == "south":
            j = grid.nz - 1 - normal_index
        elif door.wall == "east":
            i = grid.nx - 1 - normal_index
        index = j * grid.nx + i
        if labels[index] < 0:
            continue
        point = grid.point(index)
        along = point[0] if north_south else point[1]
        tangent_step = grid.dx if north_south else grid.dz
        if along < low - tangent_step or along > high + tangent_step:
            continue
        along = max(low, min(high, along))
        portal, anchor = {
            "north": ((along, 0.), (along, radius)),
            "south": ((along, scene.room.length_m), (along, scene.room.length_m - radius)),
            "west": ((0., along), (radius, along)),
            "east": ((scene.room.width_m, along), (scene.room.width_m - radius, along)),
        }[door.wall]
        if budget.clear_segment(portal, anchor, footprints, radius) and budget.clear_segment(anchor, point, footprints, radius):
            result.add(labels[index])
    return result


def _unassessed(policy: WalkingPolicy, doors: list[Opening], message: str,
                entity_ids: tuple[str, ...] = ()) -> WalkingReport:
    return WalkingReport(
        policy=policy, status="unassessed", grid_step_x_m=None, grid_step_z_m=None,
        grid_cells=0, walkable_center_area_m2=None, main_region_area_m2=None,
        disconnected_center_area_m2=None, significant_region_count=0,
        entrances=tuple(EntranceRoute(door_id=o.id, status="unassessed", main_region_reachable=None) for o in doors),
        issues=(WalkingIssue(code="walking_space_unassessed", entity_ids=entity_ids, message=message),),
    )


def check_walking_space(scene: RoomScene, policy: WalkingPolicy | None = None) -> WalkingReport:
    scene = RoomScene.model_validate(scene)
    policy = WalkingPolicy.model_validate(policy or WalkingPolicy())
    doors = sorted((o for o in scene.openings if o.type == "door"), key=lambda o: o.id)
    valid_doors = [o for o in doors if opening_inside_wall(o, scene.room)]
    if not valid_doors:
        return _unassessed(policy, doors,
            "Walking routes need at least one known door within its wall bounds; no entrance route was assessed.",
            tuple(o.id for o in doors))
    # Project solid components intersecting the full upright probe height.
    # Identical footprints (e.g. repeated shelf boards) need rasterizing only once.
    footprints = tuple(dict.fromkeys(
        part.bounds.footprint
        for obj in sorted(scene.objects, key=lambda o: o.id)
        if ASSET_REGISTRY[obj.asset_id].object_type != "rug"
        for part in component_bounds(obj)
        if intervals_overlap(part.bounds.vertical, (policy.floor_gap_m, policy.probe_height_m))
    ))
    try:
        grid = build_navigation_grid(scene.room.width_m, scene.room.length_m, policy.cell_size_m,
                                     footprints, policy.preferred_width_m / 2)
    except NavigationLimit:
        return _unassessed(policy, doors,
            "The bounded walking-check work limit was reached. No clearance or successful route is claimed.")
    minimum, sizes = grid.regions(policy.minimum_width_m / 2)
    preferred, preferred_sizes = grid.regions(policy.preferred_width_m / 2)
    area = grid.dx * grid.dz
    main = max(range(len(sizes)), key=lambda key: sizes[key]) if sizes else None
    significant = {key for key, size in enumerate(sizes) if size * area + TOLERANCE_M >= policy.significant_region_area_m2}
    # Preferred regions are subsets of minimum regions, since their radius is larger.
    preferred_in_main = {preferred[i] for i, label in enumerate(minimum) if label == main and preferred[i] >= 0}
    preferred_targets = {key for key in preferred_in_main if preferred_sizes[key] * area + TOLERANCE_M >= policy.significant_region_area_m2}
    if not preferred_targets and preferred_in_main:
        preferred_targets = {max(preferred_in_main, key=lambda key: preferred_sizes[key])}
    entry_min, entry_preferred = {}, {}
    try:
        budget = _EntranceBudget(remaining=MAX_ENTRANCE_TESTS)
        for door in valid_doors:
            if door.height_m + TOLERANCE_M < policy.probe_height_m:
                entry_min[door.id], entry_preferred[door.id] = set(), set()
            else:
                entry_min[door.id] = _entrance_regions(scene, door, policy.minimum_width_m / 2, grid, minimum, footprints, budget)
                entry_preferred[door.id] = _entrance_regions(scene, door, policy.preferred_width_m / 2, grid, preferred, footprints, budget)
    except NavigationLimit:
        return _unassessed(policy, doors,
            "The bounded entrance-connection work limit was reached. No successful route is claimed.")
    checks, issues = [], []
    disconnected = len(significant) > 1
    if disconnected:
        issues.append(WalkingIssue(code="walking_space_disconnected", entity_ids=(), message=f"Substantial walking-center regions are disconnected at {policy.minimum_width_m:g} m width in the sampled floor grid."))
    if len(preferred_targets) > 1:
        issues.append(WalkingIssue(code="narrow_passage", entity_ids=(), message=f"Parts of the main usable area connect at {policy.minimum_width_m:g} m but not at the preferred {policy.preferred_width_m:g} m passage width."))
    for door in doors:
        if door.id not in entry_min:
            checks.append(EntranceRoute(door_id=door.id, status="unassessed", main_region_reachable=None))
            issues.append(WalkingIssue(code="walking_space_unassessed", entity_ids=(door.id,), message="This entrance lies outside its wall bounds; its routes were not assessed."))
            continue
        reachable = tuple(other.id for other in valid_doors if other.id != door.id and entry_min[door.id] & entry_min[other.id])
        unreachable = tuple(other.id for other in valid_doors if other.id != door.id and other.id not in reachable)
        main_reachable = main is not None and main in entry_min[door.id]
        if not main_reachable:
            status: RouteStatus = "blocked"
            message = f"No {policy.minimum_width_m:g} m walking route from this entrance to the largest usable region was found at the reported grid resolution."
            if door.width_m + TOLERANCE_M < policy.minimum_width_m or door.height_m + TOLERANCE_M < policy.probe_height_m:
                message = "The modeled doorway does not fit the configured walking width or probe height."
            issues.append(WalkingIssue(code="entrance_route_blocked", entity_ids=(door.id,), message=message))
        elif not preferred_targets or not preferred_targets <= entry_preferred[door.id]:
            status = "narrow"
            issues.append(WalkingIssue(code="narrow_passage", entity_ids=(door.id,), message=f"An entrance route fits {policy.minimum_width_m:g} m, but the preferred {policy.preferred_width_m:g} m routes to substantial parts of the main usable area were not found."))
        else:
            status = "clear"
        checks.append(EntranceRoute(door_id=door.id, status=status, main_region_reachable=main_reachable,
                                   reachable_door_ids=reachable, unreachable_door_ids=unreachable))
        for other_id in unreachable:
            if door.id < other_id:
                issues.append(WalkingIssue(code="entrances_disconnected", entity_ids=(door.id, other_id), message=f"No internal walking route of {policy.minimum_width_m:g} m between these entrances was found in the sampled grid."))
    overall: RouteStatus = "blocked" if disconnected or any(c.status == "blocked" for c in checks) else "narrow" if any(c.status == "narrow" for c in checks) or len(preferred_targets) > 1 else "unassessed" if any(c.status == "unassessed" for c in checks) else "clear"
    return WalkingReport(policy=policy, status=overall, grid_step_x_m=grid.dx, grid_step_z_m=grid.dz,
        grid_cells=grid.nx * grid.nz, walkable_center_area_m2=sum(sizes) * area,
        main_region_area_m2=sizes[main] * area if main is not None else 0.,
        disconnected_center_area_m2=sum(size * area for key, size in enumerate(sizes) if key != main),
        significant_region_count=len(significant), entrances=tuple(checks), issues=tuple(issues))
