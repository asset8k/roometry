"""Deterministic reconstruction checks and bounded improvement proxies.

Practical scores are recomputed from geometry, not copied from model narratives.
They are planning proxies; independent visual review remains a separate layer.
"""

import json

from roometry.assessment.metrics import measure_room
from roometry.optimization.inventory import utility_issues
from roometry.scenes.clearance_acceptance import clearance_regressions
from roometry.scenes.hashing import scene_hash
from roometry.scenes.schemas import RoomScene, SceneObject
from roometry.scenes.validation import validate_geometry


MAJOR_TYPES = frozenset({
    "bed", "sofa", "armchair", "chair", "stool", "bench", "ottoman", "desk",
    "table", "coffee_table", "side_table", "nightstand", "wardrobe", "dresser",
    "shelf", "tv_stand", "sideboard", "tv", "refrigerator", "cooker",
    "dishwasher", "washing_machine", "dryer", "bathtub", "shower", "vanity", "sink", "toilet",
})
MODULE_TYPES = frozenset({"kitchen_counter", "kitchen_cabinet", "wall_cabinet", "ceiling_light", "chandelier"})


def average(values):
    return sum(values) / len(values) if values else None


def nearest_wall(item, scene):
    if item.wall:
        return item.wall
    x, z = item.position.x, item.position.z
    return min({"west": x, "east": scene.room.width_m - x,
                "north": z, "south": scene.room.length_m - z},
               key={"west": x, "east": scene.room.width_m - x,
                    "north": z, "south": scene.room.length_m - z}.get)


def check_measurements(entry, raw, scene):
    supplied = (entry.get("input", {}).get("dimensions") or {}).copy()
    confirmed = raw.get("confirmed_dimensions") or {}
    issues = []
    for key in ("width_m", "length_m", "height_m"):
        value = supplied.get(key)
        answer = confirmed.get(key)
        if value is not None and answer is not None and abs(value - answer) > 1e-6:
            issues.append(f"confirmed_measurement_changed:{key}")
        if value is None:
            value = answer
        if value is not None and (scene is None or abs(getattr(scene.room, key) - value) > 1e-6):
            issues.append(f"supplied_measurement_changed:{key}")
    if not supplied and not confirmed and scene and scene.room.measurements_source == "user_confirmed":
        issues.append("unconfirmed_estimate_claimed_as_user_measurement")
    return issues


def reconstruction_metrics(entry, reference, raw, scene, analysis, identities, likeness, verified_visible_extras=None):
    visual = {item["id"]: item for item in entry.get("visual_reference", {}).get("entities", [])}
    major = [obj for obj in reference.objects if visual.get(obj.id, {}).get("importance", "major") == "major"]
    group_issues = [f"incomplete_fixture_group:{key}" for key, observed in identities.observed_groups.items()
                    if not observed or set(observed) != set(identities.groups.get(key, ()))]
    coverage = sum((obj.id in identities.final or obj.id in identities.components)
                   and f"incomplete_fixture_group:{obj.id}" not in group_issues
                   for obj in major) / len(major) if major else None
    geometry = validate_geometry(scene) if scene else None
    doors = geometry.door_clearance.doors if geometry and geometry.door_clearance else ()
    access = [door.access_status == "clear" for door in doors if door.access_status != "unassessed"]
    swings = [door.swing_status == "clear" for door in doors if door.swing_status in {"clear", "blocked"}]
    items = {obj.id: obj for obj in [*scene.objects, *scene.openings]} if scene else {}
    support_checks, support_issues = [], []
    for key, item in visual.items():
        host = item.get("support_object_id")
        if not host:
            continue
        if key in identities.ambiguous or host in identities.ambiguous:
            continue
        if key in identities.components:
            supported = identities.components[key] == identities.final.get(host)
            support_checks.append(supported)
            if not supported:
                support_issues.append(f"support_relationship:{key}:{host}")
            continue
        child, parent = items.get(identities.final.get(key)), identities.final.get(host)
        supported = isinstance(child, SceneObject) and parent is not None and child.placement == "surface" and child.support_object_id == parent
        support_checks.append(supported)
        if not supported:
            support_issues.append(f"support_relationship:{key}:{host}")
    # Wall observations provide a consistency check, not independent proof that
    # the analyzer interpreted the original photograph correctly.
    wall_checks = []
    mapped = {item.observation_id: item.wall for item in scene.wall_mapping} if scene else {}
    observations = {item.id: item for item in [*analysis.observations.objects, *analysis.observations.openings]} if analysis else {}
    for key, observed_id in identities.observations.items():
        observed = observations[observed_id]
        target = mapped.get(observed.wall_id)
        item = items.get(identities.final.get(key))
        if target and item and key not in identities.ambiguous:
            wall_checks.append(nearest_wall(item, scene) == target)

    unmatched = [obj for obj in scene.objects if obj.id not in identities.final.values()] if scene else []
    # Cabinet runs and grouped lights are not one-to-one meshes. Their extra
    # parts go to visual review instead of becoming invented-furniture errors.
    modules = [obj.id for obj in unmatched if obj.type in MODULE_TYPES]
    extra_candidates = {obj.id for obj in unmatched if obj.type in MAJOR_TYPES}
    verified_visible_extras = verified_visible_extras or {}
    if not set(verified_visible_extras) <= extra_candidates:
        raise ValueError("Visible-extra review must identify unmatched retained furniture.")
    extras = sorted(extra_candidates - set(verified_visible_extras))
    eligible = [obj for obj in scene.objects if obj.type in MAJOR_TYPES] if scene else []
    precision = (len(eligible) - len(extras)) / len(eligible) if eligible else None
    # If identity is not established, do not fabricate a spatial score from an
    # arbitrary pairing. Coverage still counts distinct compatible inventory.
    wall_score = likeness.wall_relationships
    facing_score = likeness.orientation
    measurements = check_measurements(entry, raw, scene)
    hard = ([issue.code for issue in geometry.errors] if geometry else ["no_scene"]) + measurements
    semantic = support_issues + group_issues + [f"missing_functional_feature:{key}"
                    for key, passed in likeness.feature_checks.items() if not passed]
    if coverage is None or coverage < .9:
        semantic.append("major_object_coverage_below_0.90")
    if wall_score is not None and wall_score < .85:
        semantic.append("wall_relationships_below_0.85")
    if facing_score is not None and facing_score < .85:
        semantic.append("orientation_below_0.85")
    if extras:
        semantic.append("unsupported_extra_major_objects")
    terminal = raw.get("status") in {"completed", "partial"}
    usable = terminal and not hard and not semantic
    completion = ("complete" if usable and raw.get("status") == "completed" and coverage == 1
                  else "usable_partial" if usable else "failed" if not terminal else "unusable")
    return {
        "completion": completion, "usable": usable,
        "schema_valid": scene is not None,
        "geometry_valid": geometry.valid if geometry else None,
        "collision_free": not any(issue.code == "object_overlap" for issue in geometry.errors) if geometry else None,
        "door_access_clear": all(access) if access else None,
        "known_door_swings_clear": all(swings) if swings else None,
        "unknown_door_swings": sum(door.swing_status == "unknown" for door in doors),
        "major_object_coverage": coverage,
        "major_inventory_precision_proxy": precision,
        "unsupported_extra_major_objects": sorted(extras),
        "photo_verified_visible_extras": verified_visible_extras,
        "grouped_parts_for_visual_review": sorted(modules),
        "ambiguous_reference_ids": sorted(identities.ambiguous),
        "unassessed_relationships": likeness.unassessed_relationships,
        "integrated_components": identities.components,
        "fixture_groups": {key: {"observed": list(identities.observed_groups[key]), "retained": list(members)}
                           for key, members in identities.groups.items()},
        "functional_features": likeness.feature_checks,
        "wall_relationships": wall_score, "orientation": facing_score,
        "observation_wall_consistency": average(wall_checks),
        "support_relationships": average(support_checks),
        "proportions": likeness.proportions,
        "color_consistency": likeness.color_consistency,
        "asset_match": likeness.asset_match,
        "hard_issues": hard, "fidelity_issues": semantic,
        "door_checks": geometry.door_clearance.model_dump(mode="json") if geometry and geometry.door_clearance else None,
        "walking_routes": geometry.walking_space.status if geometry and geometry.walking_space else None,
        "geometry_warnings": [issue.code for issue in geometry.warnings] if geometry else [],
        "visual_review_required": True,
    }


def _physical(card):
    return {item.key: item.value for item in card.metrics}


def evaluate_improvement(original: RoomScene | None, raw: dict, focus: str):
    """Inspect the retained result even if the producer's outer DTO is invalid."""
    source = RoomScene.model_validate_json(json.dumps(raw["source_scene"]))
    result = RoomScene.model_validate_json(json.dumps(raw["result_scene"])) if raw.get("result_scene") else None
    issues = []
    if original is None or scene_hash(source) != scene_hash(original):
        issues.append("source_does_not_match_recorded_original")
    if raw.get("source_scene_hash") not in {None, scene_hash(source)}:
        issues.append("source_hash_mismatch")
    if raw.get("focus") != focus:
        issues.append("wrong_improvement_goal")
    if not result:
        return {"accepted": False, "status": raw.get("status"), "focus": focus,
                "hard_issues": issues, "meaningful_proxy_gain": False,
                "source_preserved": not issues, "changed_objects": [],
                "visual_review_required": True}
    if source.room != result.room or source.openings != result.openings:
        issues.append("room_or_openings_changed")
    by_id = {obj.id: obj for obj in result.objects}
    protected = [obj.id for obj in source.objects if (obj.fixed or obj.locked) and by_id.get(obj.id) != obj]
    if protected:
        issues.append("fixed_or_locked_objects_changed")
    before_geometry, after_geometry = validate_geometry(source), validate_geometry(result)
    issues.extend(issue.code for issue in after_geometry.errors)
    issues.extend(issue.code for issue in clearance_regressions(before_geometry, after_geometry))
    issues.extend(utility_issues(source, result))
    before, after = measure_room(source), measure_room(result)
    old, new = getattr(before, focus), getattr(after, focus)
    a, b = _physical(old), _physical(new)
    score_gain = new.score is not None and (old.score is not None and new.score >= old.score + .5 - 1e-8)
    if old.score is None:
        score_gain = focus in {"workspace", "storage"} and new.score is not None and new.score >= 6
    if focus == "openness":
        rank = {"unassessed": 0, "blocked": 0, "narrow": 1, "clear": 2}
        physical_gain = (b.get("main_walking_center_area", 0) >= a.get("main_walking_center_area", 0) + .5 - 1e-8
                         or rank[after_geometry.walking_space.status] > rank[before_geometry.walking_space.status])
    elif focus == "storage":
        physical_gain = (b.get("accessible_storage_fraction", 0) > a.get("accessible_storage_fraction", 0) + .05
                         or b.get("nominal_storage_volume", 0) >= a.get("nominal_storage_volume", 0) + max(.15, .1 * a.get("nominal_storage_volume", 0)) - 1e-8)
    elif focus == "workspace":
        physical_gain = b != a and score_gain
    else:
        physical_gain = None  # Taste and visual cohesion require the chat judge.
    original_ids = {obj.id: obj for obj in source.objects}
    changed = sorted(key for key in set(original_ids) | set(by_id) if original_ids.get(key) != by_id.get(key))
    if not changed:
        issues.append("no_scene_change")
    accepted = raw.get("status") == "completed" and not issues
    return {
        "accepted": accepted, "status": raw.get("status"), "focus": focus,
        "source_preserved": original is not None and scene_hash(source) == scene_hash(original),
        "protected_objects_changed": protected, "changed_objects": changed,
        "hard_issues": issues,
        "geometry_valid": after_geometry.valid,
        "clearance_preserved": not clearance_regressions(before_geometry, after_geometry),
        "locked_object_preservation": not protected,
        "before_proxy_score": old.score, "after_proxy_score": new.score,
        "proxy_score_gain": round(new.score - old.score, 4) if old.score is not None and new.score is not None else None,
        "physical_metrics_before": a, "physical_metrics_after": b,
        "meaningful_proxy_gain": bool(score_gain and physical_gain) if physical_gain is not None else None,
        "tradeoffs": {key: round(getattr(after, key).score - getattr(before, key).score, 4)
                      for key in ("openness", "storage", "workspace") if key != focus
                      and getattr(before, key).score is not None and getattr(after, key).score is not None},
        "visual_review_required": True,
    }
