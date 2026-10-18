"""FastAPI entry point: uv run uvicorn roometry.main:app --reload."""

import logging
from contextlib import asynccontextmanager
from typing import Literal

import anyio
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from roometry.config import Settings, get_settings
from roometry.scenes.router import router as scenes_router
from roometry.uploads.errors import UploadFailure
from roometry.uploads.middleware import UploadBodyLimitMiddleware
from roometry.uploads.router import router as uploads_router
from roometry.uploads.service import UploadService
from roometry.uploads.storage import LocalInputStorage


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["roometry"] = "roometry"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else get_settings()
    service = UploadService(
        settings.public_upload_limits(), LocalInputStorage(settings.upload_storage_dir)
    )

    async def periodic_cleanup() -> None:
        while True:
            await anyio.sleep(settings.upload_cleanup_interval_seconds)
            try:
                await service.cleanup()
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

    application = FastAPI(title="Roometry API", version="0.1.0", lifespan=lifespan)
    application.state.settings = settings
    application.state.upload_service = service
    application.add_middleware(
        UploadBodyLimitMiddleware, max_bytes=settings.upload_max_body_bytes
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
