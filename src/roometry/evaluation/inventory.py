"""Locate losses between photo observations, catalog binding and scene assembly.

The independent case inventory also exposes missed detections. A missing raw
draft remains unassessed; a retained scene never proves every draft was complete.
"""

from roometry.evaluation.schemas import InventoryLossStage, InventoryTrace, ReconstructionCase
from roometry.scenes.catalog import candidates
from roometry.scenes.schemas import RoomScene
from roometry.vision.proposal_schemas import SceneDraft, UnresolvedEntity
from roometry.vision.schemas import ObjectObservation, RoomAnalysisResponse


def trace_inventory(
    case: ReconstructionCase,
    analysis: RoomAnalysisResponse | None,
    scene: RoomScene | None,
    matched_ids: dict[str, str],
    draft: SceneDraft | None,
    unresolved: list[UnresolvedEntity],
) -> list[InventoryTrace]:
    observed = (
        [*analysis.observations.objects, *analysis.observations.openings]
        if analysis else []
    )
    retained = {item.id for item in [*scene.objects, *scene.openings]} if scene else set()
    proposed = {item.id for item in [*draft.objects, *draft.openings]} if draft else None
    reasons = {item.entity_id: item.reason.code for item in unresolved}
    used: set[str] = set()
    result: list[InventoryTrace] = []
    for expected in case.objects:
        options = [
            item for item in observed
            if item.id not in used
            and (item.type == expected.opening_type if expected.opening_type else item.type in expected.types)
            and (not expected.label_terms or any(
                term.lower() in getattr(item, "label", "").lower()
                for term in expected.label_terms
            ))
        ]
        # Prefer the final matching for interchangeable instances, then a stable
        # observed ID. Never count the same observed item as two expected ones.
        source = min(options, key=lambda item: (item.id != matched_ids.get(expected.id), item.id)) if options else None
        if source is not None:
            used.add(source.id)
        compatible = None
        if source is not None:
            compatible = (
                source.type is not None and bool(candidates(source.type, source.placement))
                if isinstance(source, ObjectObservation) else True
            )
        drafted = source.id in proposed if source is not None and proposed is not None else None
        present = (source is not None and source.id in retained) or matched_ids.get(expected.id) in retained
        code = reasons.get(source.id) if source is not None else None
        loss: InventoryLossStage | None = None
        if not present:
            if analysis is None:
                loss = "analysis_unavailable"
            elif source is None:
                loss = "analysis"
            elif compatible is False:
                loss = "catalog"
            elif drafted is False or code == "identity_mismatch":
                loss = "construction"
            elif code is not None or drafted is True:
                loss = "validation"
            else:
                loss = "construction_or_validation"
        result.append(InventoryTrace(
            expected_id=expected.id,
            observed_id=source.id if source is not None else None,
            detected=source is not None if analysis is not None else None,
            catalog_compatible=compatible,
            drafted=drafted,
            retained=present,
            loss_stage=loss,
            issue_codes=[code] if code is not None else [],
        ))
    return result
