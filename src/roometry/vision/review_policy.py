"""Bounded review coverage, catalog controls and measurable repair objectives.

These checks measure progress toward a photo-backed review finding, not whether
the model's interpretation of a photo is correct. Geometry remains independent.
"""

from math import cos, hypot, inf, isclose, sin

from roometry.scenes.assets import ASSET_REGISTRY
from roometry.scenes.catalog import ASSET_CATALOG, candidates
from roometry.vision.context_budget import shortlist
from roometry.vision.features import retains_features
from roometry.vision.importance import importance
from roometry.vision.review_render import material_color
from roometry.vision.spatial import planar_bounds, planar_gap, relationship_error, relationship_limit, POSITION_TOLERANCE_M, FACING_TOLERANCE_DEG

MAX_REVIEW_ENTITIES = 24
MAX_REPAIR_FINDINGS = 6
MAX_RELATION_COMPARISONS = 24
MAX_MATERIAL_COMPARISONS = 32
LAYOUT_KINDS = {"position", "orientation", "opening", "support", "proportion", "identity"}


def finding_priority(finding, sources):
    ranks = [importance(sources[key]) for key in finding.entity_ids if key in sources]
    rank = min(ranks, default=2)
    phase = (0 if finding.kind == "missing" and rank <= 1 else
             1 if finding.kind in LAYOUT_KINDS and rank <= 1 else
             2 if finding.kind == "color" else 3)
    return phase, rank, finding.severity != "major"


def same_values(left, right):
    """Ignore only sub-micrometer arithmetic noise, never entity identity."""
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(same_values(value,right[key]) for key,value in left.items())
    if isinstance(left, (list,tuple)) and isinstance(right, (list,tuple)):
        return len(left) == len(right) and all(same_values(a,b) for a,b in zip(left,right))
    if type(left) in {int,float} and type(right) in {int,float}:
        return isclose(left,right,rel_tol=0,abs_tol=1e-6)
    return left == right


def appearance_controls(obj):
    parts = [part for part in ASSET_CATALOG[obj.asset_id].parts if not part.placements or obj.placement in part.placements]
    roles = {part.material for part in parts}
    controls = {role: role for role in roles & {"wood", "metal", "linen", "inset", "rubber", "enamel"}}
    if roles & {"primary", "primary_dark", "primary_metal", "primary_ceramic"}:
        controls["color"] = "primary"
    result = {
        field: {"value": obj.color if field == "color" else material_color(obj, role),
                "parts": [part.name for part in parts if part.material == role or (field == "color" and part.material.startswith("primary"))][:16]}
        for field, role in sorted(controls.items())
    }
    for field, control in result.items():
        names = control["parts"]
        if "seat" in names:
            region = "seat and upholstery panels"
        elif "high_back" in names:
            region = "back panel"
        elif any(name in {"tabletop", "worktop"} for name in names):
            region = "work surface"
        elif any(name.startswith(("board_", "side_board_", "shelf_")) for name in names):
            region = "shelf boards"
        else:
            region = {"color": "main surfaces", "metal": "metal frame", "wood": "wood surfaces", "inset": "contrasting panels", "rubber": "armrests and feet", "linen": "fabric", "enamel": "enamel surfaces"}[field]
        control["region"] = region
    return result


def review_targets(analysis, scene):
    observed = [item for item in [*analysis.observations.openings, *analysis.observations.objects] if importance(item) <= 1]
    objects = {obj.id: obj for obj in scene.objects}
    openings = {item.id: item for item in scene.openings}
    observed.sort(key=lambda item: (importance(item), item.id in objects or item.id in openings,
                                    item.confidence == "low", item.id))
    walls = {item.observation_id: item.wall for item in scene.wall_mapping}
    targets = []
    for source in observed[:MAX_REVIEW_ENTITIES]:
        obj = objects.get(source.id)
        targets.append({
            "entity_id": source.id, "label": getattr(source, "label", source.type),
            "kind": "opening" if source.id in {item.id for item in analysis.observations.openings} else "object",
            "source_image_ids": source.source_image_ids, "retained": source.id in objects or source.id in openings,
            "appearance_controls": appearance_controls(obj) if obj is not None else {},
            "restoration_models": restoration_models(analysis, source) if obj is None else [],
            "planar_bounds_m": planar_bounds(source.id, scene, walls),
            "opening_gaps_m": {key: round(planar_gap(source.id, key, scene, walls), 3) for key in openings} if obj is not None else {},
        })
    targets.append({"entity_id": None, "kind": "room", "label": "Room finishes",
                    "source_image_ids": analysis.observations.image_ids,
                    "appearance_controls": scene.finishes.model_dump() if scene.finishes else {}})
    relation_budget, material_budget = MAX_RELATION_COMPARISONS, MAX_MATERIAL_COMPARISONS
    by_id = {item.id: item for item in observed}
    for target in targets:
        key = target["entity_id"]
        source = by_id.get(key)
        relations = []
        if source is not None:
            for relation in analysis.observations.relationships:
                if relation.subject_id == key and relation.alternative_target_id is None and relation.kind in {"near", "beside", "faces", "against_wall"}:
                    relations.append((relation.target_id, "near" if relation.kind == "beside" else relation.kind))
            # These are questions for the photos, never inferred facts. Probe
            # local groups even when the initial analysis missed a relationship.
            if getattr(source, "type", None) in {"desk", "shelf", "radiator"}:
                relations.extend((opening.id, "near") for opening in sorted(analysis.observations.openings, key=lambda item: item.id)
                                 if opening.type == "window" and set(source.source_image_ids) & set(opening.source_image_ids))
            facing_type = {"chair": "desk", "sofa": "tv", "armchair": "tv"}.get(getattr(source, "type", None))
            peers = [item for item in analysis.observations.objects if item.type == facing_type and facing_type and set(item.source_image_ids) & set(source.source_image_ids)]
            if len(peers) == 1 and not any(kind == "faces" for _, kind in relations):
                relations.append((peers[0].id, "faces"))
        target["relation_probes"] = []
        for target_id, kind in list(dict.fromkeys(relations))[:min(6, relation_budget)]:
            error = relationship_error(scene, key, target_id, kind)
            if error == inf:
                continue
            target["relation_probes"].append({"target_id": target_id, "relation": kind,
                "candidate_error": round(error, 3), "unit": "degrees" if kind == "faces" else "meters",
                "correction_trigger": relationship_limit(scene, kind), "correction_target": relationship_limit(scene, kind, corrective=True)})
            relation_budget -= 1
        controls = target["appearance_controls"]
        fields = [field for field in ("color", "inset", "wood", "metal", "linen", "enamel", "rubber", "floor_color", "wall_color", "floor_pattern") if field in controls]
        fields = fields[:min(3, material_budget)]
        target["material_probes"] = fields
        material_budget -= len(fields)
    return targets, len(observed) > MAX_REVIEW_ENTITIES


def restoration_models(analysis, source):
    """Missing scene controls do not mean a missing approved catalog model."""
    if not hasattr(source, 'placement') or source.type is None:
        return []
    options = candidates(source.type, source.placement)
    if source.type == 'window_covering' and not any(source.wall_id is None or o.wall_id == source.wall_id for o in analysis.observations.openings):
        options = [a for a in options if ASSET_REGISTRY[a.asset_id].allows_unlinked_covering]
    options = [a for a in options if retains_features(a, source, analysis.observations.objects)]
    return shortlist([{'asset_id': a.asset_id, 'label': a.label,
                       'configurations': {p: {} for p in ASSET_REGISTRY[a.asset_id].placements}}
                      for a in options], source, limit=2)[:2]


def select_findings(analysis, judgment, scene=None):
    sources = {item.id: item for item in [*analysis.observations.objects, *analysis.observations.openings]}
    retained = {item.id for item in [*scene.objects, *scene.openings]} if scene else set()
    checked = {item.entity_id: item for item in judgment.checks}
    for finding in judgment.findings:
        # The server can restore an existing observation, never invent an ID.
        # Valid agreeing photo checks remain necessary even if the model wrongly
        # equates missing scene controls with missing catalog capabilities.
        if scene is not None and finding.kind == 'missing' and finding.entity_ids and all(
            key not in retained and key in sources and importance(sources[key]) <= 1
            and key in checked and checked[key].placement == 'difference'
            and (restoration_models(analysis, sources[key]) or key in {o.id for o in analysis.observations.openings})
            for key in finding.entity_ids
        ):
            finding.repairable = True
    eligible = [(index, finding) for index, finding in enumerate(judgment.findings)
                if finding.repairable and finding.confidence != "low" and (finding.entity_ids or finding.kind == "color")
                # Minor unsupported accessories cannot consume the sole visual
                # repair while the arrangement still needs checking.
                and not (finding.kind == "missing" and finding_priority(finding, sources)[1] > 1)]
    if scene is not None:
        eligible = [(index, finding) for index, finding in eligible if finding.placement_target is None or any(
            _placement_error(scene, key, finding.placement_target) > relationship_limit(scene, finding.placement_target.relation)
            for key in finding.entity_ids)]
        eligible = [(index, finding) for index, finding in eligible if finding.appearance_target is None or any(
            _color_error(appearance_value(scene, key, finding.appearance_target.field), finding.appearance_target.value)
                > (45 if finding.appearance_target.value.startswith('#') else 0)
            for key in ([None] if finding.appearance_target.field in {'floor_color', 'wall_color', 'floor_pattern'} else finding.entity_ids))]
    return sorted(eligible, key=lambda item: (
        finding_priority(item[1], sources),
        item[0],
    ))[:MAX_REPAIR_FINDINGS]


def appearance_value(scene, entity_id, field):
    if entity_id is None:
        return getattr(scene.finishes, field, None)
    obj = next((item for item in scene.objects if item.id == entity_id), None)
    return appearance_controls(obj).get(field, {}).get("value") if obj else None


def _color_error(actual, target):
    if actual is None:
        return inf
    if target.startswith("#"):
        return sum((int(actual[i:i+2], 16)-int(target[i:i+2], 16))**2 for i in (1,3,5))**.5
    return 0 if actual == target else 1


def _placement_error(scene, subject_id, target):
    return relationship_error(scene, subject_id, target.target_id, target.relation)


def finding_progress(previous, candidate, finding):
    """(addressed, regressed), without another provider request.

    Targeted placement must reach the existing broad proximity/facing threshold
    and improve. Targeted color must approach the requested material color.
    A change somewhere else cannot satisfy the finding.
    """
    ids = finding.entity_ids or [None]
    if finding.placement_target is not None:
        before = [_placement_error(previous, key, finding.placement_target) for key in ids]
        after = [_placement_error(candidate, key, finding.placement_target) for key in ids]
        relation = finding.placement_target.relation
        limit = relationship_limit(candidate, relation, corrective=True)
        tolerance = FACING_TOLERANCE_DEG if relation == "faces" else POSITION_TOLERANCE_M
        return (any(old > relationship_limit(previous, relation) for old in before) and all(new <= limit and new < old-.001 for old,new in zip(before,after)),
                any(new > old+tolerance for old,new in zip(before,after)))
    if finding.appearance_target is not None:
        target = finding.appearance_target
        # A color finding on wall IDs refers to room finishes, not wall objects.
        if target.field in {"floor_color", "wall_color", "floor_pattern"}:
            ids = [None]
        before = [_color_error(appearance_value(previous,key,target.field),target.value) for key in ids]
        after = [_color_error(appearance_value(candidate,key,target.field),target.value) for key in ids]
        limit = 45 if target.value.startswith("#") else 0
        return (all(new <= limit and new < old-.001 for old,new in zip(before,after)),
                any(new > old+1e-6 for old,new in zip(before,after)))
    old = {item.id:item for item in [*previous.objects,*previous.openings]}
    new = {item.id:item for item in [*candidate.objects,*candidate.openings]}
    if finding.kind == "missing":
        return all(key not in old and key in new for key in ids), any(key in old and key not in new for key in ids)
    fields = {"identity": {"type","asset_id","placement"}, "support": {"placement","wall","support_object_id","support_surface_id","opening_id"},
              "proportion": {"dimensions_m"}, "opening": {"wall","offset_m","width_m","height_m","sill_height_m"}}
    selected = fields.get(finding.kind, set())
    return (any(key in old and key in new and not same_values(old[key].model_dump(include=selected), new[key].model_dump(include=selected)) for key in ids), False)
