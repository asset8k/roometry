"""Deterministic lifecycle nodes around an injected, asynchronous step adapter."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import anyio
from langgraph.runtime import Runtime
from pydantic import ValidationError

from roometry.agents.state import AgentState, RunStep, RunUsage
from roometry.scenes.validation import validate_geometry


class StepAdapter(Protocol):
    async def __call__(self, state: AgentState) -> RunStep: ...


@dataclass(frozen=True)
class RunContext:
    """Injected per invocation; never serialized in graph checkpoints."""

    advance: StepAdapter
    authorize: Callable[[], Awaitable[None]]
    cancelled: Callable[[], bool]
    now: Callable[[], datetime]


async def guard(state: AgentState, runtime: Runtime[RunContext]) -> dict[str, object]:
    context = runtime.context
    await context.authorize()
    if context.cancelled():
        return {"status": "cancelled"}
    if context.now() >= state.expires_at:
        return {"status": "expired"}
    if state.status not in {"queued", "running"} or not state.continue_run:
        return {}
    if (
        state.steps_used >= state.limits.max_steps
        or state.usage.model_calls >= state.limits.max_model_calls
        or state.usage.tool_calls >= state.limits.max_tool_calls
    ):
        return {"status": "budget_exhausted"}
    return {"status": "running", "steps_used": state.steps_used + 1}


def check_step(state: AgentState, step: RunStep) -> None:
    """Only cache observations and scenes belonging to this exact room input."""
    analysis = step.analysis or state.analysis
    if step.analysis is not None:
        if (
            step.analysis.input_id != state.input_id
            or step.analysis.expires_at != state.input_expires_at
            or step.analysis.observations.image_ids != state.image_ids
            or step.analysis.input_options != state.input_options
            or (state.analysis is not None and step.analysis != state.analysis)
        ):
            raise ValueError("Analysis does not match this run's original input.")
    proposal = step.proposal
    if proposal is not None:
        if (
            analysis is None
            or proposal.analysis_id != analysis.analysis_id
            or proposal.input_id != state.input_id
            or proposal.expires_at != analysis.expires_at
        ):
            raise ValueError("Proposal does not belong to this analysis.")
        confirmed = state.confirmed_dimensions
        if confirmed is not None and any(
            value is not None and getattr(proposal.dimensions, axis) != value
            for axis, value in confirmed.model_dump(
                exclude={"measurements_source"}
            ).items()
        ):
            raise ValueError("Proposal changed confirmed room dimensions.")
        estimated = {
            axis
            for axis in ("width_m", "length_m", "height_m")
            if getattr(proposal.dimensions, axis) is not None
            and (confirmed is None or getattr(confirmed, axis) is None)
        }
        if set(proposal.estimated_axes) != estimated:
            raise ValueError("Unconfirmed dimensions must remain labeled as estimates.")
        if proposal.scene is not None and not validate_geometry(proposal.scene).valid:
            raise ValueError("Proposal contains invalid geometry.")
        if proposal.scene is not None:
            expected = {
                item.id
                for item in [
                    *analysis.observations.objects,
                    *analysis.observations.openings,
                ]
            }
            accounted = [
                item.id for item in [*proposal.scene.objects, *proposal.scene.openings]
            ]
            accounted.extend(item.entity_id for item in proposal.unresolved_entities)
            if set(accounted) != expected or len(accounted) != len(expected):
                raise ValueError(
                    "Proposal must account for every original observation."
                )


async def advance(state: AgentState, runtime: Runtime[RunContext]) -> dict[str, object]:
    context = runtime.context
    result: dict[str, object]
    await context.authorize()
    if context.cancelled():
        return {"status": "cancelled"}
    try:
        with anyio.fail_after(state.limits.step_timeout_seconds):
            step = await context.advance(state.model_copy(deep=True))
        step = RunStep.model_validate(step)
        check_step(state, step)
    except TimeoutError:
        result = {"status": "failed", "last_error": "step_timeout"}
    except (ValidationError, ValueError):
        result = {"status": "failed", "last_error": "invalid_step"}
    except Exception:
        # No raw adapter/provider details or photo-derived text in public errors.
        result = {"status": "failed", "last_error": "step_failed"}
    else:
        usage = RunUsage(
            **{
                key: value + getattr(step.usage, key)
                for key, value in state.usage.model_dump().items()
            }
        )
        result = {
            "continue_run": step.action == "continue",
            "usage": usage.model_dump(),
        }
        if step.analysis is not None:
            result["analysis"] = step.analysis.model_dump()
        if step.proposal is not None:
            result["proposal"] = step.proposal.model_dump()
            if step.proposal.scene is not None:
                scene = step.proposal.scene.model_dump()
                result["working_scene"] = scene
                if state.base_scene is None:
                    result["base_scene"] = step.proposal.scene.model_dump()
        if (
            usage.model_calls > state.limits.max_model_calls
            or usage.tool_calls > state.limits.max_tool_calls
        ):
            result["status"] = "budget_exhausted"
    # Revocation/expiry during a step must never expose its result or continue.
    await context.authorize()
    if context.cancelled():
        return {"status": "cancelled"}
    if context.now() >= state.expires_at:
        return {"status": "expired"}
    result["events"] = [
        {"step": state.steps_used, "status": result.get("status", "running")}
    ]
    return result


def finalize(state: AgentState) -> dict[str, object]:
    if state.status not in {"queued", "running"}:
        status = state.status
    else:
        status = {"ready": "completed", "partial": "partial"}.get(
            state.proposal.status if state.proposal else "", "incomplete"
        )
    return {"status": status, "events": [{"step": state.steps_used, "status": status}]}
