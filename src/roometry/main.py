"""FastAPI entry point: uv run uvicorn roometry.main:app --reload."""

from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from roometry.config import Settings, get_settings
from roometry.scenes.router import router as scenes_router


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["roometry"] = "roometry"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else get_settings()
    application = FastAPI(title="Roometry API", version="0.1.0")

    if settings.cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    application.include_router(scenes_router)

    @application.exception_handler(RequestValidationError)
    async def request_validation_error(
        _request: Request, error: RequestValidationError,
    ) -> JSONResponse:
        # Raw error input may contain NaN/Infinity or exception context that
        # JSONResponse cannot encode. Return the useful, JSON-safe error fields.
        return JSONResponse(status_code=422, content={
            "detail": [
                {"type": issue["type"], "loc": issue["loc"], "msg": issue["msg"]}
                for issue in error.errors()
            ],
        })

    @application.get("/api/health", response_model=HealthResponse, tags=["health"])
    async def health() -> HealthResponse:
        # Liveness only: database, model provider, and MCP checks come later.
        return HealthResponse()

    return application


app = create_app()
