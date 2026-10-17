"""Copy-based floor transforms and support-child propagation; no persistence."""

from math import cos, sin, tau

from pydantic import TypeAdapter, ValidationError

from roometry.scenes.edit_schemas import EditOperation, EditRule, MoveObject
from roometry.scenes.schemas import RoomScene

_EDIT_ADAPTER: TypeAdapter[EditOperation] = TypeAdapter(EditOperation)


class EditRejected(ValueError):
    """A deterministic edit-policy or candidate-schema failure."""

    def __init__(self, code: EditRule, entity_ids: tuple[str, ...], message: str):
        super().__init__(message)
        self.code = code
        self.entity_ids = entity_ids


def apply_edit(scene: RoomScene, edit: EditOperation) -> RoomScene:
    """Produce a structurally valid candidate; the service checks its geometry.

    Protection is evaluated against this supplied base, never against flags in
    the edit (which cannot contain flags). Callers responsible for saved projects
    must provide an authorized previous scene rather than trust a client's base.
    """
    base = RoomScene.model_validate(scene)
    edit = _EDIT_ADAPTER.validate_python(edit)
    target = next((obj for obj in base.objects if obj.id == edit.object_id), None)
    if target is None:
        raise EditRejected("object_not_found", (edit.object_id,), "Object does not exist in the supplied scene.")
    if target.locked:
        raise EditRejected("object_locked", (target.id,), "Locked objects cannot be moved or rotated.")
    if target.fixed:
        raise EditRejected("object_fixed", (target.id,), "Fixed objects cannot be moved or rotated.")
    if target.placement != "floor":
        raise EditRejected(
            "unsupported_placement", (target.id,),
            "Direct transforms support floor objects only; surface objects follow their support.",
        )

    new_x, new_z = target.position.x, target.position.z
    new_rotation = target.rotation_y_rad
    if isinstance(edit, MoveObject):
        new_x, new_z = edit.position.x, edit.position.z
    else:
        new_rotation = edit.rotation_y_rad % tau
        # Match SceneObject normalization for tiny negative values rounding to tau.
        if new_rotation == tau:
            new_rotation = 0.0

    if (new_x, new_z, new_rotation) == (target.position.x, target.position.z, target.rotation_y_rad):
        return base

    children = [obj for obj in base.objects if obj.support_object_id == target.id]
    protected = tuple(sorted(child.id for child in children if child.locked or child.fixed))
    if protected:
        raise EditRejected(
            "protected_children", (target.id, *protected),
            "Transform would move or rotate locked or fixed surface children.",
        )

    candidate = base.model_copy(deep=True)
    by_id = {obj.id: obj for obj in candidate.objects}
    moved = by_id[target.id]
    moved.position.x, moved.position.z = new_x, new_z
    moved.rotation_y_rad = new_rotation

    angle = new_rotation - target.rotation_y_rad
    c, s = cos(angle), sin(angle)
    for child in children:
        transformed = by_id[child.id]
        dx = child.position.x - target.position.x
        dz = child.position.z - target.position.z
        # Three.js positive Y rotation: x' = c*x + s*z; z' = -s*x + c*z.
        transformed.position.x = new_x + c * dx + s * dz
        transformed.position.z = new_z - s * dx + c * dz
        transformed.rotation_y_rad = child.rotation_y_rad + angle

    try:
        return RoomScene.model_validate(candidate)
    except ValidationError as error:
        invalid_ids: set[str] = set()
        for issue in error.errors():
            location = issue["loc"]
            if len(location) >= 2 and location[0] == "objects" and isinstance(location[1], int):
                invalid_ids.add(candidate.objects[location[1]].id)
        raise EditRejected(
            "candidate_schema", tuple(sorted(invalid_ids)) or (target.id,),
            "Transformed positions or rotations exceed the scene schema's limits.",
        ) from error
