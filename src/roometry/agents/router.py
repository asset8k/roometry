"""Private, bounded start/read/answer/cancel endpoints for guest-owned runs."""

from collections.abc import Awaitable
from typing import cast

from fastapi import APIRouter, Request, Response
from pydantic import Field

from roometry.agents.checkpointing import RunBusy, RunCapacityReached, RunUnavailable
from roometry.agents.clarification import AnswerConflict, InvalidAnswer
from roometry.agents.runs import AgentRuns
from roometry.agents.state import AgentState, ClarificationAnswer, RunModel
from roometry.config import Settings
from roometry.uploads.schemas import OpaqueId
from roometry.uploads.session import check_origin, session


class StartRunRequest(RunModel):
    input_id: OpaqueId
    request_id: OpaqueId = Field(
        description="Client-generated UUID v4. Reuse it with the same input_id when retrying a start; a retry reads the same run and never starts another generation."
    )


class EmptyRequest(RunModel):
    pass


class RunIssue(RunModel):
    code: str
    message: str


class RunErrorResponse(RunModel):
    detail: list[RunIssue]


class RunFailure(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(code)
        self.status = status
        self.response = RunErrorResponse(detail=[RunIssue(code=code, message=message)])


router = APIRouter(
    prefix="/api/agent-runs",
    tags=["agent runs"],
    responses={
        code: {"model": RunErrorResponse}
        for code in (400, 403, 404, 409, 413, 415, 422, 429)
    },
)


def access(
    request: Request, response: Response, *, mutation: bool = False
) -> tuple[AgentRuns, str]:
    if mutation:
        check_origin(request, cast(Settings, request.app.state.settings))
    response.headers["Cache-Control"] = "no-store"
    _, owner = session(request)
    return cast(AgentRuns, request.app.state.agent_runs), owner


async def result(operation: Awaitable[AgentState]) -> AgentState:
    try:
        return await operation
    except RunUnavailable:
        raise RunFailure(
            404, "run_not_found", "This run is unavailable or has expired."
        ) from None
    except RunBusy:
        raise RunFailure(
            409, "run_busy", "This run is already processing another operation."
        ) from None
    except RunCapacityReached:
        raise RunFailure(
            429, "run_capacity_reached",
            "Temporary run capacity is full. Try again later.",
        ) from None
    except InvalidAnswer as error:
        raise RunFailure(422, "invalid_answer", str(error)) from None
    except AnswerConflict as error:
        raise RunFailure(409, "answer_conflict", str(error)) from None


@router.post(
    "", response_model=AgentState,
    summary="Start a bounded agent run; return at a question or terminal state",
)
async def start_run(
    body: StartRunRequest, request: Request, response: Response
) -> AgentState:
    runs, owner = access(request, response, mutation=True)
    return await result(runs.start(body.input_id, owner, body.request_id))


@router.get(
    "", response_model=AgentState,
    summary="Find an owned run by its start request ID without executing it",
)
async def read_started_run(request_id: OpaqueId, request: Request, response: Response) -> AgentState:
    runs, owner = access(request, response)
    return await result(runs.read_request(request_id, owner))


@router.get(
    "/{run_id}", response_model=AgentState,
    summary="Read the current owned run without executing or resuming it",
)
async def read_run(run_id: OpaqueId, request: Request, response: Response) -> AgentState:
    runs, owner = access(request, response)
    return await result(runs.read(run_id, owner))


@router.post(
    "/{run_id}/answer", response_model=AgentState,
    summary="Answer the pending question and resume the same graph",
)
async def answer_run(
    run_id: OpaqueId, body: ClarificationAnswer, request: Request, response: Response
) -> AgentState:
    runs, owner = access(request, response, mutation=True)
    return await result(runs.answer(run_id, owner, body))


@router.post(
    "/{run_id}/cancel", response_model=AgentState,
    summary="Cancel an owned run, including one waiting for an answer",
    description="Waiting runs stop immediately. During an in-flight provider request cancellation is cooperative: read the run until it reaches cancelled; no subsequent tool or model call is dispatched.",
)
async def cancel_run(
    run_id: OpaqueId, body: EmptyRequest, request: Request, response: Response
) -> AgentState:
    runs, owner = access(request, response, mutation=True)
    return await result(runs.cancel(run_id, owner))
