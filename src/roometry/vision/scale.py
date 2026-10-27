"""Conservative room-span estimates from existing photo observations.

No catalogue defaults, room-type templates, pixel distances or extra model calls.
The measurements remain estimates; a cropped wall cannot supply its full span.
"""

from math import floor
from statistics import median

from roometry.scenes.schemas import MAX_ROOM_SIDE_M
from roometry.vision.schemas import ObjectObservation, RoomAnalysisResponse


def photo_span_estimates(analysis: RoomAnalysisResponse) -> tuple[dict[str, float], list[str]]:
    obs = analysis.observations
    # Without an established axis assignment, two complete spans are needed.
    # Do not infer what a user's single width/length measurement refers to.
    if any(getattr(source, axis, None) is not None
           for source in (analysis.input_options.dimensions, obs.estimated_dimensions)
           for axis in ("width_m", "length_m")):
        return {}, []
    roles = {wall.id: wall.span.role for wall in obs.walls
             if wall.span is not None and wall.span.confidence == "high"}
    for relation in obs.relationships:
        if (relation.confidence == "high" and relation.kind in {"adjacent_wall", "opposite_wall"}
                and relation.subject_id in roles and relation.target_id in roles):
            same_span = roles[relation.subject_id] == roles[relation.target_id]
            if same_span != (relation.kind == "opposite_wall"):
                return {}, []
    parallel = {(r.subject_id, r.target_id) for r in obs.relationships
                if r.kind == "against_wall" and r.confidence == "high"}
    spans: dict[str, list[tuple[float, str]]] = {"shorter": [], "longer": []}
    for wall in obs.walls:
        if wall.span is None or wall.span.confidence != "high":
            continue
        samples = []
        for item in [*obs.objects, *obs.openings]:
            if item.wall_id != wall.id or item.confidence != "high":
                continue
            if not set(item.source_image_ids) & set(wall.source_image_ids):
                continue
            coverage = item.wall_coverage
            if coverage is None or coverage < .2:
                continue
            if isinstance(item, ObjectObservation):
                if (item.id, wall.id) not in parallel:
                    continue  # A sofa's local width may be perpendicular to the wall.
                width = getattr(item.estimated_dimensions_m, "width", None)
            else:
                width = item.estimated_width_m
            if width is not None and 1 <= width / coverage <= MAX_ROOM_SIDE_M:
                samples.append((width / coverage, item.id))
        if not samples or max(v for v, _ in samples) / min(v for v, _ in samples) > 1.35:
            continue  # Conflicting scale references are not averaged into false confidence.
        spans[wall.span.role].extend(samples)
    if not all(spans.values()):
        return {}, []
    for samples in spans.values():
        if max(v for v, _ in samples) / min(v for v, _ in samples) > 1.35:
            return {}, []
    short, long = (median(v for v, _ in spans[role]) for role in ("shorter", "longer"))
    if long < short:
        return {}, []
    values = {axis: floor(value * 2 + .5) / 2
              for axis, value in (("width_m", short), ("length_m", long))}
    wall_labels = {wall.id: wall.label for wall in obs.walls}
    labels = {item.id: getattr(item, "label", None) or f"{item.type} on {wall_labels[item.wall_id]}"
              for item in [*obs.objects, *obs.openings] if item.wall_id in wall_labels}
    notes = []
    for axis, role in (("width_m", "shorter"), ("length_m", "longer")):
        refs = ", ".join(sorted({labels[key].replace("_", " ") for _, key in spans[role]}))
        notes.append((f"Estimated room {axis.removesuffix('_m')}: {values[axis]:g} m from photo size and "
                      f"full-wall coverage of {refs}. No plan axes were supplied: width denotes the shorter "
                      "span and length the longer span. This is a rough scale estimate, not a measurement.")[:1000])
    return values, notes
