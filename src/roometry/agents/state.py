"""Typed execution state; runtime clients, owner tokens and image bytes stay out."""

from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    model_validator,
)
from typing_extensions import TypedDict

from roometry.scenes.schemas import Identifier, RoomScene
from roometry.uploads.schemas import (
    MAX_IMAGES,
    Height,
    InputDimensions,
    OpaqueId,
    RoomInputOptions,
    Side,
)
from roometry.vision.proposal_schemas import DimensionName, SceneProposalResponse
from roometry.vision.schemas import ReconstructionConstraints, RoomAnalysisResponse

ClarificationKind = Literal[
    "room_dimensions", "object_mobility", "object_identity", "layout_orientation"
]

RunStatus = Literal[
    "queued",
    "running",
    "awaiting_answer",
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
        extra="forbid",
        strict=True,
        frozen=True,
        revalidate_instances="always",
        json_schema_serialization_defaults_required=True,
    )


class RunLimits(RunModel):
    max_steps: int = Field(default=16, ge=1, le=32)
    step_timeout_seconds: float = Field(default=90, gt=0, le=610, allow_inf_nan=False)
    max_model_calls: int = Field(default=20, ge=1, le=32)
    max_tool_calls: int = Field(default=24, ge=1, le=64)


class RunUsage(RunModel):
    """Actual usage reported by a trusted adapter, never by an LLM decision.

    RunBudget reserves calls before dispatch, including nested service retries.
    Unknown provider usage is explicit. These counters are not dollar billing.
    """

    model_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    unknown_usage_calls: int = Field(default=0, ge=0)


ToolName = Literal[
    "analyze_photos",
    "propose_scene",
    "read_scene",
    "search_catalog",
    "move_object",
    "rotate_object",
    "validate_scene",
    "request_clarification",
    "finish",
]


class ToolCall(RunModel):
    call_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    name: ToolName
    arguments: dict[str, JsonValue]


class ToolFeedback(RunModel):
    name: ToolName | None
    outcome: Literal["success", "rejected", "error"]
    message: str = Field(min_length=1, max_length=1000)
    details: dict[str, JsonValue] = Field(default_factory=dict)


class ClarificationChoice(RunModel):
    id: Identifier
    label: str = Field(min_length=1, max_length=200)


class EnteredDimensions(RunModel):
    width_m: Side | None = None
    length_m: Side | None = None
    height_m: Height | None = None


class ClarificationAnswer(RunModel):
    question_id: Identifier
    choice_id: Identifier
    dimensions: EnteredDimensions | None = Field(
        default=None,
        description="Only for enter_measurements: supply the requested axes in meters. Entered values are treated as user supplied, without a separate confirmation flag.",
    )


class ClarificationQuestion(RunModel):
    """Three server-defined actions for one specific missing fact."""

    id: Identifier
    question: str = Field(min_length=1, max_length=500)
    choices: Annotated[list[ClarificationChoice], Field(min_length=3, max_length=3)]
    entity_ids: Annotated[list[Identifier], Field(max_length=200)] = Field(
        default_factory=list
    )
    kind: ClarificationKind
    dimension_axes: Annotated[list[DimensionName], Field(max_length=3)] = Field(
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
    questions: Annotated[list[ClarificationQuestion], Field(max_length=3)] = Field(
        default_factory=list
    )
    submitted_answers: Annotated[
        dict[Identifier, ClarificationAnswer], Field(max_length=3)
    ] = Field(default_factory=dict)
    reconstruction_constraints: ReconstructionConstraints = Field(
        default_factory=ReconstructionConstraints
    )
    object_mobility: Annotated[dict[Identifier, bool | None], Field(max_length=3)] = (
        Field(default_factory=dict)
    )
    base_scene: RoomScene | None = None
    working_scene: RoomScene | None = None
    pending_tool: ToolCall | None = None
    tool_feedback: Annotated[list[ToolFeedback], Field(max_length=16)] = Field(
        default_factory=list
    )
    events: Annotated[list[RunEvent], append_events] = Field(default_factory=list)
    stop_reason: Literal["finished", "budget_limit"] | None = None
    last_error: (
        Literal[
            "step_failed",
            "step_timeout",
            "invalid_step",
            "decision_failed",
            "tool_failed",
        ]
        | None
    ) = None

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
    """Narrow adapter output; only trusted usage increments are accepted."""

    action: Literal["continue", "finish"]
    analysis: RoomAnalysisResponse | None = None
    proposal: SceneProposalResponse | None = None
    usage: RunUsage = Field(default_factory=RunUsage)
    pending_tool: ToolCall | None = None
    feedback: ToolFeedback | None = None
    working_scene: RoomScene | None = None
    pending_question: ClarificationQuestion | None = None
    failure: Literal["decision_failed", "tool_failed"] | None = None
