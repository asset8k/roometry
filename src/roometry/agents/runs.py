"""Owned temporary runs, idempotent answers, and real interrupt resumption."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import anyio
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from roometry.agents.budget import RunBudget
from roometry.agents.checkpointing import (
    CheckpointRun,
    RunBusy,
    RunUnavailable,
    TemporaryCheckpoints,
    new_run_id,
)
from roometry.agents.clarification import AnswerConflict, validate_answer
from roometry.agents.graph import build_graph
from roometry.agents.nodes import AssessmentAdapter, RunContext, StepAdapter
from roometry.agents.state import AgentState, ClarificationAnswer, RunLimits
from roometry.uploads.errors import UploadFailure
from roometry.uploads.schemas import PreparedRoomInput


class AgentRuns:
    def __init__(
        self,
        resolve_input: Callable[[str, str], PreparedRoomInput],
        advance: StepAdapter,
        *,
        execute_tool: StepAdapter | None = None,
        review_scene: StepAdapter | None = None,
        assess_room: AssessmentAdapter | None = None,
        default_limits: RunLimits | None = None,
        checkpoints: TemporaryCheckpoints | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        ttl_seconds: int = 3600,
        quotas=None,
    ):
        if not 1 <= ttl_seconds <= 3600:
            raise ValueError("Run TTL must be between 1 second and 1 hour.")
        self.resolve_input, self.advance, self.now = resolve_input, advance, now
        self.execute_tool = execute_tool
        self.review_scene = review_scene
        self.assess_room = assess_room
        self.default_limits = default_limits or RunLimits()
        self.checkpoints = (
            checkpoints if checkpoints is not None else TemporaryCheckpoints()
        )
        self.durable = getattr(self.checkpoints, "registered", None)
        self.ttl_seconds = ttl_seconds
        self.quotas = quotas
        self._start_lock = anyio.Lock()

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
        expires_at = prepared.expires_at if owner.startswith("account:") else min(prepared.expires_at, now + timedelta(seconds=self.ttl_seconds))
        if expires_at <= now:
            raise RunUnavailable()
        state = AgentState(
            persistence="account" if owner.startswith("account:") else "temporary",
            run_id=new_run_id(),
            input_id=input_id,
            image_ids=[image.image_id for image in prepared.images],
            input_options=prepared.options.model_copy(deep=True),
            created_at=now,
            input_expires_at=prepared.expires_at,
            expires_at=expires_at,
            limits=limits or self.default_limits,
            confirmed_dimensions=dimensions.model_copy(deep=True)
            if dimensions is not None
            and dimensions.measurements_source == "user_confirmed"
            else None,
        )
        self.checkpoints.add(owner, state, now).progress.publish(state)
        return state.model_copy(deep=True)

    async def _owned(self, run_id: str, owner: str) -> CheckpointRun:
        entry = self.checkpoints.get(run_id, owner, self.now())
        try:
            prepared = await self._input(entry.initial.input_id, owner)
        except RunUnavailable:
            self.checkpoints.remove(run_id, owner)
            raise
        # Recheck after I/O; deletion/expiry may have happened while resolving.
        current = self.checkpoints.get(run_id, owner, self.now())
        if (
            prepared.expires_at != current.initial.input_expires_at
            or prepared.options != current.initial.input_options
            or [image.image_id for image in prepared.images]
            != current.initial.image_ids
        ):
            self.checkpoints.remove(run_id, owner)
            raise RunUnavailable()
        return current

    @staticmethod
    def _config(entry: CheckpointRun) -> RunnableConfig:
        return {
            "configurable": {"thread_id": entry.initial.run_id},
            "recursion_limit": 4 * entry.initial.limits.max_steps + 4,
        }

    async def read(self, run_id: str, owner: str) -> AgentState:
        entry = await self._owned(run_id, owner)
        graph = build_graph(entry.saver)
        snapshot = await graph.aget_state(self._config(entry))
        result = (
            AgentState.model_validate(snapshot.values)
            if snapshot.values
            else entry.initial
        )
        if entry.recovery and self.durable:
            record = await anyio.to_thread.run_sync(self.durable.load, owner, run_id)
            stable = bool(snapshot.interrupts) or result.status not in {"queued", "running", "awaiting_answer"}
            expired_lease = record['lease_until'] is None or record['lease_until'].replace(tzinfo=UTC) <= self.now()
            if stable and expired_lease:
                await anyio.to_thread.run_sync(lambda:self.durable.release(owner,run_id,recovery=True))
                entry.recovery=False
            elif expired_lease:
                status = result.proposal.status if result.working_scene and result.proposal else 'failed'
                status = 'cancelled' if record['cancel_requested'] else 'completed' if status == 'ready' else 'partial' if status == 'partial' else 'failed'
                await graph.aupdate_state(self._config(entry), {"status":status,"continue_run":False,"pending_tool":None,
                    "pending_question":None,"last_error":"step_failed","usage":record['usage']}, as_node="finalize")
                await anyio.to_thread.run_sync(lambda:self.durable.release(owner,run_id,recovery=True))
                entry.recovery=False
                return await self.read(run_id,owner)
        await self._owned(run_id, owner)
        return result.model_copy(deep=True)

    async def observe(self, run_id: str, owner: str):
        entry = await self._owned(run_id, owner)
        state = await self.read(run_id, owner)
        if not entry.progress.events: entry.progress.publish(state)
        return state, entry.progress

    async def execute(self, run_id: str, owner: str) -> AgentState:
        return await self._drive(run_id, owner)

    async def read_request(self, request_id: str, owner: str) -> AgentState:
        """Read-only discovery while the original start request is in flight."""
        if self.durable and owner.startswith('account:'):
            found=await anyio.to_thread.run_sync(self.durable.find,owner,request_id)
            if found:return await self.read(found,owner)
        for entry in list(self.checkpoints._runs.values()):
            if entry.owner == owner and entry.request_id == request_id:
                return await self.read(entry.initial.run_id, owner)
        raise RunUnavailable()

    async def start(self, input_id: str, owner: str, request_id: str) -> AgentState:
        """Client-generated request IDs make retried starts safe as well."""
        initial: AgentState | None = None
        if self.durable and owner.startswith('account:'):
            found=await anyio.to_thread.run_sync(self.durable.find,owner,request_id)
            if found:
                existing=await self.read(found,owner)
                if existing.input_id!=input_id:raise AnswerConflict('This request identifies another input.')
                metadata=await anyio.to_thread.run_sync(self.durable.load,owner,found)
                if existing.status=='queued' and metadata['execution_owner'] is None:return await self.execute(found,owner)
                return existing
        async with self._start_lock:
            self.checkpoints.cleanup(self.now())
            for entry in self.checkpoints._runs.values():
                if entry.owner == owner and entry.request_id == request_id:
                    if entry.initial.input_id != input_id:
                        raise AnswerConflict(
                            "This request ID already identifies another input."
                        )
                    existing_id = entry.initial.run_id
                    break
            else:
                if self.quotas:
                    await anyio.to_thread.run_sync(self.quotas.check_new, owner)
                initial = await self.create(input_id, owner)
                entry = self.checkpoints.get(initial.run_id, owner, self.now())
                entry.request_id = request_id
                if self.durable and owner.startswith('account:'):
                    await anyio.to_thread.run_sync(self.durable.request,owner,initial.run_id,request_id)
                existing_id = None
        if existing_id is not None:
            return await self.read(existing_id, owner)
        assert initial is not None
        try:
            return await self.execute(initial.run_id, owner)
        except anyio.get_cancelled_exc_class():
            with anyio.CancelScope(shield=True):
                try:
                    await self.cancel(initial.run_id, owner)
                except RunUnavailable:
                    pass
            raise

    async def answer(
        self, run_id: str, owner: str, answer: ClarificationAnswer
    ) -> AgentState:
        return await self._drive(
            run_id, owner, ClarificationAnswer.model_validate(answer)
        )

    @staticmethod
    def _duplicate(state: AgentState, answer: ClarificationAnswer) -> bool:
        previous = state.submitted_answers.get(answer.question_id)
        if previous is not None and previous != answer:
            raise AnswerConflict("This question already has a different answer.")
        return previous is not None

    async def _drive(
        self, run_id: str, owner: str, answer: ClarificationAnswer | None = None
    ) -> AgentState:
        entry = await self._owned(run_id, owner)
        if entry.busy:
            if answer is not None:
                current = await self.read(run_id, owner)
                if self._duplicate(current, answer) or entry.answer_in_flight == answer:
                    return current
                if (
                    entry.answer_in_flight is not None
                    and entry.answer_in_flight.question_id == answer.question_id
                ):
                    raise AnswerConflict(
                        "A different answer is already being processed."
                    )
            raise RunBusy()

        async def authorize() -> None:
            await self._owned(run_id, owner)

        if self.durable and owner.startswith('account:'):
            await self.read(run_id,owner)
            await anyio.to_thread.run_sync(self.durable.claim,owner,run_id,entry.initial.limits.step_timeout_seconds)
        async def persist_usage(usage):
            if self.quotas:
                await anyio.to_thread.run_sync(self.quotas.started, run_id)
            if self.durable and owner.startswith('account:'):
                await anyio.to_thread.run_sync(self.durable.reserve,owner,run_id,usage,entry.initial.limits.step_timeout_seconds)
        graph, config = build_graph(entry.saver), self._config(entry)
        budget = RunBudget(
            entry.initial.limits,
            entry.initial.usage,
            authorize,
            lambda: entry.cancel_requested,
            run_id=run_id,
            persist_usage=persist_usage,
        )
        entry.busy = True
        try:
            snapshot = await graph.aget_state(config)
            current = (
                AgentState.model_validate(snapshot.values)
                if snapshot.values
                else entry.initial
            )
            if answer is not None and self._duplicate(current, answer):
                return await self.read(run_id, owner)
            if answer is not None:
                if (
                    entry.cancel_requested
                    or current.status != "awaiting_answer"
                    or not snapshot.interrupts
                ):
                    raise AnswerConflict("This run is not waiting for an answer.")
                validate_answer(current, answer)
            elif snapshot.interrupts or (snapshot.values and not snapshot.next):
                return await self.read(run_id, owner)
            if self.quotas:
                await anyio.to_thread.run_sync(lambda: self.quotas.reserve(owner, run_id, run_id, initial=True))
            budget.sync(current.usage)
            context = RunContext(
                self.advance,
                authorize,
                lambda: entry.cancel_requested,
                self.now,
                owner=owner,
                budget=budget,
                execute_tool=self.execute_tool,
                review_scene=self.review_scene,
                assess_room=self.assess_room,
            )
            entry.answer_in_flight = answer
            graph_input = (
                Command(resume={snapshot.interrupts[0].id: answer.model_dump()})
                if answer is not None
                else None
                if snapshot.values
                else entry.initial.model_dump()
            )
            # Only this original mutation drives the graph. SSE merely observes
            # bounded public events; disconnecting a subscriber cannot replay work.
            async for values in graph.astream(graph_input, config, context=context, stream_mode="values", durability="sync"):
                if "run_id" in values:
                    state = AgentState.model_validate({k: v for k, v in values.items() if k != "__interrupt__"})
                    if state.status in {"queued", "running", "awaiting_answer"}:
                        entry.progress.publish(state, cancelling=entry.cancel_requested)
            current = await self.read(run_id, owner)
            entry.progress.publish(current, force=True)  # Checkpoint is now authoritative.
            return current
        except anyio.get_cancelled_exc_class():
            # A cancelled execution may have an ambiguous in-flight side effect.
            # Do not silently replay that task on the next execute() call.
            entry.cancel_requested = True
            with anyio.CancelScope(shield=True):
                try:
                    await self._owned(run_id, owner)
                    snapshot = await graph.aget_state(config)
                    if snapshot.values and snapshot.next:
                        # Cancellation can interrupt the first checkpoint read,
                        # before this invocation has synchronized its ledger.
                        budget.sync(AgentState.model_validate(snapshot.values).usage)
                        await graph.aupdate_state(
                            config,
                            {
                                "status": "cancelled",
                                "usage": budget.usage.model_dump(),
                                "pending_question": None,
                                "pending_tool": None,
                                "continue_run": False,
                            },
                            as_node="finalize",
                        )
                except RunUnavailable:
                    pass
            raise
        finally:
            entry.answer_in_flight = None
            entry.busy = False
            if self.quotas:
                with anyio.CancelScope(shield=True):
                    await anyio.to_thread.run_sync(self.quotas.release_unused, run_id)
            if self.durable and owner.startswith('account:'):
                with anyio.CancelScope(shield=True):
                    await anyio.to_thread.run_sync(self.durable.release,owner,run_id)

    async def cancel(self, run_id: str, owner: str) -> AgentState:
        entry = await self._owned(run_id, owner)
        entry.cancel_requested = True
        if self.durable and owner.startswith('account:'):
            await anyio.to_thread.run_sync(self.durable.cancel,owner,run_id)
            record=await anyio.to_thread.run_sync(self.durable.load,owner,run_id)
            if record['execution_owner'] not in (None,self.durable.instance):return await self.read(run_id,owner)
        if entry.busy:
            current = await self.read(run_id, owner)
            entry.progress.publish(current, cancelling=True)
            return current
        graph, config = build_graph(entry.saver), self._config(entry)
        snapshot = await graph.aget_state(config)
        current = (
            AgentState.model_validate(snapshot.values)
            if snapshot.values
            else entry.initial
        )
        if current.status in {
            "completed",
            "partial",
            "incomplete",
            "failed",
            "budget_exhausted",
            "cancelled",
            "expired",
        }:
            return await self.read(run_id, owner)
        await graph.aupdate_state(
            config,
            {
                **current.model_dump(),
                "status": "cancelled",
                "continue_run": False,
                "pending_question": None,
                "pending_tool": None,
                "events": [{"step": current.steps_used, "status": "cancelled"}],
            },
            as_node="finalize",
        )
        current = await self.read(run_id, owner)
        entry.progress.publish(current, force=True)
        return current

    async def cleanup(self) -> None:
        self.checkpoints.cleanup(self.now())
        for entry in list(self.checkpoints._runs.values()):
            try:
                await self._owned(entry.initial.run_id, entry.owner)
            except RunUnavailable:
                pass
