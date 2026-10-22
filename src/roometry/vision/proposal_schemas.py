"""Measurement confirmation, constrained model drafts, and validated proposals."""

from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, field_validator, model_validator

from roometry.scenes.assets import Placement
from roometry.scenes.schemas import (
    AssetId,
    EstimateCorrection,
    FiniteNumber,
    Identifier,
    NonnegativeDistance,
    ObjectDimensions,
    Position,
    PositiveDistance,
    RoomScene,
    SupportSurfaceId,
    Wall,
)
from roometry.scenes.validation import GeometryReport
from roometry.uploads.schemas import InputDimensions, OpaqueId
from roometry.vision.fidelity import FidelityReport
from roometry.vision.schemas import AnalysisModel, Notes, RoomHeight, SillHeight, ReconstructionConstraints

DimensionName = Literal["width_m", "length_m", "height_m"]
AXES: tuple[DimensionName, ...] = ("width_m", "length_m", "height_m")


class SceneProposalRequest(AnalysisModel):
    analysis_id: OpaqueId
    dimensions: InputDimensions | None = None
    allow_estimated_dimensions: bool = False
    constraints: ReconstructionConstraints = Field(default_factory=ReconstructionConstraints)

    @model_validator(mode="after")
    def confirmed_input(self) -> Self:
        if (
            self.dimensions is not None
            and self.dimensions.measurements_source != "user_confirmed"
        ):
            raise ValueError("Submitted measurements must be user confirmed.")
        return self


class WallAssignment(AnalysisModel):
    observation_id: Identifier
    wall: Wall


class ObjectPose(AnalysisModel):
    id: Identifier
    position: Position
    rotation_y_rad: FiniteNumber
    placement: Placement
    wall: Wall | None
    support_object_id: Identifier | None
    opening_id: Identifier | None
    support_surface_id: SupportSurfaceId | None


class DraftObject(ObjectPose):
    asset_id: AssetId | None
    # null asks the server for catalog defaults, overlaid with recorded estimates.
    dimensions_m: ObjectDimensions | None


class BoundObject(ObjectPose):
    asset_id: AssetId
    dimensions_m: ObjectDimensions


class DraftOpening(AnalysisModel):
    id: Identifier
    wall: Wall
    offset_m: NonnegativeDistance
    width_m: PositiveDistance
    height_m: RoomHeight
    sill_height_m: SillHeight


class DraftContent(AnalysisModel):
    estimate_corrections: Annotated[list[EstimateCorrection], Field(max_length=200)] = Field(default_factory=list)
    wall_assignments: Annotated[list[WallAssignment], Field(max_length=4)]
    openings: Annotated[list[DraftOpening], Field(max_length=32)]
    assumptions: Notes
    warnings: Notes


class SceneDraft(DraftContent):
    """Untrusted model selections and poses; the server resolves and checks them."""

    objects: Annotated[list[DraftObject], Field(max_length=200)]


class BoundDraft(DraftContent):
    objects: Annotated[list[BoundObject], Field(max_length=200)]


class ProposalIssue(AnalysisModel):
    code: Literal[
        "unsupported_room_shape",
        "unknown_object",
        "unsupported_placement",
        "asset_mismatch",
        "insufficient_evidence",
        "draft_schema",
        "identity_mismatch",
        "wall_mapping",
        "placement_mismatch",
        "dimension_mismatch",
        "support_mismatch",
        "scene_schema",
        "geometry",
        "proposal_limit_reached",
        "dependency_unresolved",
        "repair_failed",
        "fidelity",
    ]
    entity_ids: Annotated[list[Identifier], Field(max_length=200)]
    message: Annotated[str, Field(min_length=1, max_length=1000)]


class UnresolvedEntity(AnalysisModel):
    """An original observation omitted from the validated scene, never deleted."""

    entity_id: Identifier
    kind: Literal["object", "opening"]
    reason: ProposalIssue

    @model_validator(mode="after")
    def identifies_entity(self) -> Self:
        if self.entity_id not in self.reason.entity_ids:
            raise ValueError("An unresolved reason must identify its observation.")
        return self


class SceneProposalResponse(AnalysisModel):
    analysis_id: OpaqueId
    input_id: OpaqueId
    status: Literal["needs_dimensions", "ready", "partial", "incomplete"]
    dimensions: InputDimensions
    dimensions_to_confirm: Annotated[list[DimensionName], Field(max_length=3)]
    estimated_axes: Annotated[list[DimensionName], Field(max_length=3)]
    scene: RoomScene | None
    validation: GeometryReport | None
    fidelity: FidelityReport | None = None
    issues: Annotated[list[ProposalIssue], Field(max_length=50)]
    unresolved_entities: Annotated[list[UnresolvedEntity], Field(max_length=232)] = Field(default_factory=list)
    warnings: Notes
    expires_at: AwareDatetime

    @field_validator("validation", mode="before")
    @classmethod
    def read_serialized_report(cls, value: object) -> object:
        # `valid` is computed by GeometryReport. Permit round trips through its
        # public serialization, but never accept a contradictory supplied flag.
        if isinstance(value, dict) and "valid" in value:
            if value["valid"] is not (not value.get("errors")):
                raise ValueError("Geometry validity must agree with its errors.")
            return {key: item for key, item in value.items() if key != "valid"}
        return value

    @model_validator(mode="after")
    def consistent_status(self) -> Self:
        for axes in (self.dimensions_to_confirm, self.estimated_axes):
            if len(set(axes)) != len(axes):
                raise ValueError("Dimension axes must be unique.")
        if any(getattr(self.dimensions, axis) is None for axis in self.estimated_axes):
            raise ValueError("Estimated axes must have values.")
        if (
            self.dimensions.measurements_source == "user_confirmed"
            and self.estimated_axes
        ):
            raise ValueError("Confirmed measurements cannot include estimated axes.")
        unresolved = {item.entity_id for item in self.unresolved_entities}
        if len(unresolved) != len(self.unresolved_entities):
            raise ValueError("Unresolved observations must be unique.")
        if self.status in {"ready", "partial"}:
            if (
                self.scene is None
                or self.validation is None
                or not self.validation.valid
                or self.dimensions_to_confirm
            ):
                raise ValueError(
                    "Renderable proposals require a valid scene and resolved dimensions."
                )
            retained = {item.id for item in [*self.scene.objects, *self.scene.openings]}
            if retained & unresolved:
                raise ValueError("Unresolved observations cannot appear in the scene.")
            if self.status == "ready" and (self.issues or unresolved):
                raise ValueError("Ready proposals cannot have unresolved issues.")
            fidelity_pending = any(item.code == "fidelity" for item in self.issues)
            if self.status == "partial" and (
                not retained or not self.issues or (not unresolved and not fidelity_pending)
            ):
                raise ValueError("Partial proposals require retained observations and explicit omissions or fidelity issues.")
            if self.status == "ready" and self.fidelity is not None and any(
                check.required and not check.passed for check in self.fidelity.checks
            ):
                raise ValueError("Required fidelity failures cannot be labeled ready.")
            expected = self.dimensions.model_dump(
                exclude_none=True, exclude={"measurements_source"}
            )
            if len(expected) != 3 or any(
                getattr(self.scene.room, key) != value
                for key, value in expected.items()
            ):
                raise ValueError("Scene dimensions must match resolved measurements.")
            if (
                self.scene.room.measurements_source
                != self.dimensions.measurements_source
            ):
                raise ValueError("Measurement provenance must match.")
            if bool(self.estimated_axes) != (
                self.dimensions.measurements_source == "estimated"
            ):
                raise ValueError("Identify every unconfirmed scene dimension.")
        elif self.scene is not None:
            raise ValueError("Unvalidated proposals cannot expose a scene.")
        if self.status == "needs_dimensions" and not self.dimensions_to_confirm:
            raise ValueError("Missing dimensions must be identified.")
        if self.status == "needs_dimensions" and self.unresolved_entities:
            raise ValueError("Resolve dimensions before selecting a partial scene.")
        if self.status == "incomplete" and not self.issues:
            raise ValueError("Incomplete proposals must explain why.")
        return self
