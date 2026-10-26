"""Private registered execution state, separate from saved project history."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from roometry.database import Base
from roometry.projects.model import DOCUMENT


class RuntimeInput(Base):
    __tablename__ = "runtime_inputs"
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    prepared: Mapped[dict] = mapped_column(DOCUMENT)
    images: Mapped[list] = mapped_column(DOCUMENT)
    vision: Mapped[dict | None] = mapped_column(DOCUMENT)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RuntimeRun(Base):
    __tablename__ = "runtime_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "input_id"],
            ["runtime_inputs.user_id", "runtime_inputs.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("user_id", "kind", "request_id"),
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    input_id: Mapped[str] = mapped_column(String(36))
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20))
    request_id: Mapped[str | None] = mapped_column(String(36))
    data: Mapped[dict] = mapped_column(DOCUMENT)
    status: Mapped[str] = mapped_column(String(30))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    usage: Mapped[dict] = mapped_column(DOCUMENT)
    execution_owner: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GraphCheckpoint(Base):
    __tablename__ = "graph_checkpoints"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "run_id"],
            ["runtime_runs.user_id", "runtime_runs.id"],
            ondelete="CASCADE",
        ),
    )
    user_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    namespace: Mapped[str] = mapped_column(String(512), primary_key=True)
    checkpoint_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    parent_id: Mapped[str | None] = mapped_column(String(80))
    type: Mapped[str] = mapped_column(String(20))
    payload: Mapped[bytes] = mapped_column(LargeBinary)
    metadata_json: Mapped[dict] = mapped_column(DOCUMENT)


class GraphWrite(Base):
    __tablename__ = "graph_writes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "run_id"],
            ["runtime_runs.user_id", "runtime_runs.id"],
            ondelete="CASCADE",
        ),
    )
    user_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    namespace: Mapped[str] = mapped_column(String(512), primary_key=True)
    checkpoint_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    idx: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[str] = mapped_column(String(128))
    type: Mapped[str] = mapped_column(String(20))
    payload: Mapped[bytes] = mapped_column(LargeBinary)
    task_path: Mapped[str] = mapped_column(String(512))
