"""Optional per-run accounting at the shared provider boundary.

No observer means the existing standalone analysis APIs behave as before.
Observers receive usage only, never photo bytes, prompts or API keys.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Protocol

from openai.types.responses import ResponseUsage


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
