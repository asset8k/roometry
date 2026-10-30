"""Photo prerequisites for goal optimization, never a blanket partial-scene ban."""

from roometry.assessment.evidence import evidence_for_run
from roometry.assessment.workspace import belongs_to_workspace
from roometry.scenes.catalog import ASSET_CATALOG
from roometry.vision.features import retains_features
from roometry.vision.importance import importance
from roometry.vision.spatial import relationship_error, relationship_limit
import re


def integrated_shower_fixtures(sources, objects):
    """Recognize an already rendered head/mixer, without resolving an omitted ID.

    Only one photographed shower on the same known wall can establish this
    association. A fixed head cannot stand in for an observed handheld assembly.
    """
    represented = set()
    for fixture in sources.values():
        if (fixture.type != 'shower_fixture' or fixture.id in objects or fixture.wall_id is None
            or re.search(r'\b(handheld|hose|rail)\b',' '.join([fixture.label,*fixture.notes]),re.I)):
            continue
        hosts = [o for o in sources.values() if o.type == 'shower' and o.wall_id == fixture.wall_id]
        if len(hosts) != 1 or hosts[0].id not in objects:
            continue
        host = objects[hosts[0].id]
        if host.type != 'shower':
            continue
        parts = {p.name for p in ASSET_CATALOG[host.asset_id].parts}
        if {'shower_head','mixer'} <= parts:
            represented.add(fixture.id)
    return represented


def reconstruction_gaps(run, scene):
    if getattr(run,'analysis',None) is None:
        return []
    sources = {o.id:o for o in run.analysis.observations.objects}
    objects = {o.id:o for o in scene.objects}
    evidence = evidence_for_run(run)
    essential = {o.id for o in sources.values() if importance(o) == 0
                 and o.type not in {'wall_shelf', 'table_lamp', 'ceiling_light', 'chandelier'}}
    essential |= evidence.workspace_ids | {key for pair in (*evidence.chair_pairs, *evidence.workspace_devices) for key in pair}
    gaps = set(essential - objects.keys()) - integrated_shower_fixtures(sources,objects)
    for key in essential & objects.keys():
        if not retains_features(ASSET_CATALOG[objects[key].asset_id], sources[key], list(sources.values())):
            gaps.add(key)
    for chair,desk in evidence.chair_pairs:
        if chair in objects and desk in objects and any(relationship_error(scene,chair,desk,kind) > relationship_limit(scene,kind)
                                                      for kind in ('near','faces')):
            gaps.update((chair,desk))
    # A supported display must remain attached to its photographed workstation,
    # including the permitted countertop support level.
    for child,desk in evidence.workspace_devices:
        if not belongs_to_workspace(objects, child, desk):
            gaps.add(child)
    return [sources[key].label for key in sorted(gaps) if key in sources]


def readiness_message(gaps):
    names = '; '.join(gaps[:3])
    if len(gaps) > 3:
        names += f'; {len(gaps)-3} more'
    return (f'Complete these reconstructed features before improving the room: {names}. '
            'Correct existing positions in Edit layout, use chat to restore missing items, or create the room again.')[:900]
