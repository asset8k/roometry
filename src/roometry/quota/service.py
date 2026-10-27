"""Atomic SQL reservations. No process-local counters or inference on reads."""

import hashlib
import hmac
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from roometry.quota.model import QuotaAction, QuotaBucket, QuotaRoom

request_ip = ContextVar("quota_request_ip", default=None)


class NetworkScope:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        token = request_ip.set((scope.get("client") or (None,))[0])
        try:
            await self.app(scope, receive, send)
        finally:
            request_ip.reset(token)


class QuotaFailure(Exception):
    def __init__(self, code, message, retry_after=None):
        self.code, self.message, self.retry_after = code, message, retry_after


class Quotas:
    def __init__(self, database, settings, now=lambda: datetime.now(UTC)):
        self.database, self.settings, self.now = database, settings, now

    @property
    def enabled(self):
        return self.settings.quota_enabled

    def key(self, value):
        return hmac.new(
            self.settings.jwt_secret_key.get_secret_value().encode(),
            value.encode(),
            hashlib.sha256,
        ).hexdigest()

    def reset(self):
        return datetime.combine(
            self.now().astimezone(UTC).date() + timedelta(days=1), time(), UTC
        )

    @contextmanager
    def transaction(self):
        with self.database.sessions.begin() as db:
            if self.database.engine.dialect.name == "sqlite":
                db.execute(text("BEGIN IMMEDIATE"))
            yield db

    def bucket(self, db, key):
        day = self.now().astimezone(UTC).date()
        insert = (
            sqlite_insert
            if self.database.engine.dialect.name == "sqlite"
            else pg_insert
        )
        db.execute(
            insert(QuotaBucket)
            .values(key=key, day=day, used=0)
            .on_conflict_do_nothing(index_elements=["key"])
        )
        row = db.scalar(
            select(QuotaBucket).where(QuotaBucket.key == key).with_for_update()
        )
        if row.day != day:
            row.day, row.used = day, 0
        return row

    def reserve(self, owner, room_id, action_id, *, initial=False):
        if not self.enabled:
            return
        with self.transaction() as db:
            daily = self.bucket(db, self.key("owner:" + owner))
            room = db.scalar(
                select(QuotaRoom).where(QuotaRoom.id == room_id).with_for_update()
            )
            receipt = db.get(QuotaAction, action_id)
            if receipt:
                if receipt.room_id != room_id:
                    raise ValueError("Action belongs to another session.")
                return
            if room is None:
                if daily.used >= self.settings.ai_sessions_per_day:
                    raise QuotaFailure(
                        "daily_session_limit",
                        "Your daily AI session allowance is used. It resets at 00:00 UTC. Your existing rooms remain available.",
                        max(1, int((self.reset() - self.now()).total_seconds())),
                    )
                ip_key = (
                    self.key("ip:" + request_ip.get())
                    if not owner.startswith("account:") and request_ip.get()
                    else None
                )
                if ip_key:
                    ip = self.bucket(db, ip_key)
                    if ip.used >= self.settings.ai_guest_sessions_per_ip_per_day:
                        raise QuotaFailure(
                            "guest_network_limit",
                            "This network has reached its guest AI session allowance. Sign in or try after 00:00 UTC.",
                            max(1, int((self.reset() - self.now()).total_seconds())),
                        )
                    ip.used += 1
                daily.used += 1
                # A pre-quota/saved reconstruction still occupies the first action.
                room = QuotaRoom(
                    id=room_id,
                    bucket=daily.key,
                    ip_bucket=ip_key,
                    day=daily.day,
                    actions=0 if initial else 1,
                    baseline=0 if initial else 1,
                    first_action=action_id,
                )
                db.add(room)
            if room.actions >= self.settings.ai_actions_per_session:
                raise QuotaFailure(
                    "session_action_limit",
                    "This room has used its AI actions. You can still edit it manually, save it, or start another room if you have a session remaining today.",
                )
            room.actions += 1
            db.flush()
            db.add(QuotaAction(id=action_id, room_id=room_id, started=False))

    def check_new(self, owner):
        if self.enabled and self.status(owner)["sessions_remaining"] == 0:
            raise QuotaFailure(
                "daily_session_limit",
                "Your daily AI session allowance is used. It resets at 00:00 UTC. Your existing rooms remain available.",
                max(1, int((self.reset() - self.now()).total_seconds())),
            )

    def started(self, action_id):
        if not self.enabled:
            return
        with self.database.sessions.begin() as db:
            action = db.get(QuotaAction, action_id)
            if action is None:
                raise RuntimeError("Paid dispatch needs a quota reservation.")
            action.started = True

    def release_unused(self, action_id):
        if not self.enabled:
            return
        with self.transaction() as db:
            action = db.get(QuotaAction, action_id)
            if action is None or action.started:
                return
            room = db.get(QuotaRoom, action.room_id)
            keys = (room.bucket, room.ip_bucket)
            buckets = [
                db.scalar(
                    select(QuotaBucket).where(QuotaBucket.key == key).with_for_update()
                )
                for key in keys
                if key
            ]
            room = db.scalar(
                select(QuotaRoom)
                .where(QuotaRoom.id == action.room_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            action = db.scalar(
                select(QuotaAction)
                .where(QuotaAction.id == action_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if action is None or action.started:
                return
            room.actions -= 1
            db.delete(action)
            if room.actions == room.baseline and room.first_action == action_id:
                for bucket in buckets:
                    if bucket and bucket.day == room.day:
                        bucket.used = max(0, bucket.used - 1)
                db.flush()
                db.delete(room)

    def status(self, owner, room_id=None):
        result = {
            "enabled": self.enabled,
            "sessions_limit": self.settings.ai_sessions_per_day,
            "sessions_remaining": None,
            "actions_limit": self.settings.ai_actions_per_session,
            "actions_remaining": None,
            "resets_at": self.reset().isoformat(),
        }
        if not self.enabled:
            return result
        with self.database.sessions() as db:
            bucket = db.get(QuotaBucket, self.key("owner:" + owner))
            used = (
                bucket.used
                if bucket and bucket.day == self.now().astimezone(UTC).date()
                else 0
            )
            result["sessions_remaining"] = max(
                0, self.settings.ai_sessions_per_day - used
            )
            if room_id:
                room = db.get(QuotaRoom, room_id)
                result["actions_remaining"] = max(
                    0,
                    self.settings.ai_actions_per_session
                    - (room.actions if room else 1),
                )
        return result
