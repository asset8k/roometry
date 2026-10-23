"""Eligible clarifications and reads for the current accepted scene revision."""

from roometry.agents.clarification import question_for
from roometry.agents.state import AgentState, ClarificationKind, ClarificationQuestion


def clarification_candidates(
    state: AgentState, *, reserve_decision: bool = True
) -> list[ClarificationQuestion]:
    if state.analysis is None:
        return []
    # Advertising a question must leave work budget after the decision itself.
    effective = state.model_copy(
        update={
            "usage": state.usage.model_copy(
                update={"model_calls": state.usage.model_calls + int(reserve_decision)}
            ),
        }
    )
    targets: list[tuple[ClarificationKind, str | None]] = [("room_dimensions", None), ("room_wall_axis", None)]
    if state.working_scene is None:
        from roometry.vision.importance import importance

        for obj in sorted(
            state.analysis.observations.objects, key=lambda o: (importance(o), o.id)
        ):
            targets.extend(
                [("object_identity", obj.id), ("layout_orientation", obj.id)]
            )
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


def clarification_target(question: ClarificationQuestion) -> str | None:
    return None if question.kind in {"room_dimensions", "room_wall_axis"} else question.entity_ids[0] if question.entity_ids else None


CATALOG_READ_LIMITS = {"search_furniture": 4, "get_furniture_details": 8, "list_furniture_categories": 1}


def catalog_available(state: AgentState, name: str) -> bool:
    # An unavailable/malformed MCP endpoint is not retried inside a run. A
    # not-found rejection still permits a different valid item lookup.
    return not any(read.outcome == "error" for read in state.catalog_reads) and sum(
        read.name == name for read in state.catalog_reads
    ) < CATALOG_READ_LIMITS[name]


def catalog_already_read(state: AgentState, name: str, arguments: dict) -> bool:
    return any(read.name == name and read.arguments == arguments for read in state.catalog_reads)
