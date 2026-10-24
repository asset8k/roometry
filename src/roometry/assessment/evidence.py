"""Category-specific uncertainty and architectural surfaces, without model calls."""

from dataclasses import dataclass
import re

from roometry.scenes.scoring import STORAGE_TYPES
from roometry.vision.importance import importance
from roometry.vision.schemas import OpeningObservation

GEOMETRY_CATEGORIES = frozenset({"openness", "storage", "workspace"})
ARCHITECTURAL_SURFACE = re.compile(r"\b(window\s*sill|windowsill|window ledge|architectural ledge)\b", re.I)


@dataclass(frozen=True)
class AssessmentEvidence:
    uncertain_categories: frozenset[str] = frozenset()
    excluded_storage_ids: frozenset[str] = frozenset()
    missing_workspace: bool = False
    missing_storage: bool = False


def assessment_evidence(analysis=None, proposal=None) -> AssessmentEvidence:
    if analysis is None:
        return AssessmentEvidence()
    observations = analysis.observations
    excluded = frozenset(o.id for o in observations.objects
                         if o.type in STORAGE_TYPES and ARCHITECTURAL_SURFACE.search(o.label))
    observed = {o.id: o for o in [*observations.objects, *observations.openings]}
    uncertain = set()
    missing_workspace = missing_storage = False
    for item in proposal.unresolved_entities if proposal else ():
        obj = observed.get(item.entity_id)
        if obj is None:
            uncertain.update(GEOMETRY_CATEGORIES)
        elif isinstance(obj, OpeningObservation):
            if obj.type == "door":
                uncertain.update(GEOMETRY_CATEGORIES)  # Unknown entrance/access.
        else:
            missing_workspace |= obj.type == "desk"
            missing_storage |= obj.type in STORAGE_TYPES and obj.id not in excluded
            if obj.type == "desk" or obj.type in {"chair", "stool"}:
                uncertain.add("workspace")
            if obj.type in STORAGE_TYPES and obj.id not in excluded:
                uncertain.add("storage")
            if obj.type is None or (obj.placement in {"floor", None} and importance(obj) <= 1):
                uncertain.update(GEOMETRY_CATEGORIES)
            # A missing wall decoration/door leaf does not erase known entrance
            # geometry or invalidate independently visible photo aesthetics.
    if proposal and set(proposal.estimated_axes) & {"width_m", "length_m"}:
        uncertain.update(GEOMETRY_CATEGORIES)
    return AssessmentEvidence(frozenset(uncertain), excluded, missing_workspace, missing_storage)


def evidence_for_run(run) -> AssessmentEvidence:
    return assessment_evidence(getattr(run, "analysis", None), getattr(run, "proposal", None))
