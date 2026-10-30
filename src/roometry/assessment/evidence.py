"""Category-specific uncertainty and architectural surfaces, without model calls."""

from dataclasses import dataclass
import re

from roometry.scenes.scoring import STORAGE_TYPES
from roometry.vision.importance import importance
from roometry.vision.schemas import OpeningObservation
from roometry.vision.functional_groups import workstation_pairs

GEOMETRY_CATEGORIES = frozenset({"openness", "storage", "workspace"})
ARCHITECTURAL_SURFACE = re.compile(r"\b(window\s*sill|windowsill|window ledge|architectural ledge)\b", re.I)


@dataclass(frozen=True)
class AssessmentEvidence:
    uncertain_categories: frozenset[str] = frozenset()
    excluded_storage_ids: frozenset[str] = frozenset()
    missing_workspace: bool = False
    missing_storage: bool = False
    workspace_ids: frozenset[str] = frozenset()
    chair_pairs: tuple[tuple[str, str], ...] = ()
    workspace_devices: tuple[tuple[str, str], ...] = ()
    missing_floor_ids: frozenset[str] = frozenset()
    storage_ids: frozenset[str] = frozenset()


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
    desks = frozenset(o.id for o in observations.objects if o.type == 'desk')
    sources = {o.id:o for o in observations.objects}
    devices = []
    for obj in observations.objects:
        if obj.type not in {'monitor', 'computer', 'laptop'}:
            continue
        visited, key = set(), obj.support_object_id
        while key in sources and key not in visited:
            if key in desks:
                devices.append((obj.id,key))
                break
            visited.add(key)
            key = sources[key].support_object_id
    # Missing principal floor furniture cannot earn a high openness rating
    # merely because valid-subset assembly left its floor area empty.
    floor_ids = frozenset(item.entity_id for item in proposal.unresolved_entities if (
        (obj := sources.get(item.entity_id)) is not None and importance(obj) == 0
        and obj.placement in {'floor', None})) if proposal else frozenset()
    return AssessmentEvidence(frozenset(uncertain), excluded, missing_workspace, missing_storage,
        desks, tuple(sorted(workstation_pairs(analysis, observations.objects).items())), tuple(devices), floor_ids,
        frozenset(item.entity_id for item in proposal.unresolved_entities if item.entity_id in sources
                  and sources[item.entity_id].type in STORAGE_TYPES and item.entity_id not in excluded) if proposal else frozenset())


def evidence_for_run(run) -> AssessmentEvidence:
    return assessment_evidence(getattr(run, "analysis", None), getattr(run, "proposal", None))
