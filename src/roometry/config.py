"""Settings used by the initial API scaffold."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from roometry.uploads.schemas import MAX_IMAGES, MIB, UploadLimits


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

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
    upload_storage_dir: Path = Path(__file__).resolve().parents[2] / "uploads" / "room-inputs"
    upload_cleanup_interval_seconds: int = Field(default=60, gt=0)

    @model_validator(mode="after")
    def consistent_upload_policy(self) -> "Settings":
        if "*" in self.cors_origins:
            raise ValueError("Credentialed upload requests require explicit CORS origins.")
        if self.upload_max_file_bytes > self.upload_max_total_file_bytes:
            raise ValueError("The combined upload limit must accommodate one maximum-sized file.")
        if self.upload_max_body_bytes <= self.upload_max_total_file_bytes + 16384:
            raise ValueError("The HTTP body limit must allow file bytes, options, and multipart overhead.")
        if self.upload_min_edge_px > self.upload_max_edge_px:
            raise ValueError("The minimum image edge cannot exceed the maximum.")
        if self.upload_max_pixels < self.upload_min_edge_px ** 2:
            raise ValueError("The pixel limit must accommodate the minimum image dimensions.")
        if self.upload_prepared_max_edge_px > self.upload_max_edge_px:
            raise ValueError("The prepared image edge cannot exceed the source edge limit.")
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
