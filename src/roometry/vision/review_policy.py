"""Bounded review coverage, catalog controls and measurable repair objectives.

These checks measure progress toward a photo-backed review finding, not whether
the model's interpretation of a photo is correct. Geometry remains independent.
"""

from math import cos, hypot, inf, isclose, sin

from roometry.scenes.catalog import ASSET_CATALOG
from roometry.vision.importance import importance
from roometry.vision.review_render import material_color
from roometry.vision.spatial import planar_bounds, planar_gap

MAX_REVIEW_ENTITIES = 24
MAX_REPAIR_FINDINGS = 6
LAYOUT_KINDS = {"position", "orientation", "opening", "support", "proportion", "identity"}


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
    return {
        field: {"value": obj.color if field == "color" else material_color(obj, role),
                "parts": [part.name for part in parts if part.material == role or (field == "color" and part.material.startswith("primary"))][:16]}
        for field, role in sorted(controls.items())
    }


def review_targets(analysis, scene):
    observed = [item for item in [*analysis.observations.openings, *analysis.observations.objects] if importance(item) <= 1]
    observed.sort(key=lambda item: (importance(item), item.confidence == "low", item.id))
    objects = {obj.id: obj for obj in scene.objects}
    openings = {item.id: item for item in scene.openings}
    walls = {item.observation_id: item.wall for item in scene.wall_mapping}
    targets = []
    for source in observed[:MAX_REVIEW_ENTITIES]:
        obj = objects.get(source.id)
        targets.append({
            "entity_id": source.id, "label": getattr(source, "label", source.type),
            "kind": "opening" if source.id in {item.id for item in analysis.observations.openings} else "object",
            "source_image_ids": source.source_image_ids, "retained": source.id in objects or source.id in openings,
            "appearance_controls": appearance_controls(obj) if obj is not None else {},
            "planar_bounds_m": planar_bounds(source.id, scene, walls),
            "opening_gaps_m": {key: round(planar_gap(source.id, key, scene, walls), 3) for key in openings} if obj is not None else {},
        })
    targets.append({"entity_id": None, "kind": "room", "label": "Room finishes",
                    "source_image_ids": analysis.observations.image_ids,
                    "appearance_controls": scene.finishes.model_dump() if scene.finishes else {}})
    return targets, len(observed) > MAX_REVIEW_ENTITIES


def select_findings(analysis, judgment):
    sources = {item.id: item for item in [*analysis.observations.objects, *analysis.observations.openings]}
    def priority(finding):
        ranks = [importance(sources[key]) for key in finding.entity_ids if key in sources]
        return min(ranks, default=0)
    eligible = [(index, finding) for index, finding in enumerate(judgment.findings)
                if finding.repairable and finding.confidence != "low" and (finding.entity_ids or finding.kind == "color")
                # Minor unsupported accessories cannot consume the sole visual
                # repair while the arrangement still needs checking.
                and not (finding.kind == "missing" and finding.severity == "minor" and priority(finding) > 1)]
    return sorted(eligible, key=lambda item: (
        priority(item[1]), item[1].severity != "major",
        0 if item[1].kind in LAYOUT_KINDS else 1 if item[1].kind == "color" else 2,
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
    walls = {item.observation_id: item.wall for item in scene.wall_mapping}
    if target.relation != "faces":
        return planar_gap(subject_id, target.target_id, scene, walls)
    subject = next((obj for obj in scene.objects if obj.id == subject_id), None)
    bounds = planar_bounds(target.target_id, scene, walls)
    if subject is None or bounds is None:
        return inf
    dx, dz = (bounds[0]+bounds[1])/2-subject.position.x, (bounds[2]+bounds[3])/2-subject.position.z
    distance = hypot(dx, dz)
    return 1-(sin(subject.rotation_y_rad)*dx+cos(subject.rotation_y_rad)*dz)/distance if distance > .05 else inf


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
        limit = 1-cos(.7853981634) if relation == "faces" else .4 if relation == "against_wall" else max(.8, .15*min(candidate.room.width_m, candidate.room.length_m))
        return (all(new <= limit and new < old-.001 for old,new in zip(before,after)),
                any(new > old+1e-6 for old,new in zip(before,after)))
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
