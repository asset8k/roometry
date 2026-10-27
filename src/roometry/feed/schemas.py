"""Public response allowlists, separate from private project/run serializers."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from roometry.projects.schemas import ProjectModel
from roometry.scenes.schemas import RoomScene, RoomType
from roometry.uploads.schemas import OpaqueId


class SharingOptionsRequest(ProjectModel):
    source_run_id: OpaqueId
    scene: RoomScene


class PublishRequest(ProjectModel):
    version_id: UUID
    expected_revision: Annotated[int, Field(strict=True, ge=1)]
    expected_publication_revision: Annotated[int, Field(strict=True, ge=0)]
    title: Annotated[str, Field(min_length=1, max_length=80)]
    summary: Annotated[str, Field(max_length=240)] = ""
    consent: bool = Field(strict=True)

    @field_validator("title", "summary", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def explicit_consent(self):
        if not self.consent:
            raise ValueError("Explicit publishing consent is required.")
        return self


class UnpublishRequest(ProjectModel):
    public_id: UUID
    expected_publication_revision: Annotated[int, Field(strict=True, ge=1)]


class FeedCard(ProjectModel):
    id: UUID
    title: str
    summary: str
    room_type: RoomType
    object_count: int
    published_at: datetime
    preview_url: str


class FeedPage(ProjectModel):
    items: list[FeedCard]
    next_cursor: str | None


class SharedRoom(FeedCard):
    scene: RoomScene
