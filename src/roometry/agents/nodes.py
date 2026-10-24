"""Deterministic lifecycle nodes around an injected, asynchronous step adapter."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import anyio
from langgraph.runtime import Runtime
from langgraph.types import interrupt
from pydantic import ValidationError

from roometry.agents.budget import RunBudget
from roometry.assessment.schemas import RoomAssessment
from roometry.agents.clarification import answer_updates, question_for
from roometry.agents.state import (
    AgentState,
    CatalogRead,
    ClarificationAnswer,
    RunStep,
    RunUsage,
)
from roometry.model_calls import observe_model_calls
from roometry.scenes.clearance_acceptance import clearance_regressions
from roometry.scenes.validation import validate_geometry
from roometry.vision.evidence import apply_constraints
from roometry.vision.fidelity import evaluate_fidelity


class StepAdapter(Protocol):
    async def __call__(self, state: AgentState, context: "RunContext") -> RunStep: ...


class AssessmentAdapter(Protocol):
    async def __call__(self, state: AgentState, context: "RunContext") -> RoomAssessment | None: ...


@dataclass(frozen=True)
class RunContext:
    """Injected per invocation; never serialized in graph checkpoints."""

    advance: StepAdapter
    authorize: Callable[[], Awaitable[None]]
    cancelled: Callable[[], bool]
    now: Callable[[], datetime]
    owner: str = ""
    budget: RunBudget | None = None
    execute_tool: StepAdapter | None = None
    review_scene: StepAdapter | None = None
    assess_room: AssessmentAdapter | None = None


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
        return {"status": "budget_exhausted", "stop_reason": "budget_limit"}
    return {"status": "running", "steps_used": state.steps_used + 1}


def check_step(state: AgentState, step: RunStep, *, reconstruction_review: bool = False) -> None:
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
        if state.working_scene is not None and not reconstruction_review and proposal.scene != state.working_scene:
            raise ValueError("A proposal cannot reset an existing edited scene.")
        if state.working_scene is not None and proposal != state.proposal:
            if (not reconstruction_review or state.proposal is None or state.proposal.visual_review is not None
                or state.working_scene != state.base_scene or proposal.visual_review is None):
                raise ValueError("Only the pre-edit visual review may replace a constructed base scene.")
            from roometry.vision.review import candidate_allowed
            effective = apply_constraints(analysis, state.reconstruction_constraints, wall_axes=state.proposal.wall_axes)
            if proposal.scene is None or not candidate_allowed(effective, state.working_scene, proposal.scene):
                raise ValueError("Visual correction changed protected measurements, locks or valid inventory.")
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
    if step.pending_question is not None:
        question = step.pending_question
        effective = state.model_copy(
            update={"analysis": analysis, "proposal": proposal or state.proposal}
        )
        expected = question_for(
            effective,
            question.kind,
            None
            if question.kind in {"room_dimensions", "room_wall_axis"}
            else question.entity_ids[0]
            if question.entity_ids
            else None,
        )
        if question != expected or step.action != "continue":
            raise ValueError(
                "Questions must identify an available, specific constraint."
            )
    if step.working_scene is not None:
        original, candidate = state.working_scene, step.working_scene
        candidate_validation = validate_geometry(candidate)
        if original is None or not candidate_validation.valid:
            raise ValueError("Edits require an existing valid scene.")
        if clearance_regressions(validate_geometry(original), candidate_validation):
            raise ValueError("Agent pose edits cannot worsen clearance.")
        if original.model_dump(exclude={"objects"}) != candidate.model_dump(
            exclude={"objects"}
        ):
            raise ValueError("Scene edits cannot change room geometry or provenance.")
        if state.analysis is not None:
            effective = apply_constraints(
                state.analysis,
                state.reconstruction_constraints,
                wall_axes=state.proposal.wall_axes if state.proposal else None,
            )
            before = evaluate_fidelity(effective, original)
            after = evaluate_fidelity(effective, candidate)
            old_failures = {
                (c.kind, tuple(c.entity_ids), c.message)
                for c in before.checks
                if c.required and not c.passed
            }
            if any(
                c.required
                and not c.passed
                and (c.kind, tuple(c.entity_ids), c.message) not in old_failures
                for c in after.checks
            ):
                raise ValueError(
                    "Agent pose edits must preserve evidence-backed relationships."
                )
        originals = {obj.id: obj for obj in original.objects}
        if [obj.id for obj in original.objects] != [
            obj.id for obj in candidate.objects
        ]:
            raise ValueError("Edits cannot add, remove or reorder observed objects.")
        for obj in candidate.objects:
            old = originals[obj.id]
            if old.model_dump(exclude={"position", "rotation_y_rad"}) != obj.model_dump(
                exclude={"position", "rotation_y_rad"}
            ):
                raise ValueError("Edits may change poses only.")
            if (old.fixed or old.locked) and old != obj:
                raise ValueError("Protected objects cannot be changed.")


async def _perform(
    state: AgentState, context: RunContext, adapter: StepAdapter, *, reconstruction_review: bool = False
) -> dict[str, object]:
    result: dict[str, object]
    await context.authorize()
    if context.cancelled():
        return {"status": "cancelled"}
    if context.budget is not None:
        context.budget.sync(state.usage)
    before = context.budget.usage if context.budget is not None else RunUsage()
    try:
        with anyio.fail_after(state.limits.step_timeout_seconds):
            with observe_model_calls(context.budget):
                step = await adapter(state.model_copy(deep=True), context)
        step = RunStep.model_validate(step)
        check_step(state, step, reconstruction_review=reconstruction_review)
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
            "pending_tool": (
                step.pending_tool.model_dump() if step.pending_tool else None
            ),
        }
        if step.feedback is not None:
            result["tool_feedback"] = [
                item.model_dump()
                for item in [*state.tool_feedback, step.feedback][-16:]
            ]
        if (
            step.feedback is not None
            and step.feedback.details.get("mcp_attempted") is True
            and state.pending_tool is not None
            and step.feedback.name == state.pending_tool.name
            and step.feedback.name
            in {
                "search_furniture",
                "get_furniture_details",
                "list_furniture_categories",
            }
        ):
            read = CatalogRead.model_validate(
                {
                    "name": step.feedback.name,
                    "arguments": state.pending_tool.arguments,
                    "outcome": step.feedback.outcome,
                }
            )
            result["catalog_reads"] = [
                r.model_dump() for r in [*state.catalog_reads, read][-16:]
            ]
        if step.failure is not None:
            result.update(status="failed", last_error=step.failure)
        if step.analysis is not None:
            result["analysis"] = step.analysis.model_dump()
        if step.proposal is not None:
            result["proposal"] = step.proposal.model_dump()
            if step.proposal.scene is not None:
                scene = step.proposal.scene.model_dump()
                result["working_scene"] = scene
                if state.base_scene is None or reconstruction_review:
                    result["base_scene"] = step.proposal.scene.model_dump()
        if step.working_scene is not None:
            result["working_scene"] = step.working_scene.model_dump()
        if step.pending_question is not None:
            result.update(
                status="awaiting_answer",
                pending_question=step.pending_question.model_dump(),
                questions=[
                    q.model_dump() for q in [*state.questions, step.pending_question]
                ],
            )
        if (
            usage.model_calls > state.limits.max_model_calls
            or usage.tool_calls > state.limits.max_tool_calls
        ):
            result["status"] = "budget_exhausted"
    if context.budget is not None:
        observed = context.budget.delta(before)
        usage = RunUsage.model_validate(result.get("usage", state.usage))
        usage = RunUsage(
            **{
                key: value + getattr(observed, key)
                for key, value in usage.model_dump().items()
            }
        )
        context.budget.sync(usage)
        result["usage"] = usage.model_dump()
        if (
            context.budget.exhausted
            or usage.model_calls > state.limits.max_model_calls
            or usage.tool_calls > state.limits.max_tool_calls
        ):
            result["status"] = "budget_exhausted"
    if result.get("status") == "budget_exhausted":
        result["stop_reason"] = "budget_limit"
    # Revocation/expiry during a step must never expose its result or continue.
    await context.authorize()
    if context.cancelled():
        return {
            "status": "cancelled",
            "usage": result.get("usage", state.usage.model_dump()),
            "pending_tool": None,
        }
    if context.now() >= state.expires_at:
        return {"status": "expired"}
    result["events"] = [
        {"step": state.steps_used, "status": result.get("status", "running")}
    ]
    return result


async def advance(state: AgentState, runtime: Runtime[RunContext]) -> dict[str, object]:
    return await _perform(state, runtime.context, runtime.context.advance)


async def execute_tool(
    state: AgentState, runtime: Runtime[RunContext]
) -> dict[str, object]:
    adapter = runtime.context.execute_tool
    if adapter is None or state.pending_tool is None:
        return {"status": "failed", "last_error": "invalid_step", "pending_tool": None}
    return await _perform(state, runtime.context, adapter)


async def review_scene(state: AgentState, runtime: Runtime[RunContext]) -> dict[str, object]:
    """Paid review is checkpointed separately, never replayed by clarification."""
    adapter = runtime.context.review_scene
    if adapter is None:
        return {}  # Explicitly disabled or an injected deterministic test graph.
    return await _perform(state, runtime.context, adapter, reconstruction_review=True)


async def assess_room(state: AgentState, runtime: Runtime[RunContext]) -> dict[str, object]:
    """Optional read-only assessment after the final reconstruction, before END.

    Paid work is outside interruption replay and uses the same run accounting.
    Assessment failure never downgrades or hides an otherwise usable scene.
    """
    context = runtime.context
    await context.authorize()
    if context.cancelled():
        return {"status": "cancelled"}
    if context.now() >= state.expires_at:
        return {"status": "expired"}
    if (context.assess_room is None or state.analysis is None or state.working_scene is None
        or state.status not in {"running", "budget_exhausted"} or state.room_assessment is not None):
        return {}
    if context.budget is not None:
        context.budget.sync(state.usage)
    before = context.budget.usage if context.budget is not None else RunUsage()
    try:
        with observe_model_calls(context.budget), anyio.fail_after(state.limits.step_timeout_seconds):
            assessment = await context.assess_room(state.model_copy(deep=True), context)
        if assessment is not None:
            from roometry.scenes.hashing import scene_hash
            assessment = RoomAssessment.model_validate(assessment)
            if assessment.scene_hash != scene_hash(state.working_scene):
                raise ValueError("Assessment belongs to another scene snapshot.")
    except Exception:
        # Keep deterministic cards on provider/node failure, without retrying.
        from roometry.assessment.metrics import measure_room
        assessment = await anyio.to_thread.run_sync(lambda: measure_room(
            state.working_scene, incomplete_inventory=bool(state.proposal and state.proposal.unresolved_entities)))
    usage = state.usage
    if context.budget is not None:
        delta = context.budget.delta(before)
        usage = RunUsage(**{key: value + getattr(delta, key) for key, value in usage.model_dump().items()})
        context.budget.sync(usage)
    await context.authorize()
    if context.cancelled():
        return {"status": "cancelled", "usage": usage.model_dump()}
    if context.now() >= state.expires_at:
        return {"status": "expired", "usage": usage.model_dump()}
    return {"room_assessment": assessment.model_dump() if assessment else None, "usage": usage.model_dump()}


async def clarify(state: AgentState, runtime: Runtime[RunContext]) -> dict[str, object]:
    """Only replay-safe authorization and a pure state update surround interrupt.

    The decision, photo analysis, proposal and question preparation have already
    completed in other checkpointed nodes. Nothing here can call a model.
    """
    context = runtime.context
    await context.authorize()
    if context.cancelled():
        return {"status": "cancelled", "pending_question": None}
    if context.now() >= state.expires_at:
        return {"status": "expired", "pending_question": None}
    if state.pending_question is None:
        return {"status": "failed", "last_error": "invalid_step"}
    answer = interrupt(
        state.pending_question.model_dump(mode="json"),
        response_schema=ClarificationAnswer,
    )
    await context.authorize()
    if context.cancelled():
        return {"status": "cancelled", "pending_question": None}
    return answer_updates(state, ClarificationAnswer.model_validate(answer))


def finalize(state: AgentState) -> dict[str, object]:
    status = state.status
    reason = state.stop_reason
    if status in {"queued", "running", "budget_exhausted"}:
        proposal, scene = state.proposal, state.working_scene
        usable = (
            proposal is not None
            and proposal.status in {"ready", "partial"}
            and scene is not None
            and state.base_scene is not None
            and state.last_error is None
            and validate_geometry(scene).valid
        )
        if usable and proposal is not None:
            # Finishing consumes no provider/tool call. The reason preserves the
            # distinction between normal completion and stopped optional work.
            status = "partial" if proposal.status == "partial" else "completed"
        elif status != "budget_exhausted":
            status = "incomplete"
        reason = reason or (
            "budget_limit" if state.status == "budget_exhausted" else "finished"
        )
    return {
        "status": status,
        "stop_reason": reason,
        "pending_tool": None,
        "pending_question": None,
        "events": [{"step": state.steps_used, "status": status}],
    }
