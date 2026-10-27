"""Durable allowances; room/action IDs survive saved-room reopening."""

from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from roometry.database import Base


class QuotaBucket(Base):
    __tablename__ = "quota_buckets"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    day: Mapped[date] = mapped_column(Date)
    used: Mapped[int] = mapped_column(Integer)


class QuotaRoom(Base):
    __tablename__ = "quota_rooms"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    bucket: Mapped[str] = mapped_column(ForeignKey("quota_buckets.key"))
    ip_bucket: Mapped[str | None] = mapped_column(String(80))
    day: Mapped[date] = mapped_column(Date)
    actions: Mapped[int] = mapped_column(Integer)
    baseline: Mapped[int] = mapped_column(Integer)
    first_action: Mapped[str] = mapped_column(String(36))


class QuotaAction(Base):
    __tablename__ = "quota_actions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    room_id: Mapped[str] = mapped_column(
        ForeignKey("quota_rooms.id", ondelete="CASCADE"), index=True
    )
    started: Mapped[bool] = mapped_column(Boolean)
