"""Demo edit/validation API contracts, separate from geometric scene payloads."""

from typing import Annotated, Literal

from pydantic import Field

from roometry.scenes.schemas import (
    MAX_POSITION_M, FiniteNumber, Identifier, RoomScene, SceneModel,
)
from roometry.scenes.validation import GeometryReport

HorizontalCoordinate = Annotated[
    FiniteNumber, Field(ge=-MAX_POSITION_M, le=MAX_POSITION_M),
]


class FloorPosition(SceneModel):
    """Absolute world coordinates. Floor elevation cannot be edited."""

    x: HorizontalCoordinate
    z: HorizontalCoordinate


class MoveObject(SceneModel):
    operation: Literal["move"]
    object_id: Identifier
    position: FloorPosition


class RotateObject(SceneModel):
    operation: Literal["rotate"]
    object_id: Identifier
    rotation_y_rad: FiniteNumber


EditOperation = Annotated[MoveObject | RotateObject, Field(discriminator="operation")]
EditRule = Literal[
    "object_not_found", "object_locked", "object_fixed", "unsupported_placement",
    "protected_children", "candidate_schema", "invalid_base_geometry",
]


class EditIssue(SceneModel):
    code: EditRule
    entity_ids: tuple[str, ...]
    message: str


class ValidateSceneRequest(SceneModel):
    scene: RoomScene


class PreviewEditRequest(SceneModel):
    scene: RoomScene
    edit: EditOperation


class PreviewEditResponse(SceneModel):
    """On rejection `scene` is the unmodified supplied base, not a saved revision.

    If the base already fails geometry, it is returned for inspection and no edit
    is attempted. Geometry reports cover baseline-v1 only, not full acceptance.
    """

    accepted: bool
    scene: RoomScene
    changed_object_ids: tuple[Identifier, ...]
    edit_errors: tuple[EditIssue, ...]
    base_validation: GeometryReport
    candidate_validation: GeometryReport | None
