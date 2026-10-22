"""Responses function-call decisions, separate from deterministic tool dispatch."""

import json
from typing import cast

from openai import APIError, APITimeoutError
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputRefusal,
)
from openai.types.responses.response_create_params import (
    ResponseCreateParamsNonStreaming,
)
from pydantic import ValidationError

from roometry.agents.nodes import RunContext
from roometry.agents.prompts import AGENT_INSTRUCTIONS
from roometry.agents.state import AgentState, RunStep, ToolCall, ToolFeedback
from roometry.agents.tool_policy import clarification_candidates, clarification_target
from roometry.agents.tools import ARGUMENT_MODELS, available_tools, tool_definitions
from roometry.config import Settings
from roometry.vision.analyzer import PhotoAnalyzer
from roometry.vision.errors import AnalysisFailure


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON argument key.")
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    raise ValueError("Non-finite JSON arguments.")


class RoomAgent:
    def __init__(self, settings: Settings, provider: PhotoAnalyzer):
        self.settings, self.provider = settings, provider

    async def __call__(self, state: AgentState, context: RunContext) -> RunStep:
        def failed(code: str) -> RunStep:
            return RunStep(
                action="finish",
                failure="decision_failed",
                feedback=ToolFeedback(
                    name=None,
                    outcome="error",
                    message="The decision model could not complete this step.",
                    details={"code": code},
                ),
            )

        if (
            not self.settings.analysis_enabled
            or self.settings.openai_api_key is None
            or not self.settings.openai_api_key.get_secret_value().strip()
        ):
            return failed("analysis_unavailable")
        # This pure clarification runs after the analysis checkpoint, before any
        # paid construction/decision, and never inside replayed interrupt code.
        axis_question = next((q for q in clarification_candidates(state, reserve_decision=False) if q.kind == "room_wall_axis"), None)
        if axis_question is not None:
            return RunStep(action="continue", pending_question=axis_question, feedback=ToolFeedback(name="request_clarification", outcome="success", message="Identify the measured side before constructing the room."))
        definitions = tool_definitions(state)
        if all(tool["name"] == "finish" for tool in definitions):
            return RunStep(action="finish", feedback=ToolFeedback(
                name="finish", outcome="success", message="No useful tool action remains; finalizing the current reconstruction.",
            ))
        # Fresh, bounded state context avoids storing provider reasoning items,
        # raw responses or image bytes in graph checkpoints. Feedback is data.
        data = {
            "input_options": state.input_options.model_dump(mode="json"),
            "confirmed_dimensions": (
                state.confirmed_dimensions.model_dump(mode="json")
                if state.confirmed_dimensions
                else None
            ),
            "questions": [q.model_dump(mode="json") for q in state.questions],
            "clarification_answers": state.clarification_answers,
            "object_mobility": state.object_mobility,
            "reconstruction_constraints": state.reconstruction_constraints.model_dump(mode="json"),
            "fidelity": state.proposal.fidelity.model_dump(mode="json") if state.proposal and state.proposal.fidelity else None,
            "observations": (
                state.analysis.observations.model_dump(mode="json")
                if state.analysis
                else None
            ),
            "eligible_questions": [
                {"kind": q.kind, "object_id": clarification_target(q)}
                for q in clarification_candidates(state)
            ],
            "scene_validation": (
                {"valid": state.proposal.validation.valid, "policy_version": state.proposal.validation.policy_version}
                if state.proposal and state.proposal.validation else None
            ),
            "unresolved_repair_available": False,
            "proposal_status": state.proposal.status if state.proposal else None,
            "unresolved_entities": (
                [
                    item.model_dump(mode="json")
                    for item in state.proposal.unresolved_entities
                ]
                if state.proposal
                else []
            ),
            "current_scene": (
                state.working_scene.model_dump(mode="json")
                if state.working_scene
                else None
            ),
            "tool_feedback": [
                item.model_dump(mode="json") for item in state.tool_feedback[-8:]
            ],
            "remaining": {
                "steps": state.limits.max_steps - state.steps_used,
                "model_calls": state.limits.max_model_calls - state.usage.model_calls,
                "tool_calls": state.limits.max_tool_calls - state.usage.tool_calls,
                "questions": 3 - len(state.questions),
            },
        }
        payload = {
            "model": self.settings.model_name,
            "instructions": AGENT_INSTRUCTIONS,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": json.dumps(data, allow_nan=False),
                        }
                    ],
                }
            ],
            "tools": definitions,
            "tool_choice": "required",
            "parallel_tool_calls": False,
            "max_output_tokens": self.settings.agent_max_output_tokens,
            "store": False,
            "truncation": "disabled",
        }
        if self.settings.agent_reasoning_effort != "default":
            payload["reasoning"] = {"effort": self.settings.agent_reasoning_effort}
        if (
            len(json.dumps(payload, allow_nan=False).encode())
            > self.settings.agent_max_request_bytes
        ):
            return failed("request_too_large")
        try:
            response = await self.provider.request(
                cast(ResponseCreateParamsNonStreaming, payload)
            )
        except APITimeoutError:
            return failed("analysis_timeout")
        except APIError:
            return failed("provider_unavailable")
        except AnalysisFailure as error:
            return failed(error.code)
        if any(
            isinstance(item, ResponseOutputMessage)
            and any(isinstance(part, ResponseOutputRefusal) for part in item.content)
            for item in response.output
        ):
            return failed("analysis_refused")
        if response.status != "completed":
            return failed("analysis_incomplete")
        calls = [
            item
            for item in response.output
            if isinstance(item, ResponseFunctionToolCall)
        ]
        try:
            if (
                len(calls) != 1
                or calls[0].status not in {None, "completed"}
                or len(calls[0].arguments.encode()) > 16384
            ):
                raise ValueError("Exactly one bounded tool call is required.")
            call = ToolCall.model_validate(
                {
                    "call_id": calls[0].call_id,
                    "name": calls[0].name,
                    "arguments": json.loads(
                        calls[0].arguments,
                        object_pairs_hook=unique_object,
                        parse_constant=reject_constant,
                    ),
                }
            )
            if call.name not in available_tools(state):
                raise ValueError("Unavailable tool.")
            ARGUMENT_MODELS[call.name].model_validate(call.arguments)
        except (ValidationError, ValueError, RecursionError):
            return RunStep(
                action="continue",
                feedback=ToolFeedback(
                    name=None,
                    outcome="rejected",
                    message="Return exactly one currently available tool with its strict arguments; no extra authority fields are allowed.",
                ),
            )
        return RunStep(action="continue", pending_tool=call)
