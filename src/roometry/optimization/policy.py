"""Atomic candidate assembly and deterministic acceptance; observations stay intact."""

from dataclasses import dataclass

from roometry.assessment.metrics import measure_room
from roometry.assessment.evidence import evidence_for_run
from roometry.assessment.schemas import RoomAssessment
from roometry.scenes.clearance_acceptance import clearance_regressions
from roometry.scenes.edit_schemas import MoveObject, RotateObject
from roometry.scenes.mutations import apply_edit
from roometry.scenes.schemas import RoomScene, MaterialColors
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
    candidate = base.model_copy(deep=True)
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
            if run.preferences.budget is not None:
                raise ValueError("Finish-work costs are unknown. With a stated budget use existing-item rearrangements only; suggest color changes separately.")
            if color_changed:
                obj.color = change.color
            if material_updates:
                material = obj.material_colors.model_copy(deep=True) if obj.material_colors else MaterialColors()
                for field, value in material_updates.items(): setattr(material, field, value)
                obj.material_colors = material
    candidate = RoomScene.model_validate(candidate)
    # No resize, asset swapping, removals or additions enter the patch contract.
    originals = {o.id: o for o in run.source_scene.objects}
    for obj in candidate.objects:
        if (originals[obj.id].fixed or originals[obj.id].locked) and obj != originals[obj.id]:
            raise ValueError("A protected object or supported child would change.")
    before, after = validate_geometry(base), validate_geometry(candidate)
    issues = [f"{i.code}: {i.message}" for i in after.errors]
    issues.extend(i.message for i in clearance_regressions(before, after))
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
    changed = [o.id for o in candidate.objects if o.position != base_objects[o.id].position
               or o.rotation_y_rad != base_objects[o.id].rotation_y_rad
               or visible_colors(o) != visible_colors(base_objects[o.id])]
    return Evaluation(candidate, measured_after, getattr(measured_before, run.focus).score,
                      getattr(measured_after, run.focus).score, changed, issues[:16])
