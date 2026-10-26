"""Private room snapshots and immutable scene versions; image bytes live outside SQL."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from roometry.database import Base

DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("user_id", "source_run_id"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    source_run_id: Mapped[str] = mapped_column(String(36))
    title: Mapped[str] = mapped_column(String(120))
    revision: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(DOCUMENT)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProjectImage(Base):
    __tablename__ = "project_images"
    __table_args__ = (UniqueConstraint("project_id", "source_image_id"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    source_image_id: Mapped[str] = mapped_column(String(36))
    storage_key: Mapped[str] = mapped_column(String(80))
    sha256: Mapped[str] = mapped_column(String(64))
    sort_order: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    byte_count: Mapped[int] = mapped_column(Integer)


class SceneVersion(Base):
    __tablename__ = "scene_versions"
    __table_args__ = (UniqueConstraint("project_id", "fingerprint"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    parent_id: Mapped[UUID | None] = mapped_column(ForeignKey("scene_versions.id"))
    fingerprint: Mapped[str] = mapped_column(String(64))
    scene_json: Mapped[dict] = mapped_column(DOCUMENT)
    validation_summary: Mapped[dict] = mapped_column(DOCUMENT)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProjectSave(Base):
    __tablename__ = "project_saves"
    __table_args__ = (UniqueConstraint("user_id", "request_id"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    request_id: Mapped[str] = mapped_column(String(36))
    body_hash: Mapped[str] = mapped_column(String(64))
    response_json: Mapped[dict] = mapped_column(DOCUMENT)
