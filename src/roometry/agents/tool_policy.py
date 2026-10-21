"""Eligible clarifications and reads for the current accepted scene revision."""

from typing import Literal

from roometry.agents.clarification import question_for
from roometry.agents.state import AgentState, ClarificationQuestion


def clarification_candidates(state: AgentState, *, reserve_decision: bool = True) -> list[ClarificationQuestion]:
    if state.analysis is None:
        return []
    # Advertising a question must leave work budget after the decision itself.
    effective = state.model_copy(update={
        "usage": state.usage.model_copy(update={"model_calls": state.usage.model_calls + int(reserve_decision)}),
    })
    targets: list[tuple[Literal["room_dimensions", "object_mobility"], str | None]] = [("room_dimensions", None)]
    if state.working_scene:
        for obj in state.working_scene.objects:
            targets.append(("object_mobility", obj.id))
    questions = []
    for kind, object_id in targets:
        try:
            questions.append(question_for(effective, kind, object_id))
        except ValueError:
            pass
    return questions


def already_inspected(state: AgentState, tool: str) -> bool:
    for feedback in reversed(state.tool_feedback):
        if feedback.outcome == "success":
            if feedback.name == "propose_scene" or (
                feedback.name in {"move_object", "rotate_object"}
                and feedback.details.get("changed_object_ids")
            ):
                return False
        if feedback.name == tool and feedback.outcome == "success":
            return True
    return False
