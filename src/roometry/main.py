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
from sqlalchemy.exc import SQLAlchemyError

from roometry.agents.adapter import RoomAgent
from roometry.agents.persistence import (
    DurableCheckpoints,
    RegisteredRuns,
    WorkspaceInputs,
)
from roometry.agents.router import RunFailure
from roometry.agents.router import router as agent_router
from roometry.agents.runs import AgentRuns
from roometry.agents.state import RunLimits
from roometry.agents.tools import SceneTools
from roometry.assessment.service import RoomAssessor
from roometry.auth.errors import AuthFailure
from roometry.auth.middleware import AuthBodyLimitMiddleware
from roometry.auth.router import AuthAttempts, clear_cookie
from roometry.auth.router import router as auth_router
from roometry.auth.service import AuthService
from roometry.config import Settings, get_settings
from roometry.database import Database
from roometry.feed.service import FeedService
from roometry.feed.router import router as feed_router
from roometry.mcp.furniture_client import FurnitureCatalogClient
from roometry.optimization.provider import OptimizationProvider
from roometry.optimization.router import router as optimization_router
from roometry.optimization.runs import OptimizationRuns
from roometry.projects.router import router as projects_router
from roometry.projects.service import ProjectService
from roometry.projects.storage import ProjectPhotos
from roometry.quota.router import router as quota_router
from roometry.quota.service import NetworkScope, QuotaFailure, Quotas
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
    database = (
        Database(settings.database_url.get_secret_value())
        if settings.database_url else None
    )
    quotas = Quotas(database, settings)
    service = UploadService(
        settings.public_upload_limits(), LocalInputStorage(settings.upload_storage_dir)
    )
    registered = RegisteredRuns(database, ProjectPhotos(settings.project_storage_dir), service.storage)
    workspace_inputs = WorkspaceInputs(registered)
    analyzer = PhotoAnalyzer(settings, analysis_client)
    analysis_service = AnalysisService(settings, workspace_inputs, analyzer)
    registered.analysis = analysis_service
    scene_tools = SceneTools(analysis_service, FurnitureCatalogClient(
        timeout_seconds=settings.mcp_furniture_timeout_seconds,
        max_response_bytes=settings.mcp_furniture_max_response_bytes,
    ))
    agent_runs = AgentRuns(
        workspace_inputs.resolve,
        RoomAgent(settings, analyzer),
        checkpoints=DurableCheckpoints(registered),
        execute_tool=scene_tools,
        review_scene=scene_tools.review,
        assess_room=RoomAssessor(analysis_service),
        quotas=quotas,
        # A scene tool may make an initial request and one repair. Its outer
        # deadline must allow both configured per-request deadlines to run.
        default_limits=RunLimits(
            max_steps=settings.agent_max_steps,
            max_model_calls=settings.agent_max_model_calls,
            max_tool_calls=settings.agent_max_tool_calls,
            step_timeout_seconds=2 * max(settings.analysis_timeout_seconds, settings.request_timeout("scene_proposal")) + 5
        ),
    )

    optimization_runs = OptimizationRuns(agent_runs, provider=OptimizationProvider(analyzer), catalog=scene_tools.catalog,
        max_candidates=settings.optimization_max_candidates,
        limits=RunLimits(max_model_calls=settings.optimization_max_model_calls,
            max_tool_calls=settings.agent_max_tool_calls, step_timeout_seconds=settings.analysis_timeout_seconds))

    async def periodic_cleanup() -> None:
        while True:
            await anyio.sleep(settings.upload_cleanup_interval_seconds)
            try:
                await service.cleanup()
                await analysis_service.cleanup()
                await agent_runs.cleanup()
                await optimization_runs.cleanup()
                await anyio.to_thread.run_sync(registered.cleanup)
            except (OSError, SQLAlchemyError):
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
                    if database:
                        await anyio.to_thread.run_sync(database.close)

    application = FastAPI(title="Roometry API", version="0.1.0", lifespan=lifespan)
    application.state.settings = settings
    application.state.database = database
    application.state.feed_service = FeedService(database)
    application.state.feed_render_limiter = anyio.CapacityLimiter(2)
    application.state.quotas = quotas
    application.state.registered_runs = registered
    application.state.auth_service = AuthService(database, settings)
    application.state.auth_hash_limiter = anyio.CapacityLimiter(2)
    application.state.auth_attempts = AuthAttempts(settings.auth_attempts_per_minute)
    application.state.project_service = ProjectService(database, ProjectPhotos(settings.project_storage_dir), workspace_inputs, agent_runs, optimization_runs)
    application.state.upload_service = service
    application.state.analysis_service = analysis_service
    application.state.agent_runs = agent_runs
    application.state.optimization_runs = optimization_runs
    application.add_middleware(
        UploadBodyLimitMiddleware, max_bytes=settings.upload_max_body_bytes
    )
    application.add_middleware(
        AnalysisRequestLimitMiddleware, max_bytes=settings.analysis_max_body_bytes
    )
    application.add_middleware(AuthBodyLimitMiddleware)
    application.add_middleware(AuthBodyLimitMiddleware, prefix="/api/projects", max_bytes=8 * 1024 * 1024, subject="saved room data")

    if settings.cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    application.include_router(scenes_router)
    application.include_router(auth_router)
    application.include_router(projects_router)
    application.include_router(feed_router)
    application.include_router(uploads_router)
    application.include_router(analysis_router)
    application.include_router(agent_router)
    application.include_router(optimization_router)
    application.include_router(quota_router)
    application.add_middleware(NetworkScope)

    @application.exception_handler(QuotaFailure)
    async def quota_error(_request: Request, error: QuotaFailure):
        headers = {"Cache-Control": "no-store"}
        if error.retry_after is not None: headers["Retry-After"] = str(error.retry_after)
        return JSONResponse({"detail": [{"code": error.code, "message": error.message}]}, status_code=429, headers=headers)

    @application.exception_handler(SQLAlchemyError)
    async def persistence_error(_request:Request,_error:SQLAlchemyError):
        return JSONResponse({"detail":[{"code":"workspace_unavailable","message":"Saved work is temporarily unavailable. Retry the same request to check its status."}]},status_code=503,headers={"Cache-Control":"no-store"})

    @application.exception_handler(AuthFailure)
    async def auth_error(_request: Request, error: AuthFailure) -> JSONResponse:
        response = JSONResponse(
            error.response(), status_code=error.status,
            headers={"Cache-Control": "no-store"},
        )
        if error.code == "invalid_session":
            clear_cookie(response, settings)
        if error.status == 429:
            response.headers["Retry-After"] = "60"
        return response

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
        if _request.url.path.endswith("/publication") or _request.url.path.endswith("/sharing-options"):
            return await auth_error(_request, AuthFailure(422, "invalid_publication", "Choose a saved layout, a title, and explicit publishing consent."))
        if _request.url.path.startswith("/api/projects"):
            return await auth_error(_request, AuthFailure(422, "invalid_project", "Check the room title, scenes and conversation before saving."))
        if _request.url.path.startswith("/api/auth/"):
            return await auth_error(
                _request,
                AuthFailure(
                    422, "invalid_request",
                    "Use a username of 3–32 letters, numbers or underscores "
                    "and a password of 12–128 characters.",
                ),
            )
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
