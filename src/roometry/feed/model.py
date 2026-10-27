"""Explicit public snapshots. Private projects always remain owner-only."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from roometry.database import Base
from roometry.projects.model import DOCUMENT


class Publication(Base):
    __tablename__ = "publications"
    __table_args__ = (
        Index("ix_publications_feed", "active", "published_at", "public_id"),
    )
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    public_id: Mapped[UUID] = mapped_column(Uuid, unique=True)
    version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("scene_versions.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean)
    title: Mapped[str] = mapped_column(String(80))
    summary: Mapped[str] = mapped_column(String(240))
    scene: Mapped[dict | None] = mapped_column(DOCUMENT)
    preview: Mapped[bytes | None] = mapped_column(LargeBinary)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
