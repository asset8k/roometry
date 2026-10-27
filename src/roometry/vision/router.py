"""Owned photo observations and validated approximate base-scene proposals."""

from typing import cast

from fastapi import APIRouter, Request, Response

from roometry.agents.router import RunFailure
from roometry.config import Settings
from roometry.uploads.errors import UploadFailure
from roometry.uploads.session import check_origin, session
from roometry.vision.errors import AnalysisFailure
from roometry.vision.proposal_schemas import SceneProposalRequest, SceneProposalResponse
from roometry.vision.schemas import (
    AnalysisErrorResponse,
    AnalyzeRoomRequest,
    RoomAnalysisResponse,
)
from roometry.vision.service import AnalysisService

router = APIRouter(prefix="/api/rooms", tags=["room analysis"])


@router.post(
    "/analyze",
    response_model=RoomAnalysisResponse,
    summary="Analyze photos from the current session's unexpired upload",
    description="Opt-in paid model request. Reuses successful analysis for the same batch. Returns observations, not a reconstructed scene.",
    responses={
        code: {"model": AnalysisErrorResponse}
        for code in (400, 403, 404, 413, 415, 422, 429, 502, 503, 504)
    },
)
async def analyze_room(
    body: AnalyzeRoomRequest, request: Request, response: Response
) -> RoomAnalysisResponse:
    if request.app.state.quotas.enabled:
        raise RunFailure(409, "guided_generation_required", "Use Create room to start a quota-tracked AI session.")
    try:
        check_origin(request, cast(Settings, request.app.state.settings))
    except UploadFailure:
        raise AnalysisFailure(
            403, "forbidden_origin", "This origin cannot analyze room inputs."
        ) from None
    _, owner = session(request)
    result = await cast(AnalysisService, request.app.state.analysis_service).analyze(
        body.input_id, owner
    )
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post(
    "/propose-scene",
    response_model=SceneProposalResponse,
    summary="Confirm dimensions and propose a validated approximate base scene",
    description="Uses the current session's cached observations without repeating photo analysis. Requires confirmed room measurements unless estimates are explicitly accepted. At most two scene calls per analysis (initial attempt and one repair), including retries and dimension revisions. Invalid geometry is never returned as an accepted scene.",
    responses={
        code: {"model": AnalysisErrorResponse}
        for code in (400, 403, 404, 413, 415, 422, 429, 502, 503, 504)
    },
)
async def propose_scene(
    body: SceneProposalRequest, request: Request, response: Response
) -> SceneProposalResponse:
    if request.app.state.quotas.enabled:
        raise RunFailure(409, "guided_generation_required", "Use Create room to construct a scene within its AI session.")
    try:
        check_origin(request, cast(Settings, request.app.state.settings))
    except UploadFailure:
        raise AnalysisFailure(
            403, "forbidden_origin", "This origin cannot propose room scenes."
        ) from None
    _, owner = session(request)
    result = await cast(AnalysisService, request.app.state.analysis_service).propose(
        body, owner
    )
    response.headers["Cache-Control"] = "no-store"
    return result
