"""Pure question construction and answer effects, with no provider calls."""

from typing import Literal

from roometry.agents.state import (
    AgentState,
    ClarificationAnswer,
    ClarificationQuestion,
    ToolFeedback,
)
from roometry.uploads.schemas import InputDimensions
from roometry.vision.proposal_schemas import AXES
from roometry.vision.proposals import resolve_measurements


class InvalidAnswer(ValueError):
    pass


class AnswerConflict(ValueError):
    pass


def question_for(
    state: AgentState,
    kind: Literal["room_dimensions", "object_mobility"],
    object_id: str | None = None,
) -> ClarificationQuestion:
    if state.analysis is None or len(state.questions) >= 3:
        raise ValueError("Clarification is unavailable or its limit is reached.")
    if (
        state.steps_used >= state.limits.max_steps
        or state.usage.model_calls >= state.limits.max_model_calls
        or state.usage.tool_calls + 1 >= state.limits.max_tool_calls
    ):
        raise ValueError("There is no remaining work budget to use this answer.")
    common = {"id": f"q{len(state.questions) + 1}", "kind": kind}
    if kind == "room_dimensions":
        if object_id is not None or state.working_scene is not None:
            raise ValueError("Measurements are requested before scene construction.")
        confirmed = {
            axis: value
            for axis in AXES
            if (value := getattr(state.confirmed_dimensions, axis, None)) is not None
        }
        missing = resolve_measurements(state.analysis, confirmed, True).to_confirm
        if not missing:
            raise ValueError("There are no missing room dimensions.")
        if any(q.kind == kind and q.dimension_axes == missing for q in state.questions):
            raise ValueError("These missing dimensions have already been asked about.")
        names = ", ".join(axis.removesuffix("_m") for axis in missing)
        return ClarificationQuestion.model_validate(
            {
                **common,
            "question": f"What {'is' if len(missing) == 1 else 'are'} the room's {names} in meters? Enter the values you know; photos could not establish these dimensions.",
                "dimension_axes": missing,
                "choices": [
                    {"id": "enter_measurements", "label": "Enter the measurements"},
                    {
                        "id": "leave_unknown",
                        "label": "I don't know — finish without a complete scene",
                    },
                    {"id": "cancel_run", "label": "Cancel this run"},
                ],
            }
        )
    scene = state.working_scene
    if any(q.kind == kind and q.entity_ids == [object_id] for q in state.questions):
        raise ValueError("This object's mobility has already been asked about.")
    obj = (
        next((obj for obj in scene.objects if obj.id == object_id), None)
        if scene
        else None
    )
    if (
        obj is None
        or obj.fixed
        or obj.locked
        or obj.placement not in {"floor", "surface"}
    ):
        raise ValueError(
            "Choose an existing movable object; protected fixtures cannot be unlocked."
        )
    label = next(
        (
            item.label
            for item in state.analysis.observations.objects
            if item.id == obj.id
        ),
        obj.type.replace("_", " "),
    )
    return ClarificationQuestion.model_validate(
        {
            **common,
            "question": f"Can the {label} be moved?",
            "entity_ids": [obj.id],
            "choices": [
                {"id": "allow_movement", "label": "Yes, it can move"},
                {"id": "keep_fixed", "label": "No, keep it in place"},
                {"id": "not_sure", "label": "I'm not sure — keep it in place for now"},
            ],
        }
    )


def validate_answer(state: AgentState, answer: ClarificationAnswer) -> None:
    question = state.pending_question
    if question is None or question.id != answer.question_id:
        raise InvalidAnswer("Answer the currently pending question.")
    if answer.choice_id not in {choice.id for choice in question.choices}:
        raise InvalidAnswer("Select one of this question's three choices.")
    if question.kind == "room_dimensions" and answer.choice_id == "enter_measurements":
        if answer.dimensions is None or not any(
            getattr(answer.dimensions, axis) is not None
            for axis in question.dimension_axes
        ):
            raise InvalidAnswer(
                "Supply at least one requested measurement in meters, or choose to leave them unknown."
            )
        for axis in AXES:
            old = getattr(state.confirmed_dimensions, axis, None)
            new = getattr(answer.dimensions, axis)
            if old is not None and new is not None and old != new:
                raise InvalidAnswer(
                    "Previously supplied measurements cannot be overwritten by this answer."
                )
    elif answer.dimensions is not None:
        raise InvalidAnswer("Measurements belong only to the measurement-entry choice.")


def answer_updates(state: AgentState, answer: ClarificationAnswer) -> dict[str, object]:
    validate_answer(state, answer)
    question = state.pending_question
    assert question is not None
    result: dict[str, object] = {
        "status": "running",
        "continue_run": True,
        "pending_question": None,
        "clarification_answers": {
            **state.clarification_answers,
            question.id: answer.choice_id,
        },
        "submitted_answers": {
            **{key: item.model_dump() for key, item in state.submitted_answers.items()},
            question.id: answer.model_dump(),
        },
    }
    if question.kind == "room_dimensions":
        if answer.choice_id == "enter_measurements":
            assert answer.dimensions is not None
            values = (
                state.confirmed_dimensions.model_dump(exclude_none=True)
                if state.confirmed_dimensions
                else {}
            )
            values.update(answer.dimensions.model_dump(exclude_none=True))
            values["measurements_source"] = "user_confirmed"
            result["confirmed_dimensions"] = InputDimensions.model_validate(
                values
            ).model_dump()
            message = "User-supplied measurements are now authoritative; reuse cached photo observations."
        else:
            result.update(
                status="cancelled"
                if answer.choice_id == "cancel_run"
                else "incomplete",
                continue_run=False,
            )
            message = (
                "Measurements remain unknown; no complete reconstruction is claimed."
            )
    else:
        object_id = question.entity_ids[0]
        allowed = answer.choice_id == "allow_movement"
        # Uncertainty grants no permission to move an object.
        result["object_mobility"] = {**state.object_mobility, object_id: allowed}
        assert state.working_scene is not None
        scene = state.working_scene.model_copy(deep=True)
        if not allowed:
            for obj in scene.objects:
                if obj.id == object_id:
                    obj.locked = True
        result["working_scene"] = scene.model_dump()
        message = "The user's object-mobility constraint is recorded and enforced by scene edits."
    feedback = ToolFeedback(
        name="request_clarification", outcome="success", message=message
    )
    result["tool_feedback"] = [
        item.model_dump() for item in [*state.tool_feedback, feedback][-16:]
    ]
    result["events"] = [{"step": state.steps_used, "status": result["status"]}]
    return result
