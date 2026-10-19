"""Analyze an existing private upload; no scene proposal or generation run yet."""

from typing import cast

from fastapi import APIRouter, Request, Response

from roometry.config import Settings
from roometry.uploads.errors import UploadFailure
from roometry.uploads.session import check_origin, session
from roometry.vision.errors import AnalysisFailure
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
