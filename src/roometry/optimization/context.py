"""Authorize editable snapshots against server-owned scene ancestry, without model calls."""

from math import isclose

from roometry.scenes.clearance_acceptance import clearance_regressions
from roometry.scenes.edit_schemas import FloorPosition, MoveObject, RotateObject
from roometry.scenes.mutations import apply_edit
from roometry.scenes.validation import validate_geometry
from roometry.vision.review_policy import appearance_controls


class InvalidSceneContext(ValueError):
    pass


def refinement_message(message):
    return message.replace("original room", "starting layout").replace("the original", "the starting layout").replace("The original", "The starting layout")


def validate_context(base, scene):
    """Only validated manual transforms, visible colors and additional locks may differ."""
    if (base.model_dump(exclude={"objects"}) != scene.model_dump(exclude={"objects"})
        or [o.id for o in base.objects] != [o.id for o in scene.objects]):
        raise InvalidSceneContext("The current scene must preserve room data, openings and furniture identities.")
    replay = base.model_copy(deep=True)
    for old, obj in zip(base.objects, scene.objects, strict=True):
        if old.locked and old != obj:
            raise InvalidSceneContext("A fixed or previously locked object changed.")
        excluded = {"position", "rotation_y_rad", "color", "material_colors", "locked"}
        if old.model_dump(exclude=excluded) != obj.model_dump(exclude=excluded):
            raise InvalidSceneContext("Manual edits cannot change furniture dimensions, models or supports.")
        before, after = appearance_controls(old), appearance_controls(obj)
        if old.color != obj.color and "color" not in before:
            raise InvalidSceneContext("This furniture has no editable primary color.")
        old_material = old.material_colors.model_dump() if old.material_colors else {}
        new_material = obj.material_colors.model_dump() if obj.material_colors else {}
        if any(new_material.get(k) != old_material.get(k) and k not in before for k in old_material.keys() | new_material.keys()):
            raise InvalidSceneContext("A material change must use a visible furniture control.")
        if set(before) != set(after):
            raise InvalidSceneContext("Furniture appearance controls must remain unchanged.")
        if old.placement == "floor":
            if old.rotation_y_rad != obj.rotation_y_rad:
                replay = apply_edit(replay, RotateObject(operation="rotate", object_id=old.id, rotation_y_rad=obj.rotation_y_rad))
            if old.position.x != obj.position.x or old.position.z != obj.position.z:
                replay = apply_edit(replay, MoveObject(operation="move", object_id=old.id, position=FloorPosition(x=obj.position.x, z=obj.position.z)))
    for expected, obj in zip(replay.objects, scene.objects, strict=True):
        if (any(not isclose(getattr(expected.position, axis), getattr(obj.position, axis), abs_tol=1e-6) for axis in ("x", "y", "z"))
            or not isclose(expected.rotation_y_rad, obj.rotation_y_rad, abs_tol=1e-6)):
            raise InvalidSceneContext("Supported and attached items must retain their validated positions.")
    before, after = validate_geometry(base), validate_geometry(scene)
    if not after.valid or clearance_regressions(before, after):
        raise InvalidSceneContext("The current scene needs valid geometry and must preserve checked access routes.")
    return scene.model_copy(deep=True)
