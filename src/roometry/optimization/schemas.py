"""A saved improvement request against one immutable reconstruction snapshot."""

from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, field_validator, model_validator

from roometry.agents.state import RunModel, RunLimits, RunUsage
from roometry.assessment.schemas import RoomAssessment, AestheticComparison
from roometry.scenes.hashing import scene_hash
from roometry.scenes.schemas import RoomScene, Identifier, Color, MaterialColors, FiniteNumber
from roometry.scenes.edit_schemas import FloorPosition
from roometry.scenes.scoring import Criterion
from roometry.uploads.schemas import InputBudget, OpaqueId


class OptimizationPreferences(RunModel):
    budget: InputBudget | None = None
    style: Annotated[str, Field(max_length=100)] | None = None
    constraints: Annotated[str, Field(max_length=1000)] | None = None

    @field_validator("style", "constraints", mode="before")
    @classmethod
    def trim_text(cls, value: object) -> object:
        return (value.strip() or None) if isinstance(value, str) else value


class SceneContext(RunModel):
    scene: RoomScene
    parent_run_id: OpaqueId | None = None


class StartOptimizationRequest(RunModel):
    request_id: OpaqueId = Field(description="Reuse this ID and the identical request when retrying.")
    focus: Criterion
    preferences: OptimizationPreferences = Field(default_factory=OptimizationPreferences)
    context: SceneContext | None = None
    instruction: Annotated[str, Field(min_length=1, max_length=1000)] | None = None

    @field_validator("instruction", mode="before")
    @classmethod
    def trim_instruction(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def refinement_context(self) -> Self:
        if self.instruction is not None and self.context is None:
            raise ValueError("A refinement needs the current scene snapshot.")
        return self


class ObjectChange(RunModel):
    object_id: Identifier
    position: FloorPosition | None
    rotation_y_rad: FiniteNumber | None
    color: Color | None
    material_colors: MaterialColors | None


class CandidateProposal(RunModel):
    changes: Annotated[list[ObjectChange], Field(max_length=24)]
    summary: Annotated[str, Field(min_length=1, max_length=500)]
    suggestions: Annotated[list[Annotated[str, Field(max_length=400)]], Field(max_length=6)]
    finish: bool = False


class CandidateReview(RunModel):
    preferred: Literal["original", "candidate", "uncertain"]
    confidence: Literal["low", "medium", "high"]
    constraints_satisfied: bool
    explanation: Annotated[str, Field(min_length=1, max_length=500)]
    aesthetics: AestheticComparison | None = None


class OptimizationAttempt(RunModel):
    number: int = Field(ge=1, le=5)
    outcome: Literal["accepted", "rejected", "no_gain"]
    before_score: float | None
    after_score: float | None
    changed_object_ids: Annotated[list[Identifier], Field(max_length=200)]
    issues: Annotated[list[Annotated[str, Field(max_length=500)]], Field(max_length=16)]
    summary: Annotated[str, Field(max_length=500)]
    review: CandidateReview | None = None


class OptimizationRun(RunModel):
    run_id: OpaqueId
    source_run_id: OpaqueId
    input_id: OpaqueId
    source_scene_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_scene: RoomScene
    source_status: Literal["completed", "partial"]
    baseline_assessment: RoomAssessment | None
    focus: Criterion
    preferences: OptimizationPreferences
    instruction: Annotated[str, Field(min_length=1, max_length=1000)] | None = None
    parent_run_id: OpaqueId | None = None
    created_at: AwareDatetime
    expires_at: AwareDatetime
    status: Literal["prepared", "running", "completed", "no_improvement", "cancelled", "failed", "budget_exhausted"] = "prepared"
    stage: Literal["prepared", "proposing", "validating", "reviewing", "finished"] = "prepared"
    max_candidates: int = Field(default=3, ge=1, le=5)
    limits: RunLimits = Field(default_factory=lambda: RunLimits(max_model_calls=6))
    usage: RunUsage = Field(default_factory=RunUsage)
    attempts: Annotated[list[OptimizationAttempt], Field(max_length=5)] = Field(default_factory=list)
    result_scene: RoomScene | None = None
    result_assessment: RoomAssessment | None = None
    suggestions: Annotated[list[Annotated[str, Field(max_length=400)]], Field(max_length=6)] = Field(default_factory=list)
    message: Annotated[str, Field(max_length=500)] = "Ready to run the selected improvement."
    limitations: Annotated[list[str], Field(max_length=12)] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent_snapshot(self) -> Self:
        if self.created_at >= self.expires_at or self.source_scene_hash != scene_hash(self.source_scene):
            raise ValueError("The improvement request needs a valid source snapshot and lifetime.")
        if self.baseline_assessment and self.baseline_assessment.scene_hash != self.source_scene_hash:
            raise ValueError("The baseline rating belongs to another scene snapshot.")
        if self.result_assessment and (self.result_scene is None or self.result_assessment.scene_hash != scene_hash(self.result_scene)):
            raise ValueError("Result ratings must describe the retained candidate.")
        if self.result_scene and (self.result_scene.room != self.source_scene.room or self.result_scene.openings != self.source_scene.openings):
            raise ValueError("Optimization must preserve room measurements and openings.")
        if self.status == "completed" and (self.result_scene is None or self.result_scene == self.source_scene):
            raise ValueError("Completed optimization requires an actual retained change.")
        return self
