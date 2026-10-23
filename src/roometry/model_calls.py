"""Optional per-run accounting at the shared provider boundary.

No observer means the existing standalone analysis APIs behave as before.
Observers receive usage only, never photo bytes, prompts or API keys.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal, Protocol

from openai.types.responses import ResponseUsage

ModelOperation = Literal[
    "analysis", "scene_proposal", "scene_repair", "visual_review", "visual_repair", "agent_decision"
]
ReasoningEffort = Literal["default", "none", "low", "medium", "high", "xhigh", "max"]


@dataclass(frozen=True)
class ModelRoute:
    model: str
    reasoning_effort: ReasoningEffort


@dataclass(frozen=True)
class ModelAttempt:
    """Provider-level facts only; semantic validation is logged by its service."""

    operation: ModelOperation
    run_id: str | None
    requested_model: str
    model: str
    outcome: str
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    cached_input_tokens: int | None
    reasoning_tokens: int | None
    response_id: str | None
    provider_request_id: str | None


class ModelCallObserver(Protocol):
    async def before_call(self) -> None: ...
    def after_call(self, usage: ResponseUsage | None) -> None: ...


_observer: ContextVar[ModelCallObserver | None] = ContextVar(
    "roometry_model_observer", default=None
)


def current_observer() -> ModelCallObserver | None:
    return _observer.get()


@contextmanager
def observe_model_calls(observer: ModelCallObserver | None) -> Iterator[None]:
    token = _observer.set(observer)
    try:
        yield
    finally:
        _observer.reset(token)
