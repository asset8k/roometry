"""Pure question construction and answer effects, with no provider calls."""

import re

from roometry.agents.state import (
    AgentState,
    ClarificationAnswer,
    ClarificationKind,
    ClarificationQuestion,
    ToolFeedback,
)
from roometry.uploads.schemas import InputDimensions
from roometry.vision.proposal_schemas import AXES
from roometry.vision.proposals import resolve_measurements
from roometry.vision.schemas import WallAxisAnswer
from roometry.vision.wall_axes import resolve_wall_axes


class InvalidAnswer(ValueError):
    pass


class AnswerConflict(ValueError):
    pass


def display_label(label: str) -> str:
    """Format model identifiers without changing checkpointed entity IDs."""
    text = re.sub(r"[_\s]+", " ", label).strip()
    text = re.sub(r"\btv\b", "TV", text, flags=re.IGNORECASE)
    return text[:1].upper() + text[1:]


def question_for(
    state: AgentState,
    kind: ClarificationKind,
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
                "question": f"What {'is' if len(missing) == 1 else 'are'} the room's {names} in meters? Enter what you know, or continue with clearly labeled approximate sizes.",
                "dimension_axes": missing,
                "choices": [
                    {"id": "enter_measurements", "label": "Enter the measurements"},
                    {
                        "id": "leave_unknown",
                        "label": "Use estimated dimensions",
                    },
                    {"id": "cancel_run", "label": "Cancel this run"},
                ],
            }
        )
    if kind == "room_wall_axis":
        if object_id is not None or state.working_scene is not None or any(q.kind == kind for q in state.questions):
            raise ValueError("Room orientation is clarified once, before construction.")
        confirmed = {axis: value for axis in AXES if (value := getattr(state.confirmed_dimensions, axis, None)) is not None}
        measurements = resolve_measurements(state.analysis, confirmed, True)
        if measurements.to_confirm:
            raise ValueError("Resolve missing measurements before their wall assignment.")
        resolution = resolve_wall_axes(state.analysis, measurements.dimensions, state.reconstruction_constraints)
        if resolution.status != "needs_clarification" or resolution.reference_axis is None:
            raise ValueError("Room axes do not need clarification.")
        assert resolution.reference_span_m is not None
        walls = {wall.id: wall for wall in state.analysis.observations.walls}
        first = resolution.candidate_wall_ids[0]
        if len(resolution.candidate_wall_ids) == 2:
            choices = [
                {"id": "wall_first", "label": display_label(walls[first].label)[:190]},
                {"id": "wall_second", "label": display_label(walls[resolution.candidate_wall_ids[1]].label)[:190]},
            ]
            text = f"Which wall measures {resolution.reference_span_m:g} m?"
        else:
            other_axis = "length_m" if resolution.reference_axis == "width_m" else "width_m"
            choices = [
                {"id": "wall_first", "label": f"{resolution.reference_span_m:g} m"},
                {"id": "wall_second", "label": f"{getattr(measurements.dimensions, other_axis):g} m"},
            ]
            text = f"Which length belongs to {display_label(walls[first].label)}?"[:500]
        choices.append({"id": "not_sure", "label": "Not sure — use an approximate orientation"})
        return ClarificationQuestion.model_validate({**common, "question": text, "choices": choices, "entity_ids": resolution.candidate_wall_ids, "dimension_axes": [resolution.reference_axis]})
    if kind in {"object_identity", "layout_orientation"}:
        if state.working_scene is not None:
            raise ValueError("Resolve reconstruction hypotheses before construction.")
        if any(q.kind == kind and q.entity_ids == [object_id] for q in state.questions):
            raise ValueError("This reconstruction uncertainty has already been asked.")
        source = next(
            (o for o in state.analysis.observations.objects if o.id == object_id), None
        )
        if source is None:
            raise ValueError("Choose an observed object.")
        if kind == "object_identity":
            if (
                source.alternative_type is None
                or source.type == source.alternative_type
            ):
                raise ValueError(
                    "This observation has no distinct identity hypothesis."
                )
            primary = (
                source.type.replace("_", " ") if source.type else "Unclassified item"
            )
            choices = [
                {"id": "use_primary", "label": primary.title()},
                {
                    "id": "use_alternative",
                    "label": source.alternative_type.replace("_", " ").title(),
                },
                {
                    "id": "leave_unknown",
                    "label": "Neither / not sure — leave this item unresolved",
                },
            ]
            text = f"How should we represent {display_label(source.label)}?"
        else:
            relations = [
                r
                for r in state.analysis.observations.relationships
                if r.kind == "faces" and r.subject_id == source.id
            ]
            if len(relations) != 1 or relations[0].alternative_target_id is None:
                raise ValueError("This object has no two-target facing uncertainty.")
            relation = relations[0]
            entities = {
                o.id: o
                for o in [
                    *state.analysis.observations.objects,
                    *state.analysis.observations.openings,
                    *state.analysis.observations.walls,
                ]
            }

            def label(key):
                entity = entities[key]
                return display_label(getattr(entity, "label", getattr(entity, "type", key)))[:150]

            choices = [
                {"id": "face_primary", "label": "Toward " + label(relation.target_id)},
                {
                    "id": "face_alternative",
                    "label": "Toward " + label(relation.alternative_target_id),
                },
                {
                    "id": "leave_unknown",
                    "label": "Not sure — keep the direction approximate",
                },
            ]
            text = f"Which direction does {display_label(source.label)} face?"
        return ClarificationQuestion.model_validate(
            {
                **common,
                "question": text[:500],
                "entity_ids": [source.id],
                "choices": choices,
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
            "question": f"Can the {display_label(label)} be moved?",
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
        elif answer.choice_id == "leave_unknown":
            message = (
                "Continue with photo estimates and supported wall-span scale estimates; for missing sizes use the disclosed "
                "4 × 4 × 2.6 m starting size only on unknown axes. Keep supplied "
                "measurements authoritative, all other axes estimated, and reuse cached photo observations."
            )
        else:
            result.update(status="cancelled", continue_run=False)
            message = "Reconstruction cancelled; measurements were not invented."
    elif question.kind == "room_wall_axis":
        constraints = state.reconstruction_constraints.model_copy(deep=True)
        axis = question.dimension_axes[0]
        assert axis in {"width_m", "length_m"}
        selected = None
        if answer.choice_id != "not_sure":
            if answer.choice_id == "wall_first":
                selected = question.entity_ids[0]
            elif len(question.entity_ids) == 2:
                selected = question.entity_ids[1]
            else:
                selected = question.entity_ids[0]
                axis = "length_m" if axis == "width_m" else "width_m"
        constraints.wall_axis = WallAxisAnswer(axis=axis, wall_id=selected)
        result["reconstruction_constraints"] = constraints.model_dump()
        message = "The user's wall-to-dimension answer is recorded without changing measurements or repeating photo analysis."
    elif question.kind in {"object_identity", "layout_orientation"}:
        object_id = question.entity_ids[0]
        assert state.analysis is not None
        constraints = state.reconstruction_constraints.model_copy(deep=True)
        if question.kind == "object_identity":
            obj = next(
                o for o in state.analysis.observations.objects if o.id == object_id
            )
            selected = (
                obj.type
                if answer.choice_id == "use_primary"
                else obj.alternative_type
                if answer.choice_id == "use_alternative"
                else None
            )
            constraints.object_types[object_id] = selected
        else:
            relation = next(
                r
                for r in state.analysis.observations.relationships
                if r.kind == "faces" and r.subject_id == object_id
            )
            selected = (
                relation.target_id
                if answer.choice_id == "face_primary"
                else relation.alternative_target_id
                if answer.choice_id == "face_alternative"
                else None
            )
            constraints.facing_targets[object_id] = selected
        result["reconstruction_constraints"] = constraints.model_dump()
        message = "The user's reconstruction answer is recorded separately; reuse the original cached analysis and photos."
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
