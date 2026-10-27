"""Attribute omissions without logging photos, labels, prompts or model prose."""

from collections import Counter

from roometry.scenes.catalog import candidates
from roometry.scenes.schemas import RoomScene
from roometry.vision.importance import importance
from roometry.vision.proposal_schemas import SceneDraft, UnresolvedEntity
from roometry.vision.schemas import RoomAnalysisResponse


def omission_diagnostics(
    analysis: RoomAnalysisResponse, draft: SceneDraft | None,
    scene: RoomScene | None, unresolved: list[UnresolvedEntity],
) -> dict[str, object]:
    observations = {item.id: item for item in [*analysis.observations.objects, *analysis.observations.openings]}
    omitted = {item.entity_id: item for item in unresolved}
    proposed = {item.id: item for item in draft.objects} if draft else {}
    retained = {item.id for item in [*scene.objects, *scene.openings]} if scene else set()

    def roots(key: str, seen: frozenset[str] = frozenset()) -> set[str]:
        if key in seen:
            return {key}  # Tolerate historical or malformed dependency reports.
        item = omitted[key]
        if item.reason.code != "dependency_unresolved":
            return {key}
        parents = [ref for ref in item.reason.entity_ids if ref != key and ref in omitted]
        return set().union(*(roots(ref, seen | {key}) for ref in parents)) if parents else {key}

    stages = {
        "unknown_object": "classification", "unsupported_placement": "catalog_binding",
        "asset_mismatch": "catalog_binding", "identity_mismatch": "scene_draft",
        "draft_schema": "scene_draft", "support_mismatch": "support",
        "placement_mismatch": "attachment", "scene_schema": "assembly",
        "dimension_mismatch": "dimensions", "wall_mapping": "wall_assignment",
        "geometry": "geometry", "fidelity": "relationships",
        "dependency_unresolved": "dependency",
    }
    geometry_rules = {"object_outside_room", "object_overlap", "rug_overlap", "surface_support",
                      "wall_attachment", "ceiling_attachment", "opening_outside_wall", "wall_opening_overlap"}
    records = []
    for key, item in omitted.items():
        observation = observations.get(key)
        pose = proposed.get(key)
        object_type = getattr(observation, "type", None)
        placement = pose.placement if pose else getattr(observation, "placement", None)
        prefix = item.reason.message.partition(":")[0]
        records.append({
            "entity_id": key, "kind": item.kind,
            "object_type": object_type,
            "major": observation is not None and importance(observation) == 0,
            "stage": stages.get(item.reason.code, "assembly"), "code": item.reason.code,
            "geometry_rule": prefix if item.reason.code == "geometry" and prefix in geometry_rules else None,
            "asset_id": pose.asset_id if pose else None,
            "placement": placement,
            "catalog_type_models": len(candidates(object_type)) if item.kind == "object" and object_type else None,
            "catalog_placement_models": len(candidates(object_type, placement)) if item.kind == "object" and object_type else None,
            "related_entity_ids": item.reason.entity_ids,
            "root_entity_ids": sorted(roots(key)),
        })
    direct = [r for r in records if r["code"] != "dependency_unresolved"]
    return {
        "analysis_id": analysis.analysis_id,
        "observed": len(observations), "retained": len(retained), "unresolved": len(records),
        "direct_failures": len(direct), "dependent_failures": len(records) - len(direct),
        "unresolved_major": sum(r["major"] for r in records),
        "failure_stages": dict(Counter(r["stage"] for r in direct)),
        "entities": records,
    }
