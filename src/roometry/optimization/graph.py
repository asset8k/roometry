"""Checkpointed propose → validate/score → review → retain loop, bounded by policy."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypedDict

import anyio
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from pydantic import ValidationError

from roometry.agents.budget import RunBudget
from roometry.agents.state import AgentState
from roometry.assessment.evidence import evidence_for_run
from roometry.assessment.metrics import measure_room, with_recommendation
from roometry.model_calls import observe_model_calls
from roometry.optimization.context import refinement_message
from roometry.optimization.policy import evaluate, meaningful_gain
from roometry.optimization.provider import OptimizationProvider
from roometry.optimization.schemas import (
    CandidateProposal,
    CandidateReview,
    OptimizationAttempt,
    OptimizationRun,
)
from roometry.scenes.mutations import EditRejected


class GraphState(TypedDict, total=False):
    run: dict
    proposal: dict | None
    candidate: dict | None


@dataclass
class OptimizationContext:
    source: AgentState
    authorize: Callable[[], Awaitable[None]]
    cancelled: Callable[[], bool]
    budget: RunBudget
    provider: OptimizationProvider
    catalog: dict | None


def saved(run, context, **updates):
    if run.instruction and "message" in updates:
        updates["message"] = refinement_message(updates["message"])
    data = run.model_dump()
    data.update(updates, usage=context.budget.usage.model_dump())
    return {"run": OptimizationRun.model_validate(data).model_dump()}


async def stopped(run, context):
    await context.authorize()
    if context.cancelled():
        return saved(run, context, status="cancelled", stage="finished", message="Improvement cancelled; the original and any validated candidate are retained.")
    return None


async def initialize(state, runtime: Runtime[OptimizationContext]):
    run, context = OptimizationRun.model_validate(state['run']), runtime.context
    stop = await stopped(run, context)
    if stop: return stop
    baseline = await anyio.to_thread.run_sync(lambda: measure_room(run.source_scene, evidence=evidence_for_run(context.source)))
    if run.baseline_assessment:
        baseline = with_recommendation(baseline, aesthetics=run.baseline_assessment.aesthetics.model_dump())
    limitations = ["Added and replacement furniture represents catalog design ideas, not verified products or purchases.",
                   "Clearance and scores are approximate model checks, not a safety certification.",
                   "Style and free-text preferences are reviewed qualitatively; verify suggested changes in your room."]
    if run.source_status == "partial": limitations.append("The source reconstruction is partial; unresolved observations remain unresolved. Only affected rating categories have limited confidence.")
    return saved(run, context, baseline_assessment=baseline.model_dump(), limitations=limitations, status="running", stage="proposing")


async def propose(state, runtime: Runtime[OptimizationContext]):
    run, context = OptimizationRun.model_validate(state['run']), runtime.context
    stop = await stopped(run, context)
    if stop: return stop
    if context.budget.usage.model_calls >= run.limits.max_model_calls:
        return saved(run, context, status="budget_exhausted", stage="finished", message="The improvement call limit was reached. The original and best validated candidate are retained.")
    with observe_model_calls(context.budget), anyio.fail_after(run.limits.step_timeout_seconds):
        proposal = await context.provider.propose(run, context.source, context.catalog)
    await context.authorize()
    if context.cancelled(): return await stopped(run, context)
    proposal = CandidateProposal.model_validate(proposal)
    return {**saved(run, context, stage="validating", suggestions=proposal.suggestions), "proposal": proposal.model_dump(), "candidate": None}


def attempt(run, proposal, *, outcome, issues, before=None, after=None, changed=(), review=None):
    entry = OptimizationAttempt(number=len(run.attempts)+1, outcome=outcome, before_score=before, after_score=after,
        changed_object_ids=list(changed), issues=[i[:500] for i in issues[:16]], summary=proposal.summary, review=review, proposal=proposal)
    return [*[a.model_dump() for a in run.attempts], entry.model_dump()]


async def validate(state, runtime: Runtime[OptimizationContext]):
    run, context = OptimizationRun.model_validate(state['run']), runtime.context
    stop = await stopped(run, context)
    if stop: return stop
    if run.status != "running": return saved(run, context)
    proposal = CandidateProposal.model_validate(state['proposal'])
    if not proposal.has_operations:
        return saved(run, context, status="completed" if run.result_scene else "no_improvement", stage="finished",
            message="The best validated layout is retained." if run.result_scene else "No useful layout change was proposed. Your original room is retained; review the suggestions.")
    await context.budget.reserve_tool()  # Atomic assembly and geometry/clearance.
    await context.budget.reserve_tool()  # Fixed-policy score comparison.
    try:
        evaluation = await anyio.to_thread.run_sync(lambda: evaluate(run, proposal, context.source))
    except (ValueError, ValidationError, EditRejected) as error:
        message = (f"{error.code} [{', '.join(error.entity_ids)}]: {error}" if isinstance(error, EditRejected)
                   else str(error) if type(error) is ValueError else "The candidate could not be assembled using the allowed edits.")
        return {**saved(run, context, stage="proposing", attempts=attempt(run, proposal, outcome="rejected", issues=[message])), "candidate": None}
    await context.authorize()
    if context.cancelled(): return await stopped(run, context)
    gain = await anyio.to_thread.run_sync(lambda: meaningful_gain(run, evaluation, context.source))
    if evaluation.issues or not evaluation.changed or not gain:
        return {**saved(run, context, stage="proposing", attempts=attempt(run, proposal,
            outcome="rejected" if evaluation.issues else "no_gain",
            issues=evaluation.issues or ["Propose a meaningful gain: clearer walking space, usable added storage, or a usable workstation. A tiny score change or cosmetic change alone does not improve a functional goal."],
            before=evaluation.before_score, after=evaluation.after_score)), "candidate": None}
    return {**saved(run, context, stage="reviewing"), "candidate": {
        "scene": evaluation.scene.model_dump(), "assessment": evaluation.assessment.model_dump(),
        "before": evaluation.before_score, "after": evaluation.after_score, "changed": evaluation.changed,
        "tradeoffs": evaluation.tradeoffs}}


async def review(state, runtime: Runtime[OptimizationContext]):
    from types import SimpleNamespace

    from roometry.assessment.schemas import RoomAssessment
    from roometry.scenes.schemas import RoomScene
    run, context = OptimizationRun.model_validate(state['run']), runtime.context
    stop = await stopped(run, context)
    if stop: return stop
    if context.budget.usage.model_calls >= run.limits.max_model_calls:
        return saved(run, context, status="budget_exhausted", stage="finished", message="No review allowance remains. The unreviewed candidate was not applied.")
    raw = state['candidate']
    evaluation = SimpleNamespace(scene=RoomScene.model_validate(raw['scene']), assessment=RoomAssessment.model_validate(raw['assessment']))
    with observe_model_calls(context.budget), anyio.fail_after(run.limits.step_timeout_seconds):
        judgment = CandidateReview.model_validate(await context.provider.review(run, evaluation))
    await context.authorize()
    if context.cancelled(): return await stopped(run, context)
    accepted = judgment.preferred == "candidate" and judgment.confidence != "low" and judgment.constraints_satisfied
    rubric = judgment.aesthetics if run.focus == "aesthetics" and not run.instruction else None
    if rubric and (rubric.confidence == "low" or rubric.candidate.total <= rubric.retained.total):
        accepted = False
        judgment = judgment.model_copy(update={"explanation": "The rendered design rubric does not establish a meaningful aesthetic gain. Try a more coherent palette, proportions or grouping."})
    proposal = CandidateProposal.model_validate(state['proposal'])
    updates = {"attempts": attempt(run, proposal, outcome="accepted" if accepted else "rejected",
        issues=[] if accepted else [judgment.explanation], before=raw['before'], after=raw['after'],
        changed=raw['changed'] if accepted else (), review=judgment), "stage": "proposing"}
    if accepted:
        updates.update(result_scene=raw['scene'], result_assessment=raw['assessment'])
        updates['limitations'] = list(dict.fromkeys([*run.limitations, *raw.get('tradeoffs', [])]))[:12]
    return {**saved(run, context, **updates), "candidate": None}


def route(state):
    run = OptimizationRun.model_validate(state['run'])
    if run.status != "running": return "finalize"
    if state.get('candidate'): return "review"
    if len(run.attempts) >= run.max_candidates: return "finalize"
    proposal = state.get('proposal')
    if proposal and proposal.get('finish') and run.attempts and run.attempts[-1].outcome == "accepted": return "finalize"
    return "propose"


async def finalize(state, runtime: Runtime[OptimizationContext]):
    run, context = OptimizationRun.model_validate(state['run']), runtime.context
    stop = await stopped(run, context)
    if stop: return stop
    if run.status != "running": return saved(run, context)
    return saved(run, context, status="completed" if run.result_scene else "no_improvement", stage="finished",
        message="A validated improvement candidate is ready for review." if run.result_scene else "No candidate passed the improvement checks within this run. Your original room is retained.")


def build_graph(saver):
    graph = StateGraph(GraphState, context_schema=OptimizationContext)
    for name, fn in (("initialize", initialize), ("propose", propose), ("validate", validate), ("review", review), ("finalize", finalize)):
        graph.add_node(name, fn)
    graph.add_edge(START, "initialize")
    graph.add_conditional_edges("initialize", route, {"propose": "propose", "finalize": "finalize", "review": "review"})
    graph.add_edge("propose", "validate")
    graph.add_conditional_edges("validate", route, {"propose": "propose", "review": "review", "finalize": "finalize"})
    graph.add_conditional_edges("review", route, {"propose": "propose", "review": "review", "finalize": "finalize"})
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=saver)
