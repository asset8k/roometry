"""FastAPI entry point: uv run uvicorn roometry.main:app --reload."""

import logging
from contextlib import asynccontextmanager
from typing import Literal

import anyio
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from openai import AsyncOpenAI
from pydantic import BaseModel

from roometry.agents.adapter import RoomAgent
from roometry.agents.router import RunFailure
from roometry.agents.router import router as agent_router
from roometry.agents.runs import AgentRuns
from roometry.agents.state import RunLimits
from roometry.agents.tools import SceneTools
from roometry.config import Settings, get_settings
from roometry.mcp.furniture_client import FurnitureCatalogClient
from roometry.scenes.router import router as scenes_router
from roometry.uploads.errors import UploadFailure
from roometry.uploads.middleware import UploadBodyLimitMiddleware
from roometry.uploads.router import router as uploads_router
from roometry.uploads.service import UploadService
from roometry.uploads.storage import LocalInputStorage
from roometry.vision.analyzer import PhotoAnalyzer
from roometry.vision.errors import AnalysisFailure
from roometry.vision.middleware import ANALYSIS_PATHS, AnalysisRequestLimitMiddleware
from roometry.vision.router import router as analysis_router
from roometry.vision.service import AnalysisService


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["roometry"] = "roometry"


def create_app(
    settings: Settings | None = None, *, analysis_client: AsyncOpenAI | None = None
) -> FastAPI:
    settings = settings if settings is not None else get_settings()
    service = UploadService(
        settings.public_upload_limits(), LocalInputStorage(settings.upload_storage_dir)
    )
    analyzer = PhotoAnalyzer(settings, analysis_client)
    analysis_service = AnalysisService(settings, service.storage, analyzer)
    scene_tools = SceneTools(analysis_service, FurnitureCatalogClient(
        timeout_seconds=settings.mcp_furniture_timeout_seconds,
        max_response_bytes=settings.mcp_furniture_max_response_bytes,
    ))
    agent_runs = AgentRuns(
        service.storage.resolve,
        RoomAgent(settings, analyzer),
        execute_tool=scene_tools,
        review_scene=scene_tools.review,
        # A scene tool may make an initial request and one repair. Its outer
        # deadline must allow both configured per-request deadlines to run.
        default_limits=RunLimits(
            max_steps=settings.agent_max_steps,
            max_model_calls=settings.agent_max_model_calls,
            max_tool_calls=settings.agent_max_tool_calls,
            step_timeout_seconds=2 * settings.analysis_timeout_seconds + 5
        ),
    )

    async def periodic_cleanup() -> None:
        while True:
            await anyio.sleep(settings.upload_cleanup_interval_seconds)
            try:
                await service.cleanup()
                await analysis_service.cleanup()
                await agent_runs.cleanup()
            except OSError:
                logging.getLogger(__name__).warning(
                    "Temporary upload cleanup will be retried."
                )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            await service.start()
        except OSError:
            # Keep liveness and non-upload features available. Upload requests
            # return a safe 503 until the local storage problem is resolved.
            logging.getLogger(__name__).warning(
                "Temporary upload storage is unavailable."
            )
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(periodic_cleanup)
            try:
                yield
            finally:
                tasks.cancel_scope.cancel()
                with anyio.CancelScope(shield=True):
                    await analyzer.close()

    application = FastAPI(title="Roometry API", version="0.1.0", lifespan=lifespan)
    application.state.settings = settings
    application.state.upload_service = service
    application.state.analysis_service = analysis_service
    application.state.agent_runs = agent_runs
    application.add_middleware(
        UploadBodyLimitMiddleware, max_bytes=settings.upload_max_body_bytes
    )
    application.add_middleware(
        AnalysisRequestLimitMiddleware, max_bytes=settings.analysis_max_body_bytes
    )

    if settings.cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    application.include_router(scenes_router)
    application.include_router(uploads_router)
    application.include_router(analysis_router)
    application.include_router(agent_router)

    @application.exception_handler(RunFailure)
    async def run_error(_request: Request, error: RunFailure) -> JSONResponse:
        return JSONResponse(
            error.response.model_dump(mode="json"),
            status_code=error.status,
            headers={"Cache-Control": "no-store"},
        )

    @application.exception_handler(AnalysisFailure)
    async def analysis_error(_request: Request, error: AnalysisFailure) -> JSONResponse:
        return JSONResponse(
            error.response.model_dump(mode="json"),
            status_code=error.status,
            headers={"Cache-Control": "no-store"},
        )

    @application.exception_handler(UploadFailure)
    async def upload_error(_request: Request, error: UploadFailure) -> JSONResponse:
        return JSONResponse(
            error.response.model_dump(mode="json"),
            status_code=error.status,
            headers={"Cache-Control": "no-store"},
        )

    @application.exception_handler(RequestValidationError)
    async def request_validation_error(
        _request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        if _request.url.path == "/api/agent-runs" or _request.url.path.startswith(
            "/api/agent-runs/"
        ):
            return await run_error(
                _request,
                RunFailure(
                    422,
                    "invalid_request",
                    "Send a valid agent run request using only the documented fields.",
                ),
            )
        if _request.url.path in ANALYSIS_PATHS:
            return await analysis_error(
                _request,
                AnalysisFailure(
                    422,
                    "invalid_request",
                    "Send only a valid input_id from your current room upload.",
                ),
            )
        # Raw error input may contain NaN/Infinity or exception context that
        # JSONResponse cannot encode. Return the useful, JSON-safe error fields.
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {"type": issue["type"], "loc": issue["loc"], "msg": issue["msg"]}
                    for issue in error.errors()
                ],
            },
        )

    @application.get("/api/health", response_model=HealthResponse, tags=["health"])
    async def health() -> HealthResponse:
        # Liveness only: database, model provider, and MCP checks come later.
        return HealthResponse()

    return application


app = create_app()
