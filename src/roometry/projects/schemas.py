"""Only an explicit authenticated save can turn a temporary room into a private project."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from roometry.scenes.schemas import RoomScene
from roometry.uploads.schemas import OpaqueId


class ProjectModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SavedMessage(ProjectModel):
    id: Annotated[str, Field(min_length=1, max_length=80)]
    role: Literal["user", "assistant"]
    text: Annotated[str, Field(min_length=1, max_length=3000)]


class ManualCopy(ProjectModel):
    parent_run_id: OpaqueId | None
    scene: RoomScene


class SaveProjectRequest(ProjectModel):
    request_id: OpaqueId
    source_run_id: OpaqueId
    project_id: UUID | None = None
    expected_revision: Annotated[int, Field(strict=True, ge=0)] = 0
    title: Annotated[str, Field(min_length=1, max_length=120)]
    improvement_run_ids: Annotated[list[OpaqueId], Field(max_length=8)] = Field(
        default_factory=list
    )
    manual_copies: Annotated[list[ManualCopy], Field(min_length=1, max_length=9)]
    view: Literal["original", "improved", "editing"]
    editing_parent_run_id: OpaqueId | None
    selected_improvement_id: OpaqueId | None
    messages: Annotated[list[SavedMessage], Field(max_length=40)] = Field(
        default_factory=list
    )

    @field_validator("title", mode="before")
    @classmethod
    def trim_title(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def unique_entries(self):
        parents = [copy.parent_run_id for copy in self.manual_copies]
        if len(parents) != len(set(parents)) or None not in parents:
            raise ValueError("Supply one editable copy per scene, including Original.")
        if len(self.improvement_run_ids) != len(set(self.improvement_run_ids)):
            raise ValueError("Improvement references must be unique.")
        if len({message.id for message in self.messages}) != len(self.messages):
            raise ValueError("Message identifiers must be unique.")
        if (self.project_id is None) != (self.expected_revision == 0):
            raise ValueError("Updating a room requires its saved revision.")
        return self


class SavedProject(ProjectModel):
    id: UUID
    title: str
    revision: int
    image_count: int
    version_count: int
    saved_at: str
    visibility: Literal["private"] = "private"

class ProjectRevision(ProjectModel):
    expected_revision: Annotated[int, Field(strict=True,ge=1)]

class RenameProject(ProjectRevision):
    title: Annotated[str, Field(min_length=1,max_length=120)]
    @field_validator('title',mode='before')
    @classmethod
    def trim(cls,value):return value.strip() if isinstance(value,str) else value
