"""Typed execution state; runtime clients, owner tokens and image bytes stay out.

This foundation does not choose tools or ask questions yet. The injected step
adapter is connected to real agent decisions in the next implementation step.
"""

from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from typing_extensions import TypedDict

from roometry.scenes.schemas import Identifier, RoomScene
from roometry.uploads.schemas import (
    MAX_IMAGES,
    InputDimensions,
    OpaqueId,
    RoomInputOptions,
)
from roometry.vision.proposal_schemas import SceneProposalResponse
from roometry.vision.schemas import RoomAnalysisResponse

RunStatus = Literal[
    "queued",
    "running",
    "completed",
    "partial",
    "incomplete",
    "budget_exhausted",
    "cancelled",
    "expired",
    "failed",
]


class RunModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, revalidate_instances="always"
    )


class RunLimits(RunModel):
    max_steps: int = Field(default=8, ge=1, le=32)
    step_timeout_seconds: float = Field(default=90, gt=0, le=300, allow_inf_nan=False)
    max_model_calls: int = Field(default=8, ge=1, le=32)
    max_tool_calls: int = Field(default=16, ge=1, le=64)


class RunUsage(RunModel):
    """Actual usage reported by a trusted adapter, never by an LLM decision.

    Step 2 must reserve/check individual paid calls before dispatch, including
    any retries inside existing services. These counters alone are not billing.
    """

    model_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class ClarificationChoice(RunModel):
    id: Identifier
    label: str = Field(min_length=1, max_length=200)


class ClarificationQuestion(RunModel):
    """Reserved state payload; actual interrupt/answer handling belongs to step 3."""

    id: Identifier
    question: str = Field(min_length=1, max_length=500)
    choices: Annotated[list[ClarificationChoice], Field(min_length=3, max_length=3)]
    entity_ids: Annotated[list[Identifier], Field(max_length=200)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def unique_choices(self) -> Self:
        if len({choice.id for choice in self.choices}) != 3:
            raise ValueError("Question choice IDs must be unique.")
        return self


class RunEvent(TypedDict):
    step: int
    status: RunStatus


def append_events(left: list[RunEvent], right: list[RunEvent]) -> list[RunEvent]:
    """Bounded reducer: merge node deltas without growing checkpoints forever."""
    return [*left, *right][-64:]


class AgentState(RunModel):
    run_id: OpaqueId
    input_id: OpaqueId
    image_ids: Annotated[list[OpaqueId], Field(min_length=1, max_length=MAX_IMAGES)]
    input_options: RoomInputOptions
    created_at: AwareDatetime
    input_expires_at: AwareDatetime
    expires_at: AwareDatetime
    limits: RunLimits = Field(default_factory=RunLimits)
    status: RunStatus = "queued"
    steps_used: int = Field(default=0, ge=0)
    usage: RunUsage = Field(default_factory=RunUsage)
    continue_run: bool = True
    analysis: RoomAnalysisResponse | None = None
    proposal: SceneProposalResponse | None = None
    confirmed_dimensions: InputDimensions | None = None
    # Human answers are kept separate from immutable photo observations.
    clarification_answers: Annotated[
        dict[Identifier, Identifier], Field(max_length=3)
    ] = Field(default_factory=dict)
    pending_question: ClarificationQuestion | None = None
    base_scene: RoomScene | None = None
    working_scene: RoomScene | None = None
    events: Annotated[list[RunEvent], append_events] = Field(default_factory=list)
    last_error: Literal["step_failed", "step_timeout", "invalid_step"] | None = None

    @model_validator(mode="after")
    def consistent_input(self) -> Self:
        if not self.created_at < self.expires_at <= self.input_expires_at or len(
            set(self.image_ids)
        ) != len(self.image_ids):
            raise ValueError("Run lifetime and image references must be valid.")
        if (
            self.confirmed_dimensions is not None
            and self.confirmed_dimensions.measurements_source != "user_confirmed"
        ):
            raise ValueError("Confirmed dimensions must retain user provenance.")
        return self


class RunStep(RunModel):
    """Narrow adapter output: adapters cannot rewrite ownership, limits or counters."""

    action: Literal["continue", "finish"]
    analysis: RoomAnalysisResponse | None = None
    proposal: SceneProposalResponse | None = None
    usage: RunUsage = Field(default_factory=RunUsage)
