"""Photo observations and public analysis DTOs; no model calls or scene generation.

Observations are hypotheses, not verified geometry or authorized user constraints.
Nullable fields are required in model output so unknowns stay explicit. The later
service must check image IDs against the owned upload; these schemas cannot prove
ownership or that an observation is factually correct.
"""

from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from roometry.scenes.assets import ObjectType, Placement
from roometry.scenes.schemas import (
    MAX_NOTES,
    MAX_OBJECTS,
    MAX_OPENINGS,
    MAX_ROOM_HEIGHT_M,
    MAX_ROOM_SIDE_M,
    Color,
    Identifier,
    Note,
    PositiveDistance,
    RoomType,
)
from roometry.uploads.schemas import MAX_IMAGES, OpaqueId, RoomInputOptions

Confidence = Literal["low", "medium", "high"]
"""Qualitative model confidence, never a calibrated probability."""
ImageReferences = Annotated[list[OpaqueId], Field(min_length=1, max_length=MAX_IMAGES)]
Notes = Annotated[list[Note], Field(max_length=MAX_NOTES)]
Label = Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
RoomSide = Annotated[float, Field(gt=0, le=MAX_ROOM_SIDE_M, allow_inf_nan=False)]
RoomHeight = Annotated[float, Field(gt=0, le=MAX_ROOM_HEIGHT_M, allow_inf_nan=False)]
SillHeight = Annotated[float, Field(ge=0, le=MAX_ROOM_HEIGHT_M, allow_inf_nan=False)]


class AnalysisModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, revalidate_instances="always"
    )


class EstimatedRoomDimensions(AnalysisModel):
    """Photo estimates only; user measurements live separately in input_options."""

    width_m: RoomSide | None
    length_m: RoomSide | None
    height_m: RoomHeight | None

    @model_validator(mode="after")
    def has_estimate(self) -> Self:
        if all(value is None for value in (self.width_m, self.length_m, self.height_m)):
            raise ValueError("Use null when no room dimensions can be estimated.")
        return self


class EstimatedObjectDimensions(AnalysisModel):
    width: PositiveDistance | None
    depth: PositiveDistance | None
    height: PositiveDistance | None

    @model_validator(mode="after")
    def has_estimate(self) -> Self:
        if all(value is None for value in (self.width, self.depth, self.height)):
            raise ValueError("Use null when no object dimensions can be estimated.")
        return self


class WallObservation(AnalysisModel):
    """A stable descriptive wall ID, not a compass direction inferred from a photo."""

    id: Identifier
    label: Label
    source_image_ids: ImageReferences
    confidence: Confidence
    notes: Notes


class ObjectObservation(AnalysisModel):
    """One tentative physical instance; multiple chairs retain separate IDs.

    Relative location is descriptive and may be unknown. A wall_id is a spatial
    reference, including for nearby floor objects. Surface placement may be known
    even when its support is occluded. No asset, metric position, lock, or trusted
    fixed flag is assigned by this observation contract.
    """

    id: Identifier
    type: ObjectType | None
    label: Label
    source_image_ids: ImageReferences
    confidence: Confidence
    relative_location: Note | None
    wall_id: Identifier | None
    placement: Placement | None
    support_object_id: Identifier | None
    appears_fixed: bool | None
    color: Color | None
    estimated_dimensions_m: EstimatedObjectDimensions | None
    notes: Notes

    @model_validator(mode="after")
    def support_matches_placement(self) -> Self:
        if self.support_object_id is not None and self.placement != "surface":
            raise ValueError("A support reference requires surface placement.")
        return self


class OpeningObservation(AnalysisModel):
    id: Identifier
    type: Literal["door", "window"]
    source_image_ids: ImageReferences
    confidence: Confidence
    wall_id: Identifier | None
    relative_location: Note | None
    estimated_width_m: RoomSide | None
    estimated_height_m: RoomHeight | None
    estimated_sill_height_m: SillHeight | None
    notes: Notes

    @model_validator(mode="after")
    def door_starts_at_floor(self) -> Self:
        if self.type == "door" and self.estimated_sill_height_m not in (None, 0):
            raise ValueError("A door sill is unknown or at floor level.")
        return self


class PossibleDuplicate(AnalysisModel):
    """An unresolved match, not permission to merge or discard either instance."""

    entity_ids: Annotated[list[Identifier], Field(min_length=2, max_length=2)]
    reason: Note


class RoomObservations(AnalysisModel):
    schema_version: Literal[1]
    image_ids: ImageReferences
    room_type: RoomType | None
    room_type_confidence: Confidence | None
    room_shape: Literal["rectangular", "non_rectangular", "unknown"]
    floor_visible: bool | None
    ceiling_visible: bool | None
    estimated_dimensions: EstimatedRoomDimensions | None
    walls: Annotated[list[WallObservation], Field(max_length=4)]
    objects: Annotated[list[ObjectObservation], Field(max_length=MAX_OBJECTS)]
    openings: Annotated[list[OpeningObservation], Field(max_length=MAX_OPENINGS)]
    possible_duplicates: Annotated[list[PossibleDuplicate], Field(max_length=100)]
    unknowns: Notes
    assumptions: Notes
    warnings: Notes

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_version(cls, version: object) -> object:
        if type(version) is not int:
            raise ValueError("schema_version must be the integer 1")
        return version

    @model_validator(mode="after")
    def consistent_references(self) -> Self:
        image_ids = set(self.image_ids)
        if len(image_ids) != len(self.image_ids):
            raise ValueError("Analysis image IDs must be unique.")
        if (self.room_type is None) != (self.room_type_confidence is None):
            raise ValueError(
                "Unknown room type requires null confidence, and vice versa."
            )

        entities = [*self.walls, *self.objects, *self.openings]
        by_id = {entity.id: entity for entity in entities}
        if len(by_id) != len(entities):
            raise ValueError("Observation IDs must be unique across all entity kinds.")
        for entity in entities:
            sources = entity.source_image_ids
            if len(set(sources)) != len(sources) or not set(sources) <= image_ids:
                raise ValueError("Evidence must reference distinct analysis image IDs.")

        walls = {wall.id for wall in self.walls}
        objects = {obj.id: obj for obj in self.objects}
        for entity in [*self.objects, *self.openings]:
            if entity.wall_id is not None and entity.wall_id not in walls:
                raise ValueError("Wall references must identify an observed wall.")
        for obj in self.objects:
            visited = {obj.id}
            support_id = obj.support_object_id
            while support_id is not None:
                if support_id not in objects or support_id in visited:
                    raise ValueError(
                        "Object support references must exist and be acyclic."
                    )
                visited.add(support_id)
                support_id = objects[support_id].support_object_id

        pairs: set[frozenset[str]] = set()
        for match in self.possible_duplicates:
            pair = frozenset(match.entity_ids)
            if len(pair) != 2 or pair in pairs:
                raise ValueError(
                    "Possible duplicate pairs must be distinct and unique."
                )
            if not pair <= by_id.keys():
                raise ValueError(
                    "Possible duplicates must reference observed entities."
                )
            first, second = (by_id[entity_id] for entity_id in match.entity_ids)
            if type(first) is not type(second):
                raise ValueError(
                    "Possible duplicates must refer to the same entity kind."
                )
            pairs.add(pair)
        return self


class AnalyzeRoomRequest(AnalysisModel):
    """The service resolves options/photos from this owned, unexpired input ID."""

    input_id: OpaqueId


class RoomAnalysisResponse(AnalysisModel):
    """An analyzed input, not a scene, optimization result, or durable project.

    The server supplies IDs, times and input_options; the vision model supplies
    only observations. Confirmation/scene proposal contracts follow separately.
    """

    analysis_id: OpaqueId
    input_id: OpaqueId
    status: Literal["analyzed"]
    created_at: AwareDatetime
    expires_at: AwareDatetime
    input_options: RoomInputOptions
    observations: RoomObservations

    @model_validator(mode="after")
    def expiry_follows_creation(self) -> Self:
        if self.expires_at <= self.created_at:
            raise ValueError("Analysis expiry must follow its creation time.")
        return self
