"""Independent reference annotations and local-only evaluation recordings."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from roometry.scenes.assets import ObjectType
from roometry.scenes.schemas import Color, Identifier, RoomFinishes


class ReferenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ReferenceObject(ReferenceModel):
    id: Identifier
    types: list[ObjectType] = Field(default_factory=list)
    opening_type: Literal["window", "door"] | None = None
    label_terms: list[str] = Field(default_factory=list)
    color: Color | None = None
    material_colors: dict[str, Color] = Field(default_factory=dict)
    allowed_assets: list[str] = Field(default_factory=list)
    wall_span_fraction: tuple[float, float] | None = None
    width_depth_ratio: tuple[float, float] | None = None


class ReferenceRelation(ReferenceModel):
    kind: Literal["faces", "near", "same_end", "opposite_end"]
    subject: Identifier
    target: Identifier


class ReconstructionCase(ReferenceModel):
    id: Identifier
    photo_files: list[str]
    annotation_notes: list[str]
    room_finishes: RoomFinishes | None = None
    objects: Annotated[list[ReferenceObject], Field(min_length=1)]
    relationships: list[ReferenceRelation]


InventoryLossStage = Literal[
    "analysis_unavailable", "analysis", "catalog", "construction",
    "validation", "construction_or_validation",
]


class InventoryTrace(ReferenceModel):
    expected_id: Identifier
    observed_id: Identifier | None
    detected: bool | None
    catalog_compatible: bool | None
    drafted: bool | None
    retained: bool
    loss_stage: InventoryLossStage | None
    issue_codes: list[str]


class EvaluationReport(ReferenceModel):
    case_id: Identifier
    passed: bool
    major_object_coverage: float
    wall_relationships: float | None
    orientation: float | None
    proportions: float | None
    color_consistency: float | None
    asset_match: float | None
    failures: list[str]
    matched_ids: dict[str, str]
    inventory: list[InventoryTrace] = Field(default_factory=list)


class SavedRoomEvidence(ReferenceModel):
    """Receipt from the authenticated recorder, never credentials or API keys."""

    room_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    account_id: str = Field(min_length=1, max_length=128)
    published: bool


class RecordedTrial(ReferenceModel):
    """Paths resolve below the recording index directory; URLs are forbidden."""

    evaluation_id: Identifier
    case_id: Identifier
    kind: Literal["reconstruction", "improvement"]
    run: str
    repeat: bool = False
    parent_evaluation_id: Identifier | None = None
    cost: str | None = None
    timing: str | None = None
    draft: str | None = None
    flow_checks: str | None = None
    saved_room: SavedRoomEvidence | None = None
    # Explicit identity review can resolve ambiguous repeated instances. It
    # identifies objects only; it cannot override geometry or relationship checks.
    identity_matches: dict[Identifier, Identifier] | None = None


class RecordingIndex(ReferenceModel):
    schema_version: Literal[1]
    recording_mode: Literal["mock", "live"]
    dataset_fingerprint_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    account_id: str | None = None
    trials: list[RecordedTrial] = Field(default_factory=list)
