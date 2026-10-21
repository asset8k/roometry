"""Reserve each real model/tool call before dispatch, including nested repairs."""

from collections.abc import Awaitable, Callable

from openai.types.responses import ResponseUsage

from roometry.agents.state import RunLimits, RunUsage
from roometry.vision.errors import AnalysisFailure


class RunBudget:
    def __init__(
        self,
        limits: RunLimits,
        usage: RunUsage,
        authorize: Callable[[], Awaitable[None]],
        cancelled: Callable[[], bool],
    ):
        self.limits, self.usage = limits, usage
        self.authorize, self.cancelled = authorize, cancelled
        self.exhausted = False

    def sync(self, usage: RunUsage) -> None:
        self.usage = RunUsage(
            **{
                key: max(value, getattr(self.usage, key))
                for key, value in usage.model_dump().items()
            }
        )

    async def before_call(self) -> None:
        await self.authorize()
        if self.cancelled() or self.usage.model_calls >= self.limits.max_model_calls:
            self.exhausted = True
            # AnalysisService already knows how to retain an earlier valid
            # partial when its bounded repair cannot proceed.
            raise AnalysisFailure(
                429, "analysis_limit_reached",
                "The run cannot dispatch another model call.",
            )
        self.usage = self.usage.model_copy(
            update={
                "model_calls": self.usage.model_calls + 1,
                "unknown_usage_calls": self.usage.unknown_usage_calls + 1,
            }
        )

    def after_call(self, usage: ResponseUsage | None) -> None:
        if usage is not None:
            self.usage = self.usage.model_copy(
                update={
                    "input_tokens": self.usage.input_tokens + usage.input_tokens,
                    "output_tokens": self.usage.output_tokens + usage.output_tokens,
                    "unknown_usage_calls": self.usage.unknown_usage_calls - 1,
                }
            )

    async def reserve_tool(self) -> None:
        await self.authorize()
        if self.cancelled() or self.usage.tool_calls >= self.limits.max_tool_calls:
            self.exhausted = True
            raise AnalysisFailure(
                429, "analysis_limit_reached",
                "The run cannot dispatch another tool call.",
            )
        self.usage = self.usage.model_copy(
            update={"tool_calls": self.usage.tool_calls + 1}
        )

    def delta(self, before: RunUsage) -> RunUsage:
        return RunUsage(
            **{
                key: max(0, value - getattr(before, key))
                for key, value in self.usage.model_dump().items()
            }
        )
