"""Offline likeness checks using independent annotations, with no model calls.

Unlike production self-consistency checks this also detects furniture omitted by
photo analysis. Pass saved agent JSON plus a photo-case annotation to the CLI.
The annotations state approximate relationships, never invented metric truth.
"""

from dataclasses import replace
from math import cos, hypot, sin

from roometry.evaluation.schemas import EvaluationReport, ReconstructionCase
from roometry.evaluation.inventory import trace_inventory
from roometry.scenes.schemas import Opening, RoomScene, SceneObject
from roometry.scenes.geometry import object_bounds
from roometry.scenes.catalog import ASSET_CATALOG
from roometry.vision.assembly_context import assembly_features
from roometry.vision.schemas import RoomAnalysisResponse
from roometry.vision.proposal_schemas import SceneDraft, UnresolvedEntity
from roometry.vision.spatial import planar_gap


def evaluate_reconstruction(
    case: ReconstructionCase, analysis: RoomAnalysisResponse | None, scene: RoomScene | None,
    *, draft: SceneDraft | None = None, unresolved: list[UnresolvedEntity] | None = None,
    identity_matches: dict[str, str] | None = None,
    observation_matches: dict[str, str] | None = None,
    ambiguous_ids: frozenset[str] = frozenset(),
    component_matches: dict[str, str] | None = None,
) -> EvaluationReport:
    labels = {o.id: o.label.lower() for o in analysis.observations.objects} if analysis else {}
    items = [*scene.objects, *scene.openings] if scene else []
    matched, used, failures = {}, set(), []
    feature_checks, unassessed = {}, []
    component_matches = component_matches or {}
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

    def end_wall(item):
        """Use the footprint, preferring an oriented back in a corner tie.

        Twin beds can touch the same headboard wall and opposite side walls;
        their centers therefore do not identify the relevant room end.
        """
        if not isinstance(item, SceneObject) or item.wall:
            return nearest_wall(item)
        bounds = object_bounds(item).footprint
        xs, zs = bounds.project((1, 0)), bounds.project((0, 1))
        distances = {"west": xs[0], "east": scene.room.width_m - xs[1],
                     "north": zs[0], "south": scene.room.length_m - zs[1]}
        minimum = min(distances.values())
        if item.type in {"bed", "sofa", "armchair"}:
            back = {"north": cos(item.rotation_y_rad), "south": -cos(item.rotation_y_rad),
                    "west": sin(item.rotation_y_rad), "east": -sin(item.rotation_y_rad)}
            preferred = max(back, key=back.get)
            if back[preferred] >= cos(.7853981634) and distances[preferred] <= minimum + .05:
                return preferred
        return min(distances, key=distances.get)

    for expected in case.objects:
        if expected.id not in ambiguous_ids:
            feature_checks.update({f"{expected.id}:{feature}": False for feature in expected.required_features})
        if expected.id in component_matches:
            parent = next((o for o in items if o.id == component_matches[expected.id]), None)
            recipe = ASSET_CATALOG.get(parent.asset_id) if isinstance(parent, SceneObject) else None
            if (not expected.component_of or not recipe or not expected.component_part_names
                or not set(expected.component_part_names) <= {part.name for part in recipe.parts
                    if not part.placements or parent.placement in part.placements}
                or (identity_matches or {}).get(expected.component_of) != parent.id):
                raise ValueError("Integrated component evidence does not match its annotated host.")
            matched[expected.id] = parent
            continue  # A component does not inherit its parent's colors/proportions.
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
        if expected.id in ambiguous_ids:
            continue  # Inventory is distinct; identity-specific likeness is unassessed.
        if expected.required_features:
            candidates = [item] if isinstance(item, SceneObject) else []
            # A real supported insert may supply a feature; an unrelated fixture
            # elsewhere in the room or a label saying "sink" cannot supply it.
            candidates += [o for o in items if isinstance(o, SceneObject)
                           and o.type in {"sink", "cooker", "kitchen_counter", "vanity"}
                           and o.placement == "surface" and o.support_object_id == item.id]
            actual = {feature for obj in candidates for feature in assembly_features(replace(
                ASSET_CATALOG[obj.asset_id], parts=tuple(part for part in ASSET_CATALOG[obj.asset_id].parts
                    if not part.placements or obj.placement in part.placements)))}
            for feature in expected.required_features:
                key = f"{expected.id}:{feature}"
                feature_checks[key] = feature in actual
                if feature not in actual:
                    failures.append(f"{expected.id}: required {feature} feature is missing.")
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
        if relation.subject in ambiguous_ids or relation.target in ambiguous_ids:
            unassessed.append(f"{relation.subject} → {relation.target}: {relation.kind}; identity ambiguous.")
            continue
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
            left, right = end_wall(subject), end_wall(target)
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
    inventory = trace_inventory(
        case, analysis, scene, {key: value.id for key, value in matched.items()},
        draft, unresolved or [], observation_matches=observation_matches,
    )
    # An explicitly annotated subassembly shares its host observation, rather
    # than being reported as a missed standalone detection.
    by_ref = {entry.expected_id: entry for entry in inventory}
    for ref in case.objects:
        if ref.id in component_matches:
            parent = by_ref[ref.component_of]
            by_ref[ref.id] = parent.model_copy(update={"expected_id": ref.id})
    return EvaluationReport(
        case_id=case.id,
        passed=not failures and not ambiguous_ids,
        major_object_coverage=coverage,
        wall_relationships=score("wall"),
        orientation=score("orientation"),
        proportions=score("proportions"),
        color_consistency=score("color"),
        asset_match=score("asset"),
        failures=failures,
        matched_ids={key: value.id for key, value in matched.items()},
        inventory=list(by_ref.values()), unassessed_relationships=unassessed,
        feature_checks=feature_checks,
    )
