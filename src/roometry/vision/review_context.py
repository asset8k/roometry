"""Request-only review views; stored observations and authority stay unchanged."""

from copy import deepcopy

from roometry.vision.context_budget import photo_references
from roometry.vision.review_policy import appearance_controls


def _without_nulls(value):
    if isinstance(value, dict):
        return {
            key: _without_nulls(item) for key, item in value.items() if item is not None
        }
    if isinstance(value, list):
        return [_without_nulls(item) for item in value]
    return value


def compact_review_context(context, image_ids):
    result = deepcopy(context)
    # Audit trails explain how we reached the scene, not what the reviewer sees.
    # Keep every observed item, relationship, pose, size, mount and protection.
    for field in (
        "estimate_corrections",
        "interpretation_corrections",
        "appearance_corrections",
        "assumptions",
        "warnings",
    ):
        result["scene"].pop(field, None)
    for field in ("observations", "scene"):
        result[field] = _without_nulls(result[field])
    targets = {item["entity_id"]: item for item in result["review_targets"]}
    # These facts are already supplied verbatim in each review target. Keep one
    # copy while preserving every entity, relationship, control and source view.
    for item in result["observations"]["objects"]:
        if item["id"] in targets:
            item.pop("label", None)
            item.pop("source_image_ids", None)
    for item in result["scene"]["objects"]:
        if item["id"] in targets and targets[item["id"]]["appearance_controls"]:
            item.pop("color", None)
            item.pop("material_colors", None)
    # A null category in historical constraints explicitly leaves it unresolved;
    # preserve it rather than treating it as absent metadata.
    # Targets deliberately retain null entity_id for the room-finish check and
    # null control values for materials absent from the rendered candidate.
    return photo_references(result, image_ids)


def without_schema_titles(value):
    if isinstance(value, dict):
        return {
            key: without_schema_titles(item)
            for key, item in value.items()
            if key != "title"
        }
    if isinstance(value, list):
        return [without_schema_titles(item) for item in value]
    return value


def repair_review_context(analysis, scene, judgment, selected):
    """Send selected objectives once, with exact original appearance baselines."""
    actionable = [finding for _, finding in selected]
    subjects = {key for finding in actionable for key in finding.entity_ids}
    observed = {obj.id: obj for obj in analysis.observations.objects}
    baselines = {}
    for finding in actionable:
        target = finding.appearance_target
        if finding.kind != "color" or target is None:
            continue
        room_field = target.field in {"wall_color", "floor_color", "floor_pattern"}
        for key in [None] if room_field else finding.entity_ids:
            if room_field:
                finishes = analysis.observations.finishes
                previous = (
                    getattr(finishes.finishes, target.field) if finishes else None
                )
            else:
                obj = observed[key]
                previous = (
                    obj.color
                    if target.field == "color"
                    else getattr(obj.material_colors, target.field, None)
                )
            baselines[key, target.field] = {
                "entity_id": key,
                "field": target.field,
                "previous_value": previous,
                "revised_value": target.value,
                "source_image_ids": finding.source_image_ids,
            }
    return {
        "visual_review": judgment.model_dump(
            mode="json", include={"verdict", "summary", "limitations"}
        ),
        "selected_findings": [index for index, _ in selected],
        "repair_objectives": [
            finding.model_dump(mode="json") for finding in actionable
        ],
        "appearance_controls": {
            obj.id: appearance_controls(obj)
            for obj in scene.objects
            if obj.id in subjects
        },
        "appearance_revision_baselines": list(baselines.values()),
    }
