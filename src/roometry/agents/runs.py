"""Internal owned-run lifecycle, ready for later API/model integration.

No routes or default fake agent are exposed. Callers must inject a step adapter
and the existing private upload resolver; the frontend remains unchanged.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import anyio
from langchain_core.runnables import RunnableConfig

from roometry.agents.checkpointing import (
    CheckpointRun,
    RunBusy,
    RunUnavailable,
    TemporaryCheckpoints,
    new_run_id,
)
from roometry.agents.graph import build_graph
from roometry.agents.nodes import RunContext, StepAdapter
from roometry.agents.state import AgentState, RunLimits
from roometry.uploads.errors import UploadFailure
from roometry.uploads.schemas import PreparedRoomInput


class AgentRuns:
    def __init__(
        self,
        resolve_input: Callable[[str, str], PreparedRoomInput],
        advance: StepAdapter,
        *,
        checkpoints: TemporaryCheckpoints | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        ttl_seconds: int = 3600,
    ):
        if not 1 <= ttl_seconds <= 3600:
            raise ValueError("Run TTL must be between 1 second and 1 hour.")
        self.resolve_input, self.advance, self.now = resolve_input, advance, now
        self.checkpoints = (
            checkpoints if checkpoints is not None else TemporaryCheckpoints()
        )
        self.ttl_seconds = ttl_seconds

    async def _input(self, input_id: str, owner: str) -> PreparedRoomInput:
        try:
            prepared = await anyio.to_thread.run_sync(
                self.resolve_input, input_id, owner
            )
        except UploadFailure:
            raise RunUnavailable() from None
        if prepared.input_id != input_id or prepared.expires_at <= self.now():
            raise RunUnavailable()
        return prepared

    async def create(
        self, input_id: str, owner: str, *, limits: RunLimits | None = None
    ) -> AgentState:
        prepared = await self._input(input_id, owner)
        now = self.now()
        dimensions = prepared.options.dimensions
        expires_at = min(prepared.expires_at, now + timedelta(seconds=self.ttl_seconds))
        if expires_at <= now:
            raise RunUnavailable()
        state = AgentState(
            run_id=new_run_id(),
            input_id=input_id,
            image_ids=[image.image_id for image in prepared.images],
            input_options=prepared.options.model_copy(deep=True),
            created_at=now,
            input_expires_at=prepared.expires_at,
            expires_at=expires_at,
            limits=limits or RunLimits(),
            confirmed_dimensions=dimensions.model_copy(deep=True)
            if dimensions is not None
            and dimensions.measurements_source == "user_confirmed"
            else None,
        )
        self.checkpoints.add(owner, state, now)
        return state.model_copy(deep=True)

    async def _owned(self, run_id: str, owner: str) -> CheckpointRun:
        entry = self.checkpoints.get(run_id, owner, self.now())
        try:
            prepared = await self._input(entry.initial.input_id, owner)
        except RunUnavailable:
            self.checkpoints.remove(run_id)
            raise
        # Recheck after I/O; deletion/expiry may have happened while resolving.
        current = self.checkpoints.get(run_id, owner, self.now())
        if (
            prepared.expires_at != current.initial.input_expires_at
            or prepared.options != current.initial.input_options
            or [image.image_id for image in prepared.images]
            != current.initial.image_ids
        ):
            self.checkpoints.remove(run_id)
            raise RunUnavailable()
        return current

    @staticmethod
    def _config(entry: CheckpointRun) -> RunnableConfig:
        return {
            "configurable": {"thread_id": entry.initial.run_id},
            "recursion_limit": 2 * entry.initial.limits.max_steps + 4,
        }

    async def read(self, run_id: str, owner: str) -> AgentState:
        entry = await self._owned(run_id, owner)
        snapshot = await build_graph(entry.saver).aget_state(self._config(entry))
        result = (
            AgentState.model_validate(snapshot.values)
            if snapshot.values
            else entry.initial
        )
        await self._owned(run_id, owner)
        return result.model_copy(deep=True)

    async def execute(self, run_id: str, owner: str) -> AgentState:
        entry = await self._owned(run_id, owner)
        if entry.busy:
            raise RunBusy()
        entry.busy = True
        try:
            graph, config = build_graph(entry.saver), self._config(entry)
            snapshot = await graph.aget_state(config)
            if snapshot.values and not snapshot.next:
                return await self.read(run_id, owner)

            async def authorize() -> None:
                await self._owned(run_id, owner)

            context = RunContext(
                self.advance, authorize, lambda: entry.cancel_requested, self.now
            )
            await graph.ainvoke(
                None if snapshot.values else entry.initial.model_dump(),
                config,
                context=context,
            )
            return await self.read(run_id, owner)
        except anyio.get_cancelled_exc_class():
            # A cancelled execution may have an ambiguous in-flight side effect.
            # Do not silently replay that task on the next execute() call.
            entry.cancel_requested = True
            with anyio.CancelScope(shield=True):
                try:
                    await self._owned(run_id, owner)
                    snapshot = await graph.aget_state(config)
                    if snapshot.values and snapshot.next:
                        await graph.aupdate_state(
                            config, {"status": "cancelled"}, as_node="finalize"
                        )
                except RunUnavailable:
                    pass
            raise
        finally:
            entry.busy = False

    async def cancel(self, run_id: str, owner: str) -> AgentState:
        entry = await self._owned(run_id, owner)
        entry.cancel_requested = True
        if entry.busy:
            return await self.read(run_id, owner)
        return await self.execute(run_id, owner)

    async def cleanup(self) -> None:
        self.checkpoints.cleanup(self.now())
        for run_id, entry in list(self.checkpoints._runs.items()):
            try:
                await self._owned(run_id, entry.owner)
            except RunUnavailable:
                pass
