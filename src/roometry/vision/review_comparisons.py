"""Compare explicit photo answers with actual poses and rendered material parts.

The model supplies qualitative photo evidence; application code measures the
candidate. No precise photo coordinates or extra model calls are required.
"""

from collections import Counter

from pydantic import ValidationError

from roometry.vision.review_policy import appearance_value, _color_error, finding_priority
from roometry.vision.review_schemas import MaterialComparison, RelationComparison, ReviewFinding


def parse_comparisons(raw, target, warnings):
    """A malformed subcomparison cannot discard its other material/pose checks."""
    result = {}
    for field, model, maximum in (("relations", RelationComparison, 6), ("materials", MaterialComparison, 7)):
        values = raw.get(field, [])
        if not isinstance(values, list) or len(values) > maximum:
            warnings.append("Invalid comparison entries were excluded; the affected details remain unverified.")
            values = []
        valid = []
        for value in values:
            try:
                item = model.model_validate(value)
                key = (item.target_id, item.relation) if field == "relations" else item.field
                allowed = {(p["target_id"], p["relation"]) for p in target["relation_probes"]} if field == "relations" else set(target["material_probes"])
                if key not in allowed:
                    raise ValueError("Only server-selected photo comparisons are allowed.")
            except (ValidationError, ValueError):
                warnings.append("Invalid comparison entries were excluded; the affected details remain unverified.")
                continue
            valid.append((key, item))
        raw_keys = []
        for value in values:
            if not isinstance(value, dict):
                continue
            if field == "relations" and isinstance(value.get("target_id"), str) and isinstance(value.get("relation"), str):
                raw_keys.append((value["target_id"], value["relation"]))
            elif field == "materials" and isinstance(value.get("field"), str):
                raw_keys.append(value["field"])
        counts = Counter(raw_keys)
        result[field] = [item.model_dump() for key, item in valid if counts[key] == 1]
        if any(count > 1 for count in counts.values()):
            warnings.append("Duplicate photo comparisons were excluded; their details remain uncertain.")
    return result


def apply_comparisons(scene, targets, checks, findings, warnings, *, analysis=None):
    """Derive actionable differences even if an overall model vote says match."""
    generated, remove = [], set()
    for key, check in checks.items():
        target = targets[key]
        relations = {(item.target_id, item.relation): item for item in check.relations}
        materials = {item.field: item for item in check.materials}
        aspects = {"placement": [], "appearance": []}
        for probe in target["relation_probes"]:
            answer = relations.get((probe["target_id"], probe["relation"]))
            kind = "orientation" if probe["relation"] == "faces" else "position"
            matching = [index for index, finding in enumerate(findings) if finding.entity_ids == [key]
                        and finding.kind == kind and finding.placement_target is not None
                        and finding.placement_target.target_id == probe["target_id"] and finding.placement_target.relation == probe["relation"]]
            if answer is None or answer.photo_support == "unknown":
                aspects["placement"].append("uncertain")
                for index in matching:
                    findings[index].repairable = False
                    findings[index].placement_target = None
                continue
            different = answer.photo_support == "supported" and probe["candidate_error"] > probe["correction_trigger"]
            aspects["placement"].append("difference" if different else "consistent")
            remove.update(matching)
            if different:
                generated.append(ReviewFinding(kind=kind, entity_ids=[key], confidence="medium", severity="major",
                    source_image_ids=check.source_image_ids, view_ids=["top", "cutaway_se"],
                    description="The photographed local arrangement or facing direction differs substantially from the reconstruction.",
                    correction="Restore the photo-supported relationship while preserving the room measurements and neighboring furniture.",
                    placement_target={"target_id": probe["target_id"], "relation": probe["relation"]}, repairable=True))
        for field in target["material_probes"]:
            answer = materials.get(field)
            matching = [index for index, finding in enumerate(findings) if finding.kind == "color"
                        and ((finding.entity_ids == [key]) if key is not None else not finding.entity_ids)
                        and finding.appearance_target is not None and finding.appearance_target.field == field]
            if answer is None or answer.photo_value is None:
                aspects["appearance"].append("uncertain")
                for index in matching:
                    findings[index].repairable = False
                    findings[index].appearance_target = None
                continue
            error = _color_error(appearance_value(scene, key, field), answer.photo_value)
            different = error > (0 if field == "floor_pattern" else 45)
            aspects["appearance"].append("difference" if different else "consistent")
            remove.update(matching)
            if different:
                controls = target["appearance_controls"].get(field, {})
                parts = controls.get("region", "main surfaces") if isinstance(controls, dict) else {"floor_color": "floor", "wall_color": "walls", "floor_pattern": "floor finish"}.get(field, "room finishes")
                generated.append(ReviewFinding(kind="color", entity_ids=[key] if key is not None else [], confidence="medium", severity="major",
                    source_image_ids=check.source_image_ids, view_ids=["cutaway_nw", "cutaway_se"],
                    description=f"The photographed color of {parts or field} differs from the rendered material region.",
                    correction="Apply the photo-supported color to this region; preserve other material colors and placement.",
                    appearance_target={"field": field, "value": answer.photo_value}, repairable=True))
        for aspect, statuses in aspects.items():
            if not statuses:
                continue
            if "difference" in statuses:
                setattr(check, aspect, "difference")
            elif "uncertain" in statuses:
                setattr(check, aspect, "uncertain")
                warnings.append("Some selected local relationships or material regions remain unverified from the photos.")
            elif getattr(check, aspect) == "difference":
                # Remove a cosmetic discrepancy only when the corresponding
                # model finding was actually replaced by a measured comparison.
                remaining = [f for index, f in enumerate(findings) if index not in remove and (key in f.entity_ids if key else not f.entity_ids)
                             and ((f.kind == "color") == (aspect == "appearance"))]
                if not remaining:
                    setattr(check, aspect, "consistent")
                    if not any(index in remove and (key in f.entity_ids if key else not f.entity_ids)
                               and ((f.kind == "color") == (aspect == "appearance")) for index, f in enumerate(findings)):
                        warnings.append("An overall difference vote conflicted with its detailed photo comparisons; the detailed comparisons were retained.")
    # Keep existing relative ordering when a comparison replaces the same
    # finding, so accepted-finding indices stay stable for unrelated findings.
    result = []
    for index, finding in enumerate(findings):
        if index not in remove:
            result.append(finding)
            continue
        replacement = next((item for item in generated if item.entity_ids == finding.entity_ids and item.kind == finding.kind
                            and ((item.appearance_target is not None and finding.appearance_target is not None
                                  and item.appearance_target.field == finding.appearance_target.field)
                                 or (item.placement_target is not None and item.placement_target == finding.placement_target))), None)
        if replacement is not None:
            result.append(replacement)
            generated.remove(replacement)
    result = generated + result
    if len(result) > 12:
        if analysis is not None:
            sources = {item.id: item for item in [*analysis.observations.objects, *analysis.observations.openings]}
            result.sort(key=lambda item: finding_priority(item, sources))
        warnings.append("The bounded review prioritized its 12 most consequential differences; further differences remain unresolved.")
    return result[:12]
