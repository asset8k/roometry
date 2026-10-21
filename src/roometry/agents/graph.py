"""Bounded decision/tool loop with deterministic lifecycle and checkpointing."""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from roometry.agents.nodes import RunContext, advance, execute_tool, finalize, guard
from roometry.agents.state import AgentState


def after_guard(state: AgentState) -> str:
    return "advance" if state.status == "running" and state.continue_run else "finalize"


def after_advance(state: AgentState) -> str:
    return (
        "tools"
        if state.status == "running" and state.pending_tool is not None else "guard"
    )


def build_graph(
    checkpointer: InMemorySaver, *, interrupt_after: list[str] | None = None
) -> CompiledStateGraph:
    """Static breakpoints support foundation tests, not the later HITL quiz."""
    graph = StateGraph(AgentState, context_schema=RunContext)
    graph.add_node("guard", guard)
    graph.add_node("advance", advance)
    graph.add_node("tools", execute_tool)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "guard")
    graph.add_conditional_edges(
        "guard", after_guard, {"advance": "advance", "finalize": "finalize"}
    )
    graph.add_conditional_edges(
        "advance", after_advance, {"tools": "tools", "guard": "guard"}
    )
    graph.add_edge("tools", "guard")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer, interrupt_after=interrupt_after)
