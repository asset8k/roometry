"""Deterministic fallback selection, using the authoritative assembly checks.

This is not a layout optimizer: small estimated floor-pose conflicts may recover
locally before deferral, never by resizing, reclassification or merging.
Observations and user measurements stay intact.
Every returned subset passes the same schema and geometry checks as a full scene.
"""

from collections import Counter

from roometry.scenes.schemas import RoomScene
from roometry.scenes.validation import GeometryReport
from roometry.vision.importance import importance, retention_importance
from roometry.vision.interpretations import resolve_interpretations
from roometry.vision.appearance import resolve_appearance
from roometry.vision.proposal_schemas import ProposalIssue, SceneDraft, UnresolvedEntity
from roometry.vision.proposals import Measurements, assemble, issue, notes, preflight
from roometry.vision.schemas import RoomAnalysisResponse


def recover_corrections(analysis, draft):
    """Quarantine rejected hypothesis groups, preserving the original evidence.

    Valid records for other entities still apply. All records for a rejected
    entity are removed together because placement/support revisions can depend
    on one another. No unvalidated interpretation is applied to a partial scene.
    """
    selected = draft.model_copy(deep=True)
    problems = []
    effective = analysis
    known_ids = {obj.id for obj in analysis.observations.objects}
    for field, resolver in (
        ("interpretation_corrections", resolve_interpretations),
        ("appearance_corrections", resolve_appearance),
    ):
        records = getattr(selected, field)
        # Every unsuccessful pass must remove at least one record group.
        for _ in range(len(records) + 1):
            revised, errors = resolver(effective, records)
            if not errors:
                effective = revised
                break
            invalid = {key for key, _ in errors}
            kept = [record for record in records if (record.entity_id or "room") not in invalid]
            if len(kept) == len(records):
                raise ValueError("Rejected corrections must identify their record group.")
            problems.append(issue("fidelity", "An invalid photo-hypothesis correction was ignored; the original evidence was retained.", *sorted(invalid & known_ids)))
            records = kept
        setattr(selected, field, records)
    return selected, effective, problems


def assemble_partial(
    analysis: RoomAnalysisResponse, measurements: Measurements, draft: SceneDraft, *, recover_local: bool = True, recovery_ids=None,
) -> tuple[RoomScene | None, GeometryReport | None, list[ProposalIssue], list[UnresolvedEntity]]:
    draft, effective, correction_issues = recover_corrections(analysis, draft)
    obs = effective.observations
    observed = {entity.id: entity for entity in [*obs.objects, *obs.openings]}
    object_ids = {obj.id for obj in obs.objects}
    unresolved: dict[str, UnresolvedEntity] = {}

    def defer(entity_id: str, problem: ProposalIssue) -> None:
        unresolved.setdefault(entity_id, UnresolvedEntity(
            entity_id=entity_id,
            kind="object" if entity_id in object_ids else "opening",
            reason=problem,
        ))

    for problem in preflight(effective):
        if not problem.entity_ids:
            return None, None, [problem], []
        for entity_id in problem.entity_ids:
            defer(entity_id, problem)

    # Invented or duplicate IDs make attribution ambiguous; fail closed. Missing
    # original IDs can be reported individually without discarding other items.
    for proposed, original in ((draft.objects, obs.objects), (draft.openings, obs.openings)):
        ids = [item.id for item in proposed]
        expected = {item.id for item in original}
        if len(ids) != len(set(ids)) or not set(ids) <= expected:
            return None, None, [issue(
                "identity_mismatch", "Use original observation IDs exactly once; invented or duplicate IDs cannot be reconstructed."
            )], []
        for missing in sorted(expected - set(ids)):
            defer(missing, issue("identity_mismatch", "This observation was missing from the scene draft.", missing))

    poses = {obj.id: obj for obj in draft.objects}
    dependents = Counter(obj.support_object_id for obj in obs.objects if obj.support_object_id)

    def defer_dependencies() -> None:
        # Propagate to a fixed point, independent of observation ordering.
        while True:
            before = len(unresolved)
            for obj in obs.objects:
                if obj.id in unresolved:
                    continue
                pose = poses.get(obj.id)
                dependencies = [obj.support_object_id]
                if pose is not None:
                    dependencies.extend([pose.support_object_id, pose.opening_id])
                missing = next((ref for ref in dependencies if ref in unresolved), None)
                if missing is not None:
                    defer(obj.id, issue(
                        "dependency_unresolved", "Its supporting object or linked opening is unresolved; resolve that observation first.", obj.id, missing,
                    ))
            if len(unresolved) == before:
                return

    def priority(entity_id: str) -> tuple[int, int, int, int, str]:
        source = observed[entity_id]
        return (
            retention_importance(source),
            {"high": 0, "medium": 1, "low": 2}[source.confidence],
            0 if getattr(source, "appears_fixed", False) else 1,
            -dependents[entity_id],
            entity_id,
        )

    # Each unsuccessful iteration defers at least one entity. At most 232
    # observations are admitted by the existing input contract.
    for _ in range(len(observed) + 1):
        defer_dependencies()
        remaining = set(observed) - unresolved.keys()
        reasons = [*correction_issues, *[item.reason for item in unresolved.values()]]
        if not remaining and unresolved:
            return None, None, reasons[:50], list(unresolved.values())
        # Internal deep copies only: the owned analysis cache and raw draft must
        # preserve all original observations, estimates and relationship IDs.
        subset = analysis.model_copy(deep=True)
        subset.observations.objects = [obj for obj in subset.observations.objects if obj.id in remaining]
        subset.observations.openings = [obj for obj in subset.observations.openings if obj.id in remaining]
        subset.observations.relationships = [r for r in subset.observations.relationships if all(key in remaining or key in {w.id for w in obs.walls} for key in (r.subject_id, r.target_id, *([r.alternative_target_id] if r.alternative_target_id else [])))]
        subset.observations.possible_duplicates = [
            pair for pair in subset.observations.possible_duplicates
            if all(entity_id not in unresolved for entity_id in pair.entity_ids)
        ]
        selected = draft.model_copy(deep=True)
        selected.objects = [obj for obj in selected.objects if obj.id in remaining]
        selected.estimate_corrections = [c for c in selected.estimate_corrections if c.entity_id in remaining]
        selected.interpretation_corrections = [c for c in selected.interpretation_corrections if c.entity_id in remaining]
        selected.appearance_corrections = [c for c in selected.appearance_corrections if c.entity_id is None or c.entity_id in remaining]
        selected.openings = [obj for obj in selected.openings if obj.id in remaining]
        scene, report, problems = assemble(subset, measurements, selected, require_fidelity=False,
                                          recover_local=recover_local, recovery_ids=recovery_ids)
        if scene is not None:
            if problems and not (scene.objects or scene.openings):
                return None, report, (reasons + problems)[:50], list(unresolved.values())
            if unresolved:
                major = [getattr(observed[key], "label", observed[key].type) for key in unresolved if importance(observed[key]) == 0]
                scene.warnings = notes(scene.warnings, [
                    *([("Important room elements remain unresolved: " + "; ".join(major))[:1000]] if major else []),
                    f"Partial reconstruction: {len(unresolved)} observed objects or openings are unresolved and omitted. Their observations are preserved separately; this is not a complete room."
                ])
            if problems:
                scene.warnings = notes(scene.warnings, [
                    "Provisional reconstruction: geometry passed, but some observed spatial relationships still need correction."
                ])
            if correction_issues:
                scene.warnings = notes(scene.warnings, [
                    "Some photo-hypothesis corrections were invalid and were ignored. Original evidence and other valid corrections were retained."
                ])
            return scene, report, (reasons + problems)[:50], list(unresolved.values())

        if report is not None and report.errors:
            foundational_codes = {
                "object_outside_room", "opening_outside_wall", "wall_attachment", "ceiling_attachment",
            }
            # Diagnose invalid supports before the secondary failures of objects
            # anchored to them. Resolve the whole known conflict set before
            # rechecking, rather than repeating all pairs after every omission.
            foundational = sorted(
                (error for error in report.errors if error.code in foundational_codes),
                key=lambda item: -dependents[item.entity_ids[0]],
            )
            other_errors = [error for error in report.errors if error.code not in foundational_codes]
            for error in [*foundational, *other_errors]:
                if any(entity_id in unresolved for entity_id in error.entity_ids):
                    continue
                affected = error.entity_ids[0]
                if error.code in {"object_overlap", "rug_overlap"}:
                    left, right = error.entity_ids
                    if poses[left].support_object_id == right:
                        affected = left
                    elif poses[right].support_object_id == left:
                        affected = right
                    else:
                        # Prefer stronger evidence, fixed items and major furniture, then supports,
                        # then stable IDs. This is provisional, not photo truth.
                        affected = max(error.entity_ids, key=priority)
                defer(affected, issue(
                    "geometry", f"{error.code}: {error.message} Deferred from the partial scene; its placement remains unresolved.", *error.entity_ids,
                ))
                defer_dependencies()
        else:
            scoped = [problem for problem in problems if problem.entity_ids]
            if not scoped or any(not problem.entity_ids for problem in problems):
                return None, report, (reasons + problems)[:50], list(unresolved.values())
            for problem in scoped:
                # Semantic checks identify the invalid entity first; subsequent
                # IDs explain its related support/opening and are not deletions.
                entity_id = problem.entity_ids[0]
                if entity_id not in remaining:
                    return None, report, (reasons + problems)[:50], list(unresolved.values())
                defer(entity_id, problem)
    raise RuntimeError("Partial scene selection did not make bounded progress.")
