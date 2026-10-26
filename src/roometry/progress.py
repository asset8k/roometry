"""Bounded public progress only. Subscriptions can never execute graph/model work."""

from collections import deque
from collections.abc import Awaitable, Callable
from typing import Literal

import anyio
from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse
from sqlalchemy.exc import SQLAlchemyError

from roometry.uploads.schemas import OpaqueId

Stage = Literal["starting", "planning", "analyzing", "clarifying", "constructing", "checking", "reviewing", "assessing", "prepared", "proposing", "cancelling", "finished", "expired"]
Status = Literal["queued", "running", "awaiting_answer", "completed", "partial", "incomplete", "budget_exhausted", "cancelled", "expired", "failed", "prepared", "no_improvement"]


class ProgressEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, json_schema_serialization_defaults_required=True)
    version: Literal[1] = 1
    run_id: OpaqueId
    sequence: int = Field(ge=1)
    kind: Literal["reconstruction", "optimization"]
    stage: Stage
    status: Status
    step: int = Field(ge=0, le=1000)
    candidate: int | None = Field(default=None, ge=1, le=5)
    closed: bool

    @property
    def cursor(self) -> str:
        return f"{self.run_id}:{self.sequence}"


def stage_for(state) -> Stage:
    if state.status == "expired": return "expired"
    if state.status not in {"queued", "running", "awaiting_answer", "prepared"}: return "finished"
    if hasattr(state, "stage"):
        return {"validating": "checking"}.get(state.stage, state.stage)
    if state.status == "queued": return "starting"
    if state.status == "awaiting_answer": return "clarifying"
    if state.working_scene and not state.continue_run and not state.room_assessment: return "assessing"
    if state.working_scene and state.proposal and not state.proposal.visual_review and not state.pending_tool: return "reviewing"
    tool = state.pending_tool.name if state.pending_tool else None
    return {"analyze_photos": "analyzing", "propose_scene": "constructing", "request_clarification": "clarifying",
            "validate_scene": "checking", "score_layout": "checking", "move_object": "checking", "rotate_object": "checking"}.get(tool, "planning")


class ProgressLog:
    """Lives inside an owned expiring run; no scene, prompt, or provider text retained."""
    def __init__(self, capacity: int = 128):
        self.events: deque[ProgressEvent] = deque(maxlen=capacity)
        self.sequence = 0
        self.changed = anyio.Event()
        self.subscribers = 0

    def publish(self, state, *, cancelling=False, force=False) -> ProgressEvent:
        kind = "optimization" if hasattr(state, "source_run_id") else "reconstruction"
        closed = state.status not in {"queued", "running", "awaiting_answer", "prepared"}
        data = dict(run_id=state.run_id, kind=kind, stage="cancelling" if cancelling and not closed else stage_for(state),
                    status=state.status, step=len(state.attempts) if kind == "optimization" else state.steps_used,
                    candidate=min(len(state.attempts) + 1, state.max_candidates) if kind == "optimization" else None,
                    closed=closed)
        if not force and self.events and self.events[-1].model_dump(exclude={"sequence", "version"}) == data:
            return self.events[-1]
        self.sequence += 1
        event = ProgressEvent(sequence=self.sequence, **data)
        self.events.append(event)
        self.changed.set()
        self.changed = anyio.Event()
        return event

    def replay(self, cursor: str | None) -> list[ProgressEvent]:
        if not cursor or not self.events: return []
        prefix, _, number = cursor.rpartition(":")
        if prefix != self.events[-1].run_id or not number.isdigit(): return []
        sequence = int(number) if len(number) <= 10 else -1
        if sequence < self.events[0].sequence - 1 or sequence > self.sequence: return []
        return [event for event in self.events if event.sequence > sequence]


def encode(event: ProgressEvent, name="progress") -> str:
    return f"id: {event.cursor}\nevent: {name}\ndata: {event.model_dump_json()}\n\n"


def progress_response(request: Request, log: ProgressLog, read: Callable[[], Awaitable], *,
                      cursor: str | None = None, recheck_seconds=1., heartbeat_seconds=10., lifetime_seconds=120.) -> StreamingResponse:
    from roometry.agents.checkpointing import RunUnavailable

    # This check happens before response headers, not halfway through an SSE body.
    if log.subscribers >= 4:
        from roometry.agents.router import RunFailure
        raise RunFailure(429, "stream_capacity_reached", "Too many progress connections. Close another room tab and retry.")
    log.subscribers += 1
    released = False
    def release():
        nonlocal released
        if not released:
            log.subscribers -= 1
            released = True

    async def events():
        try:
            # Recheck before replay too: logout/deletion can race response creation.
            await read()
            yield "retry: 1500\n\n"
            # Replayed progress is followed by a snapshot signal: clients reconcile
            # via the existing authorized GET even if their last event ID is current.
            for event in log.replay(cursor): yield encode(event)
            latest = log.events[-1]
            yield encode(latest, "snapshot")
            delivered = latest.sequence
            started = heartbeat = anyio.current_time()
            while not latest.closed and anyio.current_time() - started < lifetime_seconds:
                wake = log.changed
                with anyio.move_on_after(recheck_seconds):
                    await wake.wait()
                if await request.is_disconnected(): return
                state = await read()  # Ownership/revocation/expiry checked throughout.
                # The graph publishes immediately; this also catches external cancellation.
                if not log.events or (state.status not in {"queued", "running", "prepared", "awaiting_answer"} and log.events[-1].status != state.status):
                    log.publish(state)
                for event in list(log.events):
                    if event.sequence > delivered:
                        yield encode(event)
                        delivered = event.sequence
                latest = log.events[-1]
                # A terminal transition may arrive while a slow client drains an
                # older batch. Deliver it before closing instead of losing the tail.
                if latest.closed and delivered < latest.sequence:
                    yield encode(latest, "snapshot")
                    delivered = latest.sequence
                if anyio.current_time() - heartbeat >= heartbeat_seconds:
                    yield 'event: heartbeat\ndata: {}\n\n'
                    heartbeat = anyio.current_time()
        except (RunUnavailable, SQLAlchemyError):
            yield 'event: unavailable\ndata: {"code":"run_unavailable"}\n\n'
        finally:
            release()
    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-store, no-transform", "X-Accel-Buffering": "no",
    }, background=BackgroundTask(release))
