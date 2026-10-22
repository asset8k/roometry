"""Resolve physical walls to measured axes before assigning object coordinates.

Four rectangular walls have at most 16 axis assignments. Evidence or a human
answer selects the applicable family; this never swaps or edits room dimensions.
"""

from itertools import product
from math import isclose
from typing import Literal

from roometry.scenes.schemas import Wall, WallAxisBinding
from roometry.uploads.schemas import InputDimensions
from roometry.vision.schemas import ReconstructionConstraints, RoomAnalysisResponse, WallAxisResolution

PlanAxis = Literal["width_m", "length_m"]
AXIS_WALLS: dict[PlanAxis, tuple[Wall, Wall]] = {
    "width_m": ("north", "south"), "length_m": ("west", "east"),
}


def wall_axis(wall: Wall) -> PlanAxis:
    return "width_m" if wall in {"north", "south"} else "length_m"


def resolve_wall_axes(analysis: RoomAnalysisResponse, dimensions: InputDimensions, constraints: ReconstructionConstraints, *, allow_ambiguous: bool = False) -> WallAxisResolution:
    obs = analysis.observations
    walls = {wall.id: wall for wall in obs.walls}
    w, l = dimensions.width_m, dimensions.length_m
    empty = dict(reference_axis=None, reference_span_m=None, candidate_wall_ids=[], bindings=[], warnings=[])
    if w is None or l is None or not walls or isclose(w, l, rel_tol=.02, abs_tol=.05):
        return WallAxisResolution(status="not_applicable", **empty)
    short_axis: PlanAxis = "width_m" if w < l else "length_m"
    long_axis: PlanAxis = "length_m" if w < l else "width_m"
    topology = [r for r in obs.relationships if r.kind in {"adjacent_wall", "opposite_wall"} and r.confidence == "high"]
    ids = sorted(walls)
    axes: tuple[PlanAxis, PlanAxis] = ("width_m", "length_m")
    assignments = [dict(zip(ids, values)) for values in product(axes, repeat=len(ids)) if values.count("width_m") <= 2 and values.count("length_m") <= 2]
    coherent = [a for a in assignments if all((a[r.subject_id] != a[r.target_id]) == (r.kind == "adjacent_wall") for r in topology)]
    warnings = []
    if not coherent:
        coherent = assignments
        warnings.append("The photo wall-topology hypotheses conflict; they cannot establish room orientation.")
    answer = constraints.wall_axis
    if answer is not None and answer.wall_id is not None and answer.wall_id not in walls:
        raise ValueError("The wall-axis answer must identify an observed wall.")
    if answer is not None and answer.wall_id is not None:
        selected = [a for a in coherent if a[answer.wall_id] == answer.axis]
        source = "user"
    else:
        seeds = {wall.id: short_axis if wall.span.role == "shorter" else long_axis
                 for wall in walls.values() if wall.span is not None and wall.span.confidence == "high"}
        selected = [a for a in coherent if all(a[key] == value for key, value in seeds.items())] if seeds else []
        source = "photo"
    if selected and not (answer is not None and answer.wall_id is None):
        bindings = [WallAxisBinding(observation_id=key, axis=selected[0][key], source=source)
                    for key in ids if len({a[key] for a in selected}) == 1]
        if bindings:
            return WallAxisResolution(status="resolved", reference_axis=short_axis, reference_span_m=min(w, l), candidate_wall_ids=[], bindings=bindings, warnings=warnings)
    # Human-readable, stable candidates prefer recognizable walls over an unseen
    # boundary. No rule says that a window belongs on the shorter wall.
    def salience(key: str):
        opening = any(o.wall_id == key for o in obs.openings)
        tv = any(o.wall_id == key and o.type == "tv" for o in obs.objects)
        return (-int(opening), -int(tv), -len(walls[key].source_image_ids), key)
    ordered = sorted(ids, key=salience)
    first = ordered[0]
    adjacent = [key for key in ordered[1:] if all(a[first] != a[key] for a in coherent)]
    candidates = [first, adjacent[0]] if adjacent else [first]
    uncertain = answer is not None or allow_ambiguous
    if uncertain:
        warnings.append("The wall-to-measurement assignment is unconfirmed. Orientation is a photo estimate; the entered dimensions are preserved.")
    return WallAxisResolution(status="estimated" if uncertain else "needs_clarification", reference_axis=short_axis, reference_span_m=min(w, l), candidate_wall_ids=candidates, bindings=[], warnings=warnings)
