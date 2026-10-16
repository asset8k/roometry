"""FastAPI entry point: uv run uvicorn roometry.main:app --reload."""

from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from roometry.config import Settings, get_settings


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

    @application.get("/api/health", response_model=HealthResponse, tags=["health"])
    async def health() -> HealthResponse:
        # Liveness only: database, model provider, and MCP checks come later.
        return HealthResponse()

    return application


app = create_app()
