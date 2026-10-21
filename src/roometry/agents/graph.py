"""Small looping graph; real model/tool adapters are deliberately not wired yet."""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from roometry.agents.nodes import RunContext, advance, finalize, guard
from roometry.agents.state import AgentState


def after_guard(state: AgentState) -> str:
    return "advance" if state.status == "running" and state.continue_run else "finalize"


def build_graph(
    checkpointer: InMemorySaver, *, interrupt_after: list[str] | None = None
) -> CompiledStateGraph:
    """Static breakpoints support foundation tests, not the later HITL quiz."""
    graph = StateGraph(AgentState, context_schema=RunContext)
    graph.add_node("guard", guard)
    graph.add_node("advance", advance)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "guard")
    graph.add_conditional_edges(
        "guard", after_guard, {"advance": "advance", "finalize": "finalize"}
    )
    graph.add_edge("advance", "guard")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer, interrupt_after=interrupt_after)
