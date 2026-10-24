"""Deterministic reconstruction checks against evidence, separate from collisions.

Passing checks means consistency with recorded hypotheses, not photogrammetric
accuracy. Unobserved relationships and default colors/sizes are not scored.
"""

from math import cos, hypot, sin
from typing import Annotated, Literal

from pydantic import Field

from roometry.scenes.schemas import Identifier, RoomScene, SceneModel, Wall
from roometry.vision.importance import importance
from roometry.vision.interpretations import resolve_interpretations
from roometry.vision.appearance import resolve_appearance
from roometry.vision.spatial import planar_gap, relationship_error, relationship_limit
from roometry.vision.wall_axes import wall_axis
from roometry.vision.schemas import RoomAnalysisResponse


class FidelityCheck(SceneModel):
    kind: Literal["coverage", "facing", "wall", "adjacency", "support", "proportion", "color"]
    entity_ids: list[Identifier]
    passed: bool
    required: bool
    message: str


class FidelityReport(SceneModel):
    policy_version: Literal["reconstruction_v1"] = "reconstruction_v1"
    major_object_coverage: Annotated[float, Field(ge=0, le=1)]
    relationship_score: Annotated[float, Field(ge=0, le=1)] | None
    proportion_score: Annotated[float, Field(ge=0, le=1)] | None
    color_score: Annotated[float, Field(ge=0, le=1)] | None
    recognizable: bool | None
    checks: Annotated[list[FidelityCheck], Field(max_length=4000)]
    unassessed: list[str]


def evaluate_fidelity(analysis: RoomAnalysisResponse, scene: RoomScene, wall_map: dict[str, Wall] | None = None) -> FidelityReport:
    analysis, interpretation_errors = resolve_interpretations(analysis, scene.interpretation_corrections)
    analysis, appearance_errors = resolve_appearance(analysis, scene.appearance_corrections)
    obs = analysis.observations
    wall_map = wall_map if wall_map is not None else {item.observation_id: item.wall for item in scene.wall_mapping}
    objects = {obj.id: obj for obj in scene.objects}
    openings = {obj.id: obj for obj in scene.openings}
    room = scene.room
    checks: list[FidelityCheck] = []
    unassessed = ["Positions and dimensions are approximate; passing compares the reconstruction with recorded evidence, not a measured 3D scan."]

    def check(kind, ids, passed, message, required=True):
        checks.append(FidelityCheck(kind=kind, entity_ids=ids, passed=bool(passed), required=required, message=message))

    def point(entity_id):
        if entity_id in objects:
            obj = objects[entity_id]
            return obj.position.x, obj.position.z
        if entity_id in openings:
            o = openings[entity_id]
            t = o.offset_m + o.width_m / 2
            return {"north": (t, 0), "south": (t, room.length_m), "west": (0, t), "east": (room.width_m, t)}[o.wall]
        if entity_id in wall_map:
            return {"north": (room.width_m / 2, 0), "south": (room.width_m / 2, room.length_m), "west": (0, room.length_m / 2), "east": (room.width_m, room.length_m / 2)}[wall_map[entity_id]]
        return None

    def wall_gap(obj, wall):
        c, s = abs(cos(obj.rotation_y_rad)), abs(sin(obj.rotation_y_rad))
        hx = (c * obj.dimensions_m.width + s * obj.dimensions_m.depth) / 2
        hz = (s * obj.dimensions_m.width + c * obj.dimensions_m.depth) / 2
        return {"north": obj.position.z - hz, "south": room.length_m - obj.position.z - hz, "west": obj.position.x - hx, "east": room.width_m - obj.position.x - hx}[wall]

    for binding in scene.wall_axis_bindings:
        assigned = wall_map.get(binding.observation_id)
        check("wall", [binding.observation_id], assigned is not None and wall_axis(assigned) == binding.axis, "Preserve this wall's assignment to the measured room dimension.", binding.source != "estimated")
    for entity_id, message in interpretation_errors:
        check("support", [entity_id], False, message)
    for entity_id, message in appearance_errors:
        check("color", [entity_id], False, message)
    major = [o for o in [*obs.objects, *obs.openings] if importance(o) == 0 and o.confidence != "low"]
    retained = objects.keys() | openings.keys()
    for source in major:
        check("coverage", [source.id], source.id in retained, "Retain the observed major furniture and openings.")
    coverage = sum(o.id in retained for o in major) / len(major) if major else 1.0
    opposite = {"north": "south", "south": "north", "east": "west", "west": "east"}
    for relation in obs.relationships:
        ids = [relation.subject_id, relation.target_id]
        required = relation.confidence == "high" and relation.alternative_target_id is None
        a, b = point(ids[0]), point(ids[1])
        subject = objects.get(ids[0])
        if relation.kind in {"opposite_wall", "adjacent_wall"}:
            left, right = wall_map.get(ids[0]), wall_map.get(ids[1])
            passed = left is not None and right is not None and ((opposite[left] == right) if relation.kind == "opposite_wall" else (left != right and opposite[left] != right))
            check("wall", ids, passed, f"Preserve the observed {relation.kind} relationship.", required)
        elif relation.kind == "faces":
            if subject is None or a is None or b is None:
                passed = False
            else:
                dx, dz = b[0] - a[0], b[1] - a[1]
                distance = hypot(dx, dz)
                passed = distance > .05 and relationship_error(scene, ids[0], ids[1], "faces") <= relationship_limit(scene, "faces")
            check("facing", ids, passed, "This item should face the target shown in the photos.", required)
        elif relation.kind == "against_wall":
            wall = wall_map.get(ids[1])
            check("wall", ids, subject is not None and wall is not None and wall_gap(subject, wall) <= relationship_limit(scene, "against_wall"), "Keep this item in its observed wall group, allowing approximate placement.", required)
        elif relation.kind == "supported_by":
            check("support", ids, subject is not None and subject.support_object_id == ids[1], "Preserve the observed support relationship.", required)
        else:
            passed = planar_gap(ids[0], ids[1], scene, wall_map) <= relationship_limit(scene, "near")
            check("adjacency", ids, passed, "Keep nearby/beside items in the same local group.", required)
    corrections = {(c.entity_id, c.field) for c in scene.estimate_corrections}
    for source in obs.objects:
        obj = objects.get(source.id)
        if obj is None:
            continue
        if source.support_object_id is not None:
            check("support", [source.id, source.support_object_id], obj.support_object_id == source.support_object_id, "Preserve the observed or audited supporting object.")
        if source.wall_id is not None and source.wall_id in wall_map and source.placement == "floor":
            wall = wall_map[source.wall_id]
            # Association is weaker than an explicit against_wall relation.
            check("wall", [source.id, source.wall_id], wall_gap(obj, wall) <= max(.65, min(room.width_m, room.length_m) * .2), "Review this item's approximate wall association.", False)
        if source.wall_coverage is not None and source.wall_id is not None and source.wall_id in wall_map:
            wall = wall_map[source.wall_id]
            c, s = abs(cos(obj.rotation_y_rad)), abs(sin(obj.rotation_y_rad))
            span = c * obj.dimensions_m.width + s * obj.dimensions_m.depth if wall in {"north", "south"} else s * obj.dimensions_m.width + c * obj.dimensions_m.depth
            expected = source.wall_coverage * (room.width_m if wall in {"north", "south"} else room.length_m)
            check("proportion", [source.id], .65 * expected <= span <= 1.5 * expected, "Preserve approximate observed wall coverage; do not force catalog proportions.", source.confidence == "high")
        if source.estimated_dimensions_m:
            for axis in ("width", "depth", "height"):
                expected = getattr(source.estimated_dimensions_m, axis)
                if expected is not None and (source.id, axis) not in corrections:
                    check("proportion", [source.id], .8 * expected <= getattr(obj.dimensions_m, axis) <= 1.25 * expected, f"Preserve the evidence-backed {axis} estimate.")
        if source.color:
            check("color", [source.id], obj.color.upper() == source.color.upper(), "Preserve the observed primary color.")
        if source.material_colors:
            for key, value in source.material_colors.model_dump(exclude_none=True).items():
                check("color", [source.id], obj.material_colors is not None and getattr(obj.material_colors, key, None) == value, f"Preserve the observed {key} color.")
    relation_checks = [c for c in checks if c.kind in {"wall", "facing", "adjacency", "support"}]
    def score(kinds):
        selected = [c for c in checks if c.kind in kinds]
        return sum(c.passed for c in selected) / len(selected) if selected else None
    if not relation_checks:
        unassessed.append("No explicit layout relationships were available; recognizability has not been established.")
    if not any(o.color or o.material_colors for o in obs.objects):
        unassessed.append("Object colors were not established from the photos.")
    if not obs.finishes:
        unassessed.append("Room finishes are unknown; neutral renderer defaults remain.")
    required_layout = [c for c in relation_checks if c.required]
    facing_ids = {c.entity_ids[0] for c in checks if c.kind == "facing" and c.required}
    unoriented = [o.id for o in obs.objects if o.type in {"sofa", "armchair", "chair", "tv"} and o.id in objects and o.id not in facing_ids]
    if unoriented:
        unassessed.append(f"Facing directions remain uncertain for {len(unoriented)} seating or display items.")
    if not any(c.kind == "proportion" for c in checks):
        unassessed.append("Furniture proportions have no independent photo size or wall-coverage estimates to compare.")
    recognizable = coverage >= .8 and all(c.passed for c in required_layout) if required_layout else None
    if analysis.wall_axes is not None and analysis.wall_axes.status == "estimated":
        unassessed.append("The physical wall corresponding to each entered room dimension is unconfirmed.")
        if recognizable:
            recognizable = None
    if coverage < .8 or any(o.confidence == "high" and o.id not in retained for o in major):
        recognizable = False
    elif recognizable and unoriented:
        recognizable = None
    return FidelityReport(major_object_coverage=coverage, relationship_score=score({"wall", "facing", "adjacency", "support"}), proportion_score=score({"proportion"}), color_score=score({"color"}), recognizable=recognizable, checks=checks, unassessed=unassessed)
