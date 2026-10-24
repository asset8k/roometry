"""Bounded decision/tool loop with deterministic lifecycle and checkpointing."""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from roometry.agents.nodes import (
    RunContext,
    advance,
    clarify,
    execute_tool,
    finalize,
    guard,
    review_scene,
    assess_room,
)
from roometry.agents.state import AgentState


def after_guard(state: AgentState) -> str:
    return "advance" if state.status == "running" and state.continue_run else "finalize"


def after_advance(state: AgentState) -> str:
    if state.status == "awaiting_answer" and state.pending_question is not None:
        return "clarify"
    return (
        "tools"
        if state.status == "running" and state.pending_tool is not None
        else "guard"
    )


def after_tools(state: AgentState) -> str:
    if (state.status == "running" and state.proposal is not None and state.proposal.scene is not None
        and state.proposal.visual_review is None and state.working_scene == state.base_scene
        and state.tool_feedback and state.tool_feedback[-1].name == "propose_scene"):
        return "review_scene"
    return (
        "clarify"
        if state.status == "awaiting_answer" and state.pending_question
        else "guard"
    )


def build_graph(
    checkpointer: InMemorySaver, *, interrupt_after: list[str] | None = None
) -> CompiledStateGraph:
    """Dynamic clarification interrupts coexist with test-only static breakpoints."""
    graph = StateGraph(AgentState, context_schema=RunContext)
    graph.add_node("guard", guard)
    graph.add_node("advance", advance)
    graph.add_node("tools", execute_tool)
    graph.add_node("clarify", clarify)
    graph.add_node("review_scene", review_scene)
    graph.add_node("finalize", finalize)
    graph.add_node("assess_room", assess_room)
    graph.add_edge(START, "guard")
    graph.add_conditional_edges(
        "guard", after_guard, {"advance": "advance", "finalize": "assess_room"}
    )
    graph.add_conditional_edges(
        "advance", after_advance, {"tools": "tools", "guard": "guard", "clarify": "clarify"}
    )
    graph.add_conditional_edges(
        "tools", after_tools, {"clarify": "clarify", "guard": "guard", "review_scene": "review_scene"}
    )
    graph.add_edge("review_scene", "guard")
    graph.add_edge("clarify", "guard")
    graph.add_edge("finalize", END)
    graph.add_edge("assess_room", "finalize")
    return graph.compile(checkpointer=checkpointer, interrupt_after=interrupt_after)
