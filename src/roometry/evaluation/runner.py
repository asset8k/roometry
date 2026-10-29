"""Offline likeness checks using independent annotations, with no model calls.

Unlike production self-consistency checks this also detects furniture omitted by
photo analysis. Pass saved agent JSON plus a photo-case annotation to the CLI.
The annotations state approximate relationships, never invented metric truth.
"""

from math import cos, hypot, sin

from roometry.evaluation.schemas import EvaluationReport, ReconstructionCase
from roometry.evaluation.inventory import trace_inventory
from roometry.scenes.schemas import Opening, RoomScene, SceneObject
from roometry.vision.schemas import RoomAnalysisResponse
from roometry.vision.proposal_schemas import SceneDraft, UnresolvedEntity
from roometry.vision.spatial import planar_gap


def evaluate_reconstruction(
    case: ReconstructionCase, analysis: RoomAnalysisResponse | None, scene: RoomScene | None,
    *, draft: SceneDraft | None = None, unresolved: list[UnresolvedEntity] | None = None,
    identity_matches: dict[str, str] | None = None,
    observation_matches: dict[str, str] | None = None,
) -> EvaluationReport:
    labels = {o.id: o.label.lower() for o in analysis.observations.objects} if analysis else {}
    items = [*scene.objects, *scene.openings] if scene else []
    matched, used, failures = {}, set(), []
    metrics: dict[str, list[bool]] = {
        key: [] for key in ("wall", "orientation", "proportions", "color", "asset")
    }

    def record(metric, result, message):
        metrics[metric].append(bool(result))
        if not result:
            failures.append(message)

    def primary_color_distance(a, b):
        return (
            sum((int(a[i : i + 2], 16) - int(b[i : i + 2], 16)) ** 2 for i in (1, 3, 5))
            ** 0.5
        )

    def point(item: SceneObject | Opening):
        assert scene is not None
        if isinstance(item, SceneObject):
            return item.position.x, item.position.z
        t = item.offset_m + item.width_m / 2
        return {
            "north": (t, 0),
            "south": (t, scene.room.length_m),
            "west": (0, t),
            "east": (scene.room.width_m, t),
        }[item.wall]

    def nearest_wall(item: SceneObject | Opening):
        assert scene is not None
        if item.wall:
            return item.wall
        x, z = point(item)
        return min(
            {
                "west": x,
                "east": scene.room.width_m - x,
                "north": z,
                "south": scene.room.length_m - z,
            },
            key=lambda key: {
                "west": x,
                "east": scene.room.width_m - x,
                "north": z,
                "south": scene.room.length_m - z,
            }[key],
        )

    for expected in case.objects:
        options = [
            o
            for o in items
            if o.id not in used
            and (
                o.type == expected.opening_type
                if expected.opening_type
                else o.type in expected.types
            )
            and (
                identity_matches is not None or not expected.label_terms
                or any(
                    term.lower() in labels.get(o.id, "")
                    for term in expected.label_terms
                )
            )
        ]
        if identity_matches is not None:
            options = [item for item in options if item.id == identity_matches.get(expected.id)]
        if not options:
            failures.append(
                f"Missing expected anchor: {expected.id} (including objects photo analysis omitted)."
            )
            continue
        # Stable unique matching; annotations can use label_terms to distinguish
        # similar items. No observation IDs are prescribed to a live model.
        item = min(options, key=lambda o: o.id)
        matched[expected.id] = item
        used.add(item.id)
        if expected.color and isinstance(item, SceneObject):
            record(
                "color",
                primary_color_distance(item.color, expected.color) <= 90,
                f"{expected.id}: primary color differs from the photo annotation.",
            )
        for role, color in expected.material_colors.items():
            actual = (
                getattr(item.material_colors, role, None)
                if isinstance(item, SceneObject) and item.material_colors
                else None
            )
            record(
                "color",
                actual is not None and primary_color_distance(actual, color) <= 90,
                f"{expected.id}: {role} material color is missing or inconsistent.",
            )
        if expected.allowed_assets and isinstance(item, SceneObject):
            record(
                "asset",
                item.asset_id in expected.allowed_assets,
                f"{expected.id}: model form does not match the annotated object.",
            )
        if expected.width_depth_ratio and isinstance(item, SceneObject):
            low, high = expected.width_depth_ratio
            record(
                "proportions",
                low <= item.dimensions_m.width / item.dimensions_m.depth <= high,
                f"{expected.id}: width/depth proportion differs from the annotation.",
            )
        if expected.wall_span_fraction and isinstance(item, SceneObject):
            assert scene is not None
            wall = nearest_wall(item)
            w, d = item.dimensions_m.width, item.dimensions_m.depth
            c, s = abs(cos(item.rotation_y_rad)), abs(sin(item.rotation_y_rad))
            span = c * w + s * d if wall in {"north", "south"} else s * w + c * d
            fraction = span / (
                scene.room.width_m
                if wall in {"north", "south"}
                else scene.room.length_m
            )
            low, high = expected.wall_span_fraction
            record(
                "proportions",
                low <= fraction <= high,
                f"{expected.id}: approximate wall coverage differs from the annotation.",
            )
    if case.room_finishes:
        for role in ("floor_color", "wall_color"):
            expected = getattr(case.room_finishes, role)
            actual = getattr(scene.finishes, role, None) if scene and scene.finishes else None
            if expected:
                record(
                    "color",
                    actual is not None
                    and primary_color_distance(actual, expected) <= 90,
                    f"Room {role} differs from the photo annotation.",
                )
        if case.room_finishes.floor_pattern:
            record(
                "color",
                scene is not None and scene.finishes is not None
                and scene.finishes.floor_pattern == case.room_finishes.floor_pattern,
                "Room floor pattern differs from the photo annotation.",
            )
    opposite = {"north": "south", "south": "north", "west": "east", "east": "west"}
    for relation in case.relationships:
        subject, target = matched.get(relation.subject), matched.get(relation.target)
        metric = "orientation" if relation.kind == "faces" else "wall"
        if subject is None or target is None:
            record(
                metric,
                False,
                f"{relation.subject} → {relation.target}: cannot check {relation.kind}; an anchor is missing.",
            )
            continue
        a, b = point(subject), point(target)
        dx, dz = b[0] - a[0], b[1] - a[1]
        if relation.kind == "faces":
            passed = (
                isinstance(subject, SceneObject)
                and hypot(dx, dz) > 0.05
                and (
                    sin(subject.rotation_y_rad) * dx + cos(subject.rotation_y_rad) * dz
                )
                / hypot(dx, dz)
                >= cos(0.7853981634)
            )
        elif relation.kind == "near":
            assert scene is not None
            passed = planar_gap(subject.id, target.id, scene, {}) <= max(
                0.8, 0.15 * min(scene.room.width_m, scene.room.length_m)
            )
        else:
            left, right = nearest_wall(subject), nearest_wall(target)
            passed = (
                left == right
                if relation.kind == "same_end"
                else opposite[left] == right
            )
        record(
            metric,
            passed,
            f"{relation.subject} → {relation.target}: {relation.kind} relationship failed.",
        )

    def score(key):
        values = metrics[key]
        return sum(values) / len(values) if values else None

    coverage = len(matched) / len(case.objects)
    return EvaluationReport(
        case_id=case.id,
        passed=not failures,
        major_object_coverage=coverage,
        wall_relationships=score("wall"),
        orientation=score("orientation"),
        proportions=score("proportions"),
        color_consistency=score("color"),
        asset_match=score("asset"),
        failures=failures,
        matched_ids={key: value.id for key, value in matched.items()},
        inventory=trace_inventory(
            case, analysis, scene, {key: value.id for key, value in matched.items()},
            draft, unresolved or [],
            observation_matches=observation_matches,
        ),
    )
