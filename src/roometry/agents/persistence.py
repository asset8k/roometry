"""Registered-run ownership, durable input copies and bounded execution leases."""

import json
from contextlib import nullcontext
from datetime import UTC, timedelta
from uuid import UUID, uuid4

from pydantic import TypeAdapter
from sqlalchemy import delete, select

from roometry.agents.checkpointing import (
    CheckpointRun,
    RunBusy,
    RunUnavailable,
    TemporaryCheckpoints,
)
from roometry.agents.persistence_model import RuntimeInput, RuntimeRun
from roometry.agents.sql_checkpoints import SqlCheckpoints
from roometry.agents.state import AgentState
from roometry.uploads.errors import UploadFailure
from roometry.uploads.schemas import PreparedRoomInput
from roometry.vision.service import AnalysisState

PREFIX = "account:"


def user_id(owner):
    return UUID(owner[len(PREFIX) :]) if owner.startswith(PREFIX) else None


def aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class RegisteredRuns:
    def __init__(self, database, photos, guest, now=None, days=7):
        self.database, self.photos, self.guest, self.now, self.days = (
            database,
            photos,
            guest,
            now or (lambda: guest.now()),
            days,
        )
        self.instance = str(uuid4())
        self.analysis = None

    def saver(self, owner, run_id):
        return SqlCheckpoints(
            self.database,
            user_id(owner),
            run_id,
            lambda input_id: self.vision_dump(owner, input_id),
        )

    def vision_dump(self, owner, input_id):
        state = (
            self.analysis._states.get(self.analysis.cache_key(input_id, owner))
            if self.analysis
            else None
        )
        return (
            TypeAdapter(AnalysisState).dump_python(state, mode="json")
            if state
            else None
        )

    def restore_vision(self, owner, input_id):
        if self.analysis is None:
            return
        key = self.analysis.cache_key(input_id, owner)
        if key in self.analysis._states:
            return
        with self.database.sessions() as db:
            value = db.get(RuntimeInput, (user_id(owner), input_id))
            if value and value.vision:
                state = TypeAdapter(AnalysisState).validate_json(
                    json.dumps(value.vision)
                )
                if state.owner == owner:
                    self.analysis._states[key] = state

    def pin(self, input_id, guest_owner, account):
        with self.database.sessions() as db:
            existing = db.get(RuntimeInput, (account, input_id))
            if existing and aware(existing.expires_at) > self.now():
                return
        prepared = self.guest.resolve(input_id, guest_owner)
        batch, images = self.photos.write(
            [
                (image, self.guest.read_image(input_id, image.image_id, guest_owner))
                for image in prepared.images
            ]
        )
        try:
            expires = self.now() + timedelta(days=self.days)
            extended = prepared.model_copy(update={"expires_at": expires})
            with self.database.sessions.begin() as db:
                previous = db.get(RuntimeInput, (account, input_id))
                if previous:
                    return
                db.add(
                    RuntimeInput(
                        user_id=account,
                        id=input_id,
                        prepared=extended.model_dump(mode="json"),
                        images=[{**image, "id": str(image["id"])} for image in images],
                        vision=None,
                        expires_at=expires,
                    )
                )
            batch = None
        finally:
            if batch:
                with self.database.sessions() as db:
                    saved = db.get(RuntimeInput, (account, input_id))
                    referenced = saved is not None and any(
                        i["storage_key"].startswith(batch + "/") for i in saved.images
                    )
                if not referenced:
                    self.photos.discard(batch)

    def resolve(self, input_id, owner):
        account = user_id(owner)
        if account is None:
            return self.guest.resolve(input_id, owner)
        with self.database.sessions() as db:
            stored = db.get(RuntimeInput, (account, input_id))
            if stored is None or aware(stored.expires_at) <= self.now():
                raise UploadFailure(
                    404,
                    "input_not_found",
                    "This registered run has expired or is unavailable.",
                )
            return PreparedRoomInput.model_validate_json(json.dumps(stored.prepared))

    def read_image(self, input_id, image_id, owner):
        account = user_id(owner)
        if account is None:
            return self.guest.read_image(input_id, image_id, owner)
        self.resolve(input_id, owner)
        with self.database.sessions() as db:
            stored = db.get(RuntimeInput, (account, input_id))
            image = next(
                (i for i in stored.images if i["source_image_id"] == image_id), None
            )
            if image is None:
                raise UploadFailure(
                    404, "input_not_found", "This photo is unavailable."
                )
            return self.photos.read(image["storage_key"], image["sha256"])

    def owned(self, run_id, account):
        if self.database is None:
            return False
        with self.database.sessions() as db:
            return db.get(RuntimeRun, (account, run_id)) is not None

    def find(self, owner, request_id, kind="reconstruction"):
        with self.database.sessions() as db:
            record = db.scalar(
                select(RuntimeRun).where(
                    RuntimeRun.user_id == user_id(owner),
                    RuntimeRun.request_id == request_id,
                    RuntimeRun.kind == kind,
                )
            )
            return record.id if record else None

    def put(
        self,
        owner,
        run_id,
        input_id,
        data,
        kind="reconstruction",
        request_id=None,
        project_id=None,
        *,
        transaction=None,
    ):
        with (
            nullcontext(transaction)
            if transaction is not None
            else self.database.sessions.begin() as db
        ):
            record = db.get(RuntimeRun, (user_id(owner), run_id))
            if record is not None:
                return
            prepared = db.get(RuntimeInput, (user_id(owner), input_id))
            if prepared is None:
                raise RunUnavailable()
            state = data if kind == "reconstruction" else data["result"]
            if kind == "optimization" and project_id is None:
                source = db.get(RuntimeRun, (user_id(owner), state["source_run_id"]))
                if source is None:
                    raise RunUnavailable()
                project_id = source.project_id
            db.add(
                RuntimeRun(
                    user_id=user_id(owner),
                    id=run_id,
                    input_id=input_id,
                    kind=kind,
                    project_id=project_id or prepared.project_id,
                    request_id=request_id,
                    data=data,
                    status=state["status"],
                    usage=state["usage"],
                    expires_at=prepared.expires_at,
                    updated_at=self.now(),
                )
            )

    def request(self, owner, run_id, request_id):
        with self.database.sessions.begin() as db:
            record = db.get(RuntimeRun, (user_id(owner), run_id))
            if record is None:
                raise RunUnavailable()
            record.request_id = request_id

    def load(self, owner, run_id):
        with self.database.sessions() as db:
            record = db.get(RuntimeRun, (user_id(owner), run_id))
            if record is None or aware(record.expires_at) <= self.now():
                raise RunUnavailable()
            return {
                key: getattr(record, key)
                for key in (
                    "data",
                    "status",
                    "cancel_requested",
                    "execution_owner",
                    "lease_until",
                    "usage",
                    "kind",
                    "input_id",
                    "project_id",
                    "request_id",
                )
            }

    def update(self, owner, run_id, data):
        with self.database.sessions.begin() as db:
            record = db.get(RuntimeRun, (user_id(owner), run_id))
            if record is None:
                raise RunUnavailable()
            record.data = data
            state = data if record.kind == "reconstruction" else data["result"]
            record.status = state["status"]
            record.updated_at = self.now()

    def cancel(self, owner, run_id):
        with self.database.sessions.begin() as db:
            record = db.get(RuntimeRun, (user_id(owner), run_id))
            if record is None:
                raise RunUnavailable()
            record.cancel_requested = True

    def related(self, owner, source_id):
        with self.database.sessions() as db:
            return [
                r.id
                for r in db.scalars(
                    select(RuntimeRun).where(
                        RuntimeRun.user_id == user_id(owner),
                        RuntimeRun.kind == "optimization",
                        RuntimeRun.expires_at > self.now(),
                    )
                )
                if r.data["result"]["source_run_id"] == source_id
            ]

    def claim(self, owner, run_id, seconds):
        with self.database.sessions.begin() as db:
            record = db.scalar(
                select(RuntimeRun)
                .where(RuntimeRun.user_id == user_id(owner), RuntimeRun.id == run_id)
                .with_for_update()
            )
            if record is None:
                raise RunUnavailable()
            if record.execution_owner is not None:
                raise RunBusy()
            record.execution_owner = self.instance
            record.lease_until = self.now() + timedelta(seconds=seconds + 30)

    def release(self, owner, run_id, data=None, recovery=False):
        with self.database.sessions.begin() as db:
            record = db.get(RuntimeRun, (user_id(owner), run_id))
            if record is None:
                raise RunUnavailable()
            if not recovery and record.execution_owner not in (None, self.instance):
                raise RunBusy()
            record.execution_owner = None
            record.lease_until = None
            if data:
                record.data = data
                state = data if record.kind == "reconstruction" else data["result"]
                record.status = state["status"]
                record.usage = state["usage"]
            record.updated_at = self.now()

    def reserve(self, owner, run_id, usage, seconds):
        with self.database.sessions.begin() as db:
            record = db.get(RuntimeRun, (user_id(owner), run_id))
            if record is None:
                raise RunUnavailable()
            if record.execution_owner != self.instance:
                raise RunBusy()
            record.usage = usage.model_dump()
            record.lease_until = self.now() + timedelta(seconds=seconds + 30)

    def remove(self, owner, run_id):
        with self.database.sessions.begin() as db:
            db.execute(
                delete(RuntimeRun).where(
                    RuntimeRun.user_id == user_id(owner), RuntimeRun.id == run_id
                )
            )

    def cleanup(self):
        if self.database is None:
            return
        from roometry.projects.model import ProjectImage

        with self.database.sessions.begin() as db:
            expired = list(
                db.scalars(
                    select(RuntimeInput).where(RuntimeInput.expires_at <= self.now())
                )
            )
            batches = {
                i["storage_key"].split("/")[0]
                for stored in expired
                for i in stored.images
            }
            for stored in expired:
                db.delete(stored)
        for batch in batches:
            with self.database.sessions() as db:
                saved = db.scalar(
                    select(ProjectImage.id)
                    .where(ProjectImage.storage_key.startswith(batch + "/"))
                    .limit(1)
                )
                current = any(
                    any(i["storage_key"].startswith(batch + "/") for i in row.images)
                    for row in db.scalars(select(RuntimeInput))
                )
            if saved is None and not current:
                self.photos.discard(batch)

    def recent(self, account):
        with self.database.sessions() as db:
            rows = list(
                db.scalars(
                    select(RuntimeRun)
                    .where(
                        RuntimeRun.user_id == account,
                        RuntimeRun.kind == "reconstruction",
                        RuntimeRun.status.in_(("queued", "running", "awaiting_answer", "completed", "partial")),
                        RuntimeRun.project_id.is_(None),
                        RuntimeRun.request_id.is_not(None),
                        RuntimeRun.expires_at > self.now(),
                    )
                    .order_by(RuntimeRun.updated_at.desc())
                    .limit(50)
                )
            )
            return [
                {
                    "id": r.id,
                    "status": r.status,
                    "room_type": r.data["input_options"].get("room_type") or "room",
                    "expires_at": aware(r.expires_at).isoformat(),
                }
                for r in rows
            ]


class WorkspaceInputs:
    def __init__(self, registered):
        self.registered = registered

    def now(self):
        return self.registered.now()

    def resolve(self, input_id, owner):
        return self.registered.resolve(input_id, owner)

    def read_image(self, input_id, image_id, owner):
        return self.registered.read_image(input_id, image_id, owner)


class DurableCheckpoints(TemporaryCheckpoints):
    def __init__(self, registered):
        super().__init__()
        self.registered = registered

    def add(self, owner, state, now):
        if user_id(owner) is None:
            return super().add(owner, state, now)
        self.cleanup(now)
        key = f"{owner}:{state.run_id}"
        self.registered.put(
            owner, state.run_id, state.input_id, state.model_dump(mode="json")
        )
        entry = CheckpointRun(
            owner,
            state.model_copy(deep=True),
            saver=self.registered.saver(owner, state.run_id),
        )
        self._runs[key] = entry
        return entry

    def get(self, run_id, owner, now):
        if user_id(owner) is None:
            return super().get(run_id, owner, now)
        record = self.registered.load(owner, run_id)
        if record["kind"] != "reconstruction":
            raise RunUnavailable()
        key = f"{owner}:{run_id}"
        if key not in self._runs:
            state = AgentState.model_validate_json(json.dumps(record["data"]))
            self._runs[key] = CheckpointRun(
                owner,
                state,
                saver=self.registered.saver(owner, run_id),
                request_id=None,
            )
            self._runs[key].recovery = record["execution_owner"] is not None
            self.registered.restore_vision(owner, state.input_id)
        if record["execution_owner"] is None:
            self._runs[key].recovery = False
        elif not self._runs[key].busy:
            self._runs[key].recovery = True
        self._runs[key].cancel_requested = record["cancel_requested"]
        return self._runs[key]

    def remove(self, run_id, owner=None):
        if owner and user_id(owner):
            self._runs.pop(f"{owner}:{run_id}", None)
            self.registered.remove(owner, run_id)
        else:
            super().remove(run_id)


def seed_project(registered, account, detail):
    """Rehydrate a saved reconstruction and retained candidates; never dispatch a model."""
    from roometry.auth.errors import AuthFailure
    from roometry.projects.model import Project

    # Serialize open/reseed with save/delete and publish all runtime references
    # atomically. Two tabs must never delete each other's newly hydrated run.
    with registered.database.sessions.begin() as db:
        project = db.scalar(
            select(Project)
            .where(Project.id == UUID(detail["id"]), Project.user_id == account)
            .with_for_update()
        )
        if project is None:
            raise AuthFailure(
                404, "project_not_found", "This saved room is unavailable."
            )
        if project.revision != detail["revision"]:
            raise AuthFailure(
                409,
                "project_changed",
                "This room has changed. Reopen the latest saved room.",
            )
        return _seed_project(registered, account, detail, db)


def _seed_project(registered, account, detail, db):
    from roometry.optimization.schemas import StartOptimizationRequest
    from roometry.projects.model import ProjectImage

    project_id = UUID(detail["id"])
    source = detail["snapshot"]["source"]
    owner = f"account:{account}"
    now = registered.now()
    expires = now + timedelta(days=registered.days)
    with nullcontext(db):
        existing = db.get(RuntimeRun, (account, source["run_id"]))
        if existing and aware(existing.expires_at) > now:
            return AgentState.model_validate_json(json.dumps(existing.data)), False
        images = list(
            db.scalars(
                select(ProjectImage)
                .where(ProjectImage.project_id == project_id)
                .order_by(ProjectImage.sort_order)
            )
        )
    input_id = str(uuid4())
    metadata = [
        {
            "image_id": i.source_image_id,
            "original_media_type": "image/jpeg",
            "original_bytes": i.byte_count,
            "original_width_px": i.width,
            "original_height_px": i.height,
            "prepared_media_type": "image/jpeg",
            "prepared_bytes": i.byte_count,
            "prepared_width_px": i.width,
            "prepared_height_px": i.height,
        }
        for i in images
    ]
    prepared = {
        "input_id": input_id,
        "status": "ready",
        "options": source["input_options"],
        "images": metadata,
        "expires_at": expires.isoformat(),
    }
    PreparedRoomInput.model_validate_json(json.dumps(prepared))
    data = json.loads(json.dumps(source))
    data.update(
        input_id=input_id,
        persistence="account",
        expires_at=expires.isoformat(),
        input_expires_at=expires.isoformat(),
        continue_run=False,
    )
    if data.get("analysis"):
        data["analysis"].update(input_id=input_id, expires_at=expires.isoformat())
    if data.get("proposal"):
        data["proposal"]["input_id"] = input_id
    state = AgentState.model_validate_json(json.dumps(data))
    with nullcontext(db):
        db.execute(
            delete(RuntimeRun).where(
                RuntimeRun.user_id == account, RuntimeRun.project_id == project_id
            )
        )
        db.flush()
        db.execute(
            delete(RuntimeInput).where(
                RuntimeInput.user_id == account, RuntimeInput.project_id == project_id
            )
        )
        db.add(
            RuntimeInput(
                user_id=account,
                id=input_id,
                project_id=project_id,
                prepared=prepared,
                images=[
                    {
                        "source_image_id": i.source_image_id,
                        "storage_key": i.storage_key,
                        "sha256": i.sha256,
                    }
                    for i in images
                ],
                vision=None,
                expires_at=expires,
            )
        )
    registered.put(
        owner,
        state.run_id,
        input_id,
        state.model_dump(mode="json"),
        project_id=project_id,
        transaction=db,
    )
    for candidate_id, value in detail["snapshot"]["improvements"].items():
        value = json.loads(json.dumps(value))
        value.update(input_id=input_id, expires_at=expires.isoformat())
        request = StartOptimizationRequest(
            request_id=str(uuid4()),
            focus=value["focus"],
            preferences=value["preferences"],
            instruction=value.get("instruction"),
            context={
                "scene": value["source_scene"],
                "parent_run_id": value.get("parent_run_id"),
            }
            if value.get("instruction")
            else None,
        )
        registered.put(
            owner,
            candidate_id,
            input_id,
            {
                "result": value,
                "source": state.model_dump(mode="json"),
                "request": request.model_dump(mode="json"),
                "execution_started": True,
                "finished": True,
            },
            "optimization",
            request.request_id,
            project_id,
            transaction=db,
        )
    return state, True
