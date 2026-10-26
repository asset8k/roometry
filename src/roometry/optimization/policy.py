"""Atomic candidate assembly and deterministic acceptance; observations stay intact."""

from dataclasses import dataclass

from roometry.assessment.evidence import evidence_for_run
from roometry.assessment.metrics import measure_room
from roometry.assessment.schemas import RoomAssessment
from roometry.optimization.inventory import apply_inventory, utility_issues
from roometry.scenes.clearance_acceptance import clearance_regressions
from roometry.scenes.edit_schemas import MoveObject, RotateObject
from roometry.scenes.mutations import apply_edit
from roometry.scenes.schemas import MaterialColors, RoomScene
from roometry.scenes.validation import validate_geometry
from roometry.vision.review_policy import appearance_controls
from roometry.vision.spatial import relationship_error, relationship_limit


@dataclass
class Evaluation:
    scene: RoomScene
    assessment: RoomAssessment
    before_score: float | None
    after_score: float | None
    changed: list[str]
    issues: list[str]


def evaluate(run, proposal, source):
    base = run.result_scene or run.source_scene
    candidate = apply_inventory(base, proposal)
    seen = set()
    for change in proposal.changes:
        if change.object_id in seen:
            raise ValueError("Each object may appear only once in a candidate.")
        seen.add(change.object_id)
        obj = next((o for o in candidate.objects if o.id == change.object_id), None)
        if obj is None or obj.fixed or obj.locked:
            raise ValueError("Changes must target an existing movable, unlocked object.")
        if change.position is not None:
            candidate = apply_edit(candidate, MoveObject(operation="move", object_id=obj.id, position=change.position))
        if change.rotation_y_rad is not None:
            candidate = apply_edit(candidate, RotateObject(operation="rotate", object_id=obj.id, rotation_y_rad=change.rotation_y_rad))
        obj = next(o for o in candidate.objects if o.id == change.object_id)
        controls = appearance_controls(obj)
        if change.color is not None and "color" not in controls:
            raise ValueError("This asset has no editable primary color.")
        color_changed = change.color is not None and change.color.upper() != obj.color.upper()
        material_updates = {}
        if change.material_colors is not None:
            for field, value in change.material_colors.model_dump().items():
                if value is not None:
                    if field not in controls:
                        raise ValueError("A material change must use a visible control on this asset.")
                    if value.upper() != controls[field]["value"].upper(): material_updates[field] = value
        if color_changed or material_updates:
            if color_changed:
                obj.color = change.color
            if material_updates:
                material = obj.material_colors.model_copy(deep=True) if obj.material_colors else MaterialColors()
                for field, value in material_updates.items(): setattr(material, field, value)
                obj.material_colors = material
    candidate = RoomScene.model_validate(candidate)
    originals = {o.id: o for o in run.source_scene.objects}
    retained = {o.id: o for o in candidate.objects}
    for obj in originals.values():
        if (obj.fixed or obj.locked) and retained.get(obj.id) != obj:
            raise ValueError("A protected object or supported child would change.")
    before, after = validate_geometry(base), validate_geometry(candidate)
    issues = [f"{i.code}: {i.message}" for i in after.errors]
    issues.extend(i.message for i in clearance_regressions(before, after))
    issues.extend(utility_issues(run.source_scene, candidate))
    # Human-confirmed facing constraints remain hard intent during redesign;
    # generic photo positions are reconstruction evidence, not redesign locks.
    for subject, target in source.reconstruction_constraints.facing_targets.items():
        if target is None:
            continue
        old = relationship_error(base, subject, target, "faces")
        new = relationship_error(candidate, subject, target, "faces")
        if new > max(old, relationship_limit(base, "faces")) + .01:
            issues.append("A user-confirmed facing relationship would be lost.")
    evidence = evidence_for_run(source)
    measured_before, measured_after = measure_room(base, evidence=evidence), measure_room(candidate, evidence=evidence)
    for key in ("openness", "storage", "workspace"):
        a, b = getattr(measured_before, key).score, getattr(measured_after, key).score
        if key != run.focus and a is not None and (b is None or b < a - .5):
            issues.append(f"The {key} rating would decrease substantially.")
    base_objects = {o.id: o for o in base.objects}
    def visible_colors(obj):
        return {key: control["value"].upper() for key, control in appearance_controls(obj).items()}
    changed = [o.id for o in candidate.objects if o.id not in base_objects
               or o.asset_id != base_objects[o.id].asset_id
               or o.dimensions_m != base_objects[o.id].dimensions_m
               or o.position != base_objects[o.id].position
               or o.rotation_y_rad != base_objects[o.id].rotation_y_rad
               or visible_colors(o) != visible_colors(base_objects[o.id])]
    changed.extend(o.id for o in base.objects if o.id not in retained)
    return Evaluation(candidate, measured_after, getattr(measured_before, run.focus).score,
                      getattr(measured_after, run.focus).score, changed, issues[:16])


def meaningful_gain(run, evaluation, source):
    """Same card rubric as the UI, plus a practical category outcome."""
    if run.instruction or run.focus == "aesthetics":
        return True  # Pairwise review must substantiate these qualitative goals.
    before = measure_room(run.result_scene or run.source_scene, evidence=evidence_for_run(source))
    after = evaluation.assessment
    a, b = getattr(before, run.focus), getattr(after, run.focus)
    if b.score is None:
        return False
    if a.score is None:
        # Creating a usable missing function is allowed; no fabricated 0/10 baseline.
        return run.focus in {"workspace", "storage"} and b.score >= 6.
    if b.score < a.score + .5 - 1e-8:
        return False
    def value(card, key):
        return next((m.value for m in card.metrics if m.key == key), 0.)
    if run.focus == "openness":
        old = validate_geometry(run.result_scene or run.source_scene).walking_space
        new = validate_geometry(evaluation.scene).walking_space
        rank = {"unassessed": 0, "blocked": 0, "narrow": 1, "clear": 2}
        return (value(b, "main_walking_center_area") >= value(a, "main_walking_center_area") + .5 - 1e-8
                or bool(old and new and rank.get(new.status, 0) > rank.get(old.status, 0)))
    if run.focus == "storage":
        return (value(b, "accessible_storage_fraction") > value(a, "accessible_storage_fraction") + .05
                or value(b, "nominal_storage_volume") >= value(a, "nominal_storage_volume") + max(.15, .1 * value(a, "nominal_storage_volume")) - 1e-8)
    return True
