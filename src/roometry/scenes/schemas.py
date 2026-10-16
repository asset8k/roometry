"""Version 1 scene data validation; physical geometry is a separate layer."""

from math import tau
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from roometry.scenes.assets import ASSET_REGISTRY, ObjectType, Placement

# Initial structural resource limits. Geometry policy will enforce actual room
# containment, clearances, and comparisons against the previous scene version.
MAX_ROOM_SIDE_M = 30.0
MAX_ROOM_HEIGHT_M = 10.0
MAX_OBJECT_DIMENSION_M = 30.0
MAX_POSITION_M = 100.0
MAX_OBJECTS = 200
MAX_OPENINGS = 32
MAX_NOTES = 50
MAX_NOTE_LENGTH = 1000

RoomType = Literal[
    "bedroom", "living_room", "kitchen", "bathroom", "office", "dining_room",
    "entryway", "laundry_room", "other",
]
Wall = Literal["north", "south", "west", "east"]
MeasurementSource = Literal["user_confirmed", "estimated"]
ObjectMeasurementSource = Literal["user_confirmed", "catalog", "estimated"]
Provenance = Literal["photo_observed", "user_added", "agent_added"]
Identifier = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")]
AssetId = Annotated[
    str,
    Field(
        min_length=1, max_length=96, pattern=r"^[a-z][a-z0-9_]*$",
        json_schema_extra={"enum": list(ASSET_REGISTRY)},
    ),
]
FiniteNumber = Annotated[float, Field(allow_inf_nan=False)]
PositiveDistance = Annotated[FiniteNumber, Field(gt=0, le=MAX_OBJECT_DIMENSION_M)]
NonnegativeDistance = Annotated[FiniteNumber, Field(ge=0, le=MAX_OBJECT_DIMENSION_M)]
Note = Annotated[str, Field(min_length=1, max_length=MAX_NOTE_LENGTH, pattern=r"\S")]
Color = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")]


class SceneModel(BaseModel):
    """Reject coercion/unknown fields and revalidate nested model instances."""

    model_config = ConfigDict(
        extra="forbid", strict=True, revalidate_instances="always",
    )


class Position(SceneModel):
    """World-space bottom center, in meters; room containment is checked later."""

    x: Annotated[FiniteNumber, Field(ge=-MAX_POSITION_M, le=MAX_POSITION_M)]
    y: Annotated[FiniteNumber, Field(ge=0, le=MAX_POSITION_M)]
    z: Annotated[FiniteNumber, Field(ge=-MAX_POSITION_M, le=MAX_POSITION_M)]


class ObjectDimensions(SceneModel):
    width: PositiveDistance
    depth: PositiveDistance
    height: PositiveDistance


class RoomDimensions(SceneModel):
    room_type: RoomType
    width_m: Annotated[FiniteNumber, Field(gt=0, le=MAX_ROOM_SIDE_M)]
    length_m: Annotated[FiniteNumber, Field(gt=0, le=MAX_ROOM_SIDE_M)]
    height_m: Annotated[FiniteNumber, Field(gt=0, le=MAX_ROOM_HEIGHT_M)]
    measurements_source: MeasurementSource


class Opening(SceneModel):
    id: Identifier
    type: Literal["door", "window"]
    wall: Wall
    offset_m: NonnegativeDistance
    width_m: PositiveDistance
    height_m: Annotated[FiniteNumber, Field(gt=0, le=MAX_ROOM_HEIGHT_M)]
    sill_height_m: Annotated[FiniteNumber, Field(ge=0, le=MAX_ROOM_HEIGHT_M)]
    swing: Literal["inward", "outward", "none", "unknown"]
    hinge: Literal["min_offset", "max_offset", "unknown"] | None
    provenance: Literal["photo_observed", "user_added"]
    measurements_source: MeasurementSource

    @model_validator(mode="after")
    def check_opening_fields(self) -> Self:
        if self.type == "window":
            if self.swing != "none" or self.hinge is not None:
                raise ValueError("Windows require swing='none' and hinge=null")
        else:
            if self.sill_height_m != 0:
                raise ValueError("Doors require sill_height_m=0")
            if self.swing == "none" and self.hinge is not None:
                raise ValueError("Doors without a swing require hinge=null")
            if self.swing != "none" and self.hinge is None:
                raise ValueError("Swinging or uncertain doors require a hinge edge or 'unknown'")
        return self


class SceneObject(SceneModel):
    id: Identifier
    type: ObjectType
    asset_id: AssetId
    position: Position
    rotation_y_rad: FiniteNumber
    dimensions_m: ObjectDimensions
    color: Color
    placement: Placement
    # Nullable references are deliberately required: use explicit JSON null.
    support_object_id: Identifier | None
    wall: Wall | None
    opening_id: Identifier | None
    fixed: bool
    locked: bool
    provenance: Provenance
    measurements_source: ObjectMeasurementSource

    @field_validator("rotation_y_rad")
    @classmethod
    def normalize_rotation(cls, angle: float) -> float:
        # Very small negative floats can round to tau after modulo.
        normalized = angle % tau
        return 0.0 if normalized == tau else normalized

    @field_validator("color")
    @classmethod
    def normalize_color(cls, color: str) -> str:
        return color.upper()

    @model_validator(mode="after")
    def check_asset_and_placement(self) -> Self:
        asset = ASSET_REGISTRY.get(self.asset_id)
        if asset is None:
            raise ValueError(f"Unknown asset_id: {self.asset_id}")
        if asset.object_type != self.type:
            raise ValueError(f"Asset {self.asset_id} does not match type {self.type}")
        if self.placement not in asset.placements:
            raise ValueError(f"Asset {self.asset_id} does not allow {self.placement} placement")

        if self.placement == "surface":
            if self.support_object_id is None or self.support_object_id == self.id:
                raise ValueError("Surface objects require a different support_object_id")
        elif self.support_object_id is not None:
            raise ValueError("Only surface objects may have support_object_id")

        if self.placement == "wall":
            if self.wall is None:
                raise ValueError("Wall objects require a wall")
        elif self.wall is not None:
            raise ValueError("Only wall objects may have a wall")

        if self.placement == "floor" and self.position.y != 0:
            raise ValueError("Floor objects require position.y=0")
        if self.type == "window_covering":
            if self.opening_id is None:
                raise ValueError("Window coverings require opening_id")
        elif self.opening_id is not None:
            raise ValueError("Only window coverings may have opening_id")
        return self


class RoomScene(SceneModel):
    schema_version: Literal[1]
    room: RoomDimensions
    openings: Annotated[list[Opening], Field(max_length=MAX_OPENINGS)]
    objects: Annotated[list[SceneObject], Field(max_length=MAX_OBJECTS)]
    assumptions: Annotated[list[Note], Field(max_length=MAX_NOTES)]
    warnings: Annotated[list[Note], Field(max_length=MAX_NOTES)]

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_version(cls, version: object) -> object:
        # Literal[1] alone also accepts True and 1.0 due to Python equality.
        if type(version) is not int:
            raise ValueError("schema_version must be the integer 1")
        return version

    @model_validator(mode="after")
    def check_ids_and_references(self) -> Self:
        seen: set[str] = set()
        for item in [*self.openings, *self.objects]:
            if item.id in seen:
                raise ValueError(f"Duplicate scene ID: {item.id}")
            seen.add(item.id)

        objects = {obj.id: obj for obj in self.objects}
        openings = {opening.id: opening for opening in self.openings}
        for obj in self.objects:
            if obj.placement == "surface":
                support_id = obj.support_object_id
                if support_id is None:
                    raise ValueError(f"Object {obj.id} requires support_object_id")
                support = objects.get(support_id)
                if support is None:
                    raise ValueError(f"Object {obj.id} references missing support {support_id}")
                if support.placement not in {"floor", "wall"}:
                    raise ValueError(f"Object {obj.id} cannot use nested surface support")
                if not ASSET_REGISTRY[support.asset_id].supplies_surface:
                    raise ValueError(f"Object {support.id} does not supply an approved surface")
            if obj.type == "window_covering":
                opening_id = obj.opening_id
                if opening_id is None:
                    raise ValueError(f"Object {obj.id} requires opening_id")
                opening = openings.get(opening_id)
                if opening is None or opening.type != "window":
                    raise ValueError(f"Object {obj.id} must reference an existing window")
                if obj.wall != opening.wall:
                    raise ValueError(f"Object {obj.id} must use its window's wall")
        return self
