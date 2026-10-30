"""Bounded draft upserts: restore observed entities without rewriting the room."""

from roometry.vision.errors import AnalysisFailure
from roometry.vision.proposal_schemas import ProposalIssue, SceneDraft
from roometry.vision.schemas import RoomAnalysisResponse
from roometry.scenes.assets import ASSET_REGISTRY
from roometry.scenes.catalog import candidates


def actionable_repair(analysis, draft, issues):
    """Do not pay again solely for unrepresentable or omitted loose accessories.

    This is only used after retaining valid partial geometry. Unknown/global,
    major-object, opening and placement failures always retain their repair path.
    Original observations remain explicit unresolved entries, never deletions.
    """
    objects = {o.id: o for o in analysis.observations.objects}
    present = {o.id for o in draft.objects}
    for problem in issues:
        if not problem.entity_ids or problem.code not in {"identity_mismatch", "unsupported_placement", "unknown_object"}:
            return True
        for key in problem.entity_ids:
            obj = objects.get(key)
            if obj is None or key in present:
                return True
            possible = candidates(obj.type, obj.placement) if obj.type else ()
            if obj.placement_confidence != 'high' and obj.type:
                possible = possible or candidates(obj.type)
            if obj.alternative_type and obj.confidence != 'high':
                possible = possible or candidates(obj.alternative_type)
            if not possible:
                continue
            if obj.type == 'household_accessory' and not obj.appears_fixed and problem.code == 'identity_mismatch':
                continue
            return True
    return False


def wall_repair_allowed(analysis: RoomAnalysisResponse, issues: list[ProposalIssue]) -> bool:
    walls = {item.id for item in analysis.observations.walls}
    return any(problem.code == "wall_mapping" or (
        problem.code == "fidelity" and problem.entity_ids and set(problem.entity_ids) <= walls
    ) for problem in issues)


def repair_scope(analysis: RoomAnalysisResponse, draft: SceneDraft, issues: list[ProposalIssue]) -> set[str]:
    expected = {item.id for item in [*analysis.observations.objects, *analysis.observations.openings]}
    present = {item.id for item in [*draft.objects, *draft.openings]}
    affected = (expected - present) | {key for problem in issues for key in problem.entity_ids if key in expected}
    # A global schema/wall failure has no reliable local attribution. The one
    # repair may reconstruct the draft, but still only from original identities.
    if wall_repair_allowed(analysis, issues) or any(not problem.entity_ids and not (problem.code == "identity_mismatch" and expected - present) for problem in issues):
        affected = expected.copy()
    from roometry.vision.functional_groups import reconstruction_group
    affected = reconstruction_group(analysis, draft.objects, affected)
    while True:
        previous = len(affected)
        for obj in draft.objects:
            if obj.support_object_id in affected or obj.opening_id in affected:
                affected.add(obj.id)
        if len(affected) == previous:
            return affected


def merge_repair(analysis: RoomAnalysisResponse, previous: SceneDraft, patch: SceneDraft,
                 issues: list[ProposalIssue], *, allow_room_appearance: bool = False) -> SceneDraft:
    """Omitted patch entries are unchanged, never deletions. No invented IDs."""
    allowed = repair_scope(analysis, previous, issues)
    expected_objects = {obj.id for obj in analysis.observations.objects}
    expected_openings = {obj.id for obj in analysis.observations.openings}
    observed_types = {obj.id:obj.type for obj in analysis.observations.objects}
    revised_types = {record.entity_id:record.revised_value for record in patch.interpretation_corrections if record.field == "type"}

    def reject():
        raise AnalysisFailure(502, "invalid_analysis", "The reconstruction repair changed protected or unobserved entities.")

    def upsert(old, new, expected):
        ids = [item.id for item in new]
        if len(ids) != len(set(ids)) or not set(ids) <= expected:
            reject()
        # A malformed first draft may contain invented IDs; they cannot become
        # scene identities. Original observations remain the only inventory.
        merged = {item.id: item for item in old if item.id in expected}
        for item in new:
            before = merged.get(item.id)
            if before is not None and hasattr(item, "asset_id") and item.placement == before.placement:
                # Null in an upsert means keep the existing model/size. Resetting
                # to a catalog default must not disguise an orientation repair.
                asset = ASSET_REGISTRY.get(before.asset_id)
                compatible = asset is not None and asset.object_type == revised_types.get(item.id,observed_types.get(item.id)) and item.placement in asset.placements
                item = item.model_copy(update={
                    "asset_id": item.asset_id or (before.asset_id if compatible else None),
                    "dimensions_m": item.dimensions_m or before.dimensions_m,
                })
            if item.id not in allowed and item != merged.get(item.id):
                reject()
            merged[item.id] = item
        return list(merged.values())

    wall_ids = {item.id for item in analysis.observations.walls}
    wall_failure = wall_repair_allowed(analysis, issues)
    if patch.wall_assignments and not wall_failure and patch.wall_assignments != previous.wall_assignments:
        reject()
    walls = patch.wall_assignments or previous.wall_assignments
    if {item.observation_id for item in walls} != wall_ids or len(walls) != len(wall_ids):
        reject()

    def corrections(old, new):
        merged = {(item.entity_id, item.field): item for item in old}
        keys = [(item.entity_id, item.field) for item in new]
        if len(keys) != len(set(keys)):
            reject()
        for record in new:
            key = record.entity_id, record.field
            if record.entity_id not in allowed and not (allow_room_appearance and record.entity_id is None) and record != merged.get(key):
                reject()
            merged[key] = record
        return list(merged.values())

    return SceneDraft.model_validate({
        **previous.model_dump(),
        "wall_assignments": [item.model_dump() for item in walls],
        "objects": [item.model_dump() for item in upsert(previous.objects, patch.objects, expected_objects)],
        "openings": [item.model_dump() for item in upsert(previous.openings, patch.openings, expected_openings)],
        "estimate_corrections": [item.model_dump() for item in corrections(previous.estimate_corrections, patch.estimate_corrections)],
        "interpretation_corrections": [item.model_dump() for item in corrections(previous.interpretation_corrections, patch.interpretation_corrections)],
        "appearance_corrections": [item.model_dump() for item in corrections(previous.appearance_corrections, patch.appearance_corrections)],
        "assumptions": list(dict.fromkeys([*previous.assumptions, *patch.assumptions]))[:50],
        "warnings": list(dict.fromkeys([*previous.warnings, *patch.warnings]))[:50],
    })
