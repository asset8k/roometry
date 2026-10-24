"""Settings used by the initial API scaffold."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from roometry.uploads.schemas import MAX_IMAGES, MIB, UploadLimits
from roometry.model_calls import ModelOperation, ModelRoute, ReasoningEffort


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_environment: Literal["development", "test", "production"] = "development"
    cors_origins: list[str] = Field(default_factory=list)

    # Limits apply to original uploads and normalized private derivatives.
    max_images_per_request: int = Field(default=MAX_IMAGES, ge=1, le=MAX_IMAGES)
    upload_max_file_bytes: int = Field(default=8 * MIB, gt=0)
    upload_max_total_file_bytes: int = Field(default=80 * MIB, gt=0)
    upload_max_body_bytes: int = Field(default=82 * MIB, gt=0)
    upload_min_edge_px: int = Field(default=256, gt=0)
    upload_max_edge_px: int = Field(default=10_000, gt=0)
    upload_max_pixels: int = Field(default=40_000_000, gt=0)
    upload_prepared_max_edge_px: int = Field(default=2048, gt=0)
    upload_prepared_max_bytes: int = Field(default=4 * MIB, gt=0)
    upload_ttl_seconds: int = Field(default=3600, gt=0)
    upload_storage_dir: Path = (
        Path(__file__).resolve().parents[2] / "uploads" / "room-inputs"
    )
    upload_cleanup_interval_seconds: int = Field(default=60, gt=0)

    # Opt-in paid analysis. Missing configuration never breaks the viewer/API health.
    analysis_enabled: bool = False
    openai_api_key: SecretStr | None = None
    model_name: str = Field(default="gpt-6-luna", min_length=1, max_length=128)
    # MODEL_NAME remains the legacy fallback for the economical stages only.
    analysis_model_name: str | None = Field(default=None, min_length=1, max_length=128)
    agent_model_name: str | None = Field(default=None, min_length=1, max_length=128)
    review_model_name: str | None = Field(default=None, min_length=1, max_length=128)
    scene_repair_model_name: str | None = Field(default=None, min_length=1, max_length=128)
    scene_model_name: str = Field(default="gpt-6.1-sol", min_length=1, max_length=128)
    analysis_reasoning_effort: ReasoningEffort = "medium"
    # A bounded material/relationship checklist needs room for its JSON output;
    # medium reasoning can consume the review allowance before checks finish.
    review_reasoning_effort: ReasoningEffort = "low"
    visual_review_enabled: bool = True
    review_max_output_tokens: int = Field(default=6144, ge=512, le=8192)
    scene_repair_reasoning_effort: ReasoningEffort = "medium"
    analysis_timeout_seconds: float = Field(
        default=90, gt=0, le=300, allow_inf_nan=False
    )
    analysis_max_body_bytes: int = Field(default=4096, ge=128, le=16384)
    analysis_max_request_bytes: int = Field(default=45_000_000, ge=1024, le=50_000_000)
    analysis_max_output_tokens: int = Field(default=16384, ge=128, le=32768)
    # Scene drafts carry geometry for every object, unlike photo observations.
    scene_max_output_tokens: int = Field(default=16384, ge=128, le=32768)
    scene_reasoning_effort: ReasoningEffort = "medium"
    analysis_max_output_bytes: int = Field(default=262144, ge=1024, le=1048576)
    analysis_max_provider_attempts: int = Field(default=2, ge=1, le=3)
    analysis_max_cached_inputs: int = Field(default=128, ge=1, le=1024)
    agent_max_steps: int = Field(default=16, ge=1, le=32)
    agent_max_model_calls: int = Field(default=20, ge=1, le=32)
    agent_max_tool_calls: int = Field(default=24, ge=1, le=64)
    agent_max_output_tokens: int = Field(default=2048, ge=128, le=8192)
    agent_max_request_bytes: int = Field(default=524288, ge=4096, le=2097152)
    mcp_furniture_transport: Literal["stdio"] = "stdio"
    mcp_furniture_timeout_seconds: float = Field(default=8., ge=.1, le=30., allow_inf_nan=False)
    mcp_furniture_max_response_bytes: int = Field(default=65_536, ge=4096, le=262_144)

    agent_reasoning_effort: ReasoningEffort = "low"

    @field_validator("model_name", mode="before")
    @classmethod
    def default_blank_model(cls, value: object) -> object:
        # Older .env templates included an empty placeholder.
        if isinstance(value, str):
            return value.strip() or "gpt-6-luna"
        return value

    @field_validator(
        "analysis_model_name", "agent_model_name", "review_model_name",
        "scene_repair_model_name", "scene_model_name", mode="before",
    )
    @classmethod
    def trim_stage_model(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("Stage model names must not be blank.")
        return value

    def model_route(self, operation: ModelOperation) -> ModelRoute:
        return {
            "analysis": ModelRoute(
                self.analysis_model_name or self.model_name, self.analysis_reasoning_effort
            ),
            "agent_decision": ModelRoute(
                self.agent_model_name or self.model_name, self.agent_reasoning_effort
            ),
            "visual_review": ModelRoute(
                self.review_model_name or self.model_name, self.review_reasoning_effort
            ),
            "visual_repair": ModelRoute(
                self.scene_repair_model_name or self.model_name, self.scene_repair_reasoning_effort
            ),
            "scene_proposal": ModelRoute(self.scene_model_name, self.scene_reasoning_effort),
            "scene_repair": ModelRoute(
                self.scene_repair_model_name or self.model_name,
                self.scene_repair_reasoning_effort,
            ),
        }[operation]

    @model_validator(mode="after")
    def consistent_model_routes(self) -> "Settings":
        operations: tuple[ModelOperation, ...] = (
            "analysis", "agent_decision", "visual_review", "visual_repair", "scene_proposal", "scene_repair"
        )
        for operation in operations:
            route = self.model_route(operation)
            is_sol = route.model in {"gpt-6-sol", "gpt-6.1-sol"} or route.model.startswith(
                ("gpt-6-sol-", "gpt-6.1-sol-")
            )
            if is_sol and operation != "scene_proposal":
                raise ValueError("Sol is reserved for the single initial scene construction request.")
            is_sol_61 = route.model == "gpt-6.1-sol" or route.model.startswith("gpt-6.1-sol-")
            if is_sol_61 and route.reasoning_effort == "none":
                raise ValueError("Sol requires low or greater reasoning effort, or default.")
        return self

    @model_validator(mode="after")
    def consistent_upload_policy(self) -> "Settings":
        if "*" in self.cors_origins:
            raise ValueError(
                "Credentialed upload requests require explicit CORS origins."
            )
        if self.upload_max_file_bytes > self.upload_max_total_file_bytes:
            raise ValueError(
                "The combined upload limit must accommodate one maximum-sized file."
            )
        if self.upload_max_body_bytes <= self.upload_max_total_file_bytes + 16384:
            raise ValueError(
                "The HTTP body limit must allow file bytes, options, and multipart overhead."
            )
        if self.upload_min_edge_px > self.upload_max_edge_px:
            raise ValueError("The minimum image edge cannot exceed the maximum.")
        if self.upload_max_pixels < self.upload_min_edge_px**2:
            raise ValueError(
                "The pixel limit must accommodate the minimum image dimensions."
            )
        if self.upload_prepared_max_edge_px > self.upload_max_edge_px:
            raise ValueError(
                "The prepared image edge cannot exceed the source edge limit."
            )
        return self

    def public_upload_limits(self) -> UploadLimits:
        return UploadLimits(
            max_images=self.max_images_per_request,
            max_file_bytes=self.upload_max_file_bytes,
            max_total_file_bytes=self.upload_max_total_file_bytes,
            max_body_bytes=self.upload_max_body_bytes,
            min_edge_px=self.upload_min_edge_px,
            max_edge_px=self.upload_max_edge_px,
            max_pixels=self.upload_max_pixels,
            prepared_max_edge_px=self.upload_prepared_max_edge_px,
            prepared_max_bytes=self.upload_prepared_max_bytes,
            ttl_seconds=self.upload_ttl_seconds,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
