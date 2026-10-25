"""Bounded, ephemeral checkpoints for the local single-worker foundation.

Each run has a private saver. Removing a run detaches its entire checkpoint
history, even if a revoked in-flight graph still holds a reference to that saver.
Restart loses all runs. Durable production storage belongs to a later milestone.
"""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from roometry.agents.state import AgentState, ClarificationAnswer
from roometry.progress import ProgressLog


class RunUnavailable(Exception):
    """Same response for unknown IDs, another owner, revoked input or expiry."""


class RunCapacityReached(Exception):
    pass


class RunBusy(Exception):
    pass


@dataclass
class CheckpointRun:
    owner: str
    initial: AgentState
    saver: InMemorySaver = field(default_factory=lambda: InMemorySaver(
        # Nodes write model_dump() dictionaries, dates and primitive values.
        # No arbitrary class reconstruction or pickle fallback is needed.
        serde=JsonPlusSerializer(allowed_msgpack_modules=[]),
    ))
    busy: bool = False
    cancel_requested: bool = False
    answer_in_flight: ClarificationAnswer | None = None
    request_id: str | None = None
    progress: ProgressLog = field(default_factory=ProgressLog)


class TemporaryCheckpoints:
    def __init__(self, max_runs: int = 128):
        if not 1 <= max_runs <= 1024:
            raise ValueError("Temporary run capacity must be between 1 and 1024.")
        self.max_runs = max_runs
        self._runs: dict[str, CheckpointRun] = {}

    def add(self, owner: str, state: AgentState, now: datetime) -> CheckpointRun:
        self.cleanup(now)
        if not owner.strip() or state.expires_at <= now:
            raise RunUnavailable()
        if len(self._runs) >= self.max_runs:
            raise RunCapacityReached()
        if state.run_id in self._runs:
            raise ValueError("Run IDs cannot be reused.")
        entry = CheckpointRun(owner=owner, initial=state.model_copy(deep=True))
        self._runs[state.run_id] = entry
        return entry

    def get(self, run_id: str, owner: str, now: datetime) -> CheckpointRun:
        entry = self._runs.get(run_id)
        if entry is None or entry.owner != owner:
            raise RunUnavailable()
        if entry.initial.expires_at <= now:
            self._runs.pop(run_id)
            raise RunUnavailable()
        return entry

    def remove(self, run_id: str) -> None:
        # Internal only; callers must authorize ownership before revocation.
        self._runs.pop(run_id, None)

    def cleanup(self, now: datetime) -> None:
        for run_id, entry in list(self._runs.items()):
            if entry.initial.expires_at <= now:
                self._runs.pop(run_id)


def new_run_id() -> str:
    return str(uuid4())
