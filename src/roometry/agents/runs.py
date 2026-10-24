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
from roometry.agents.nodes import RunContext, StepAdapter, AssessmentAdapter
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
        self.ttl_seconds = ttl_seconds
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
            limits=limits or self.default_limits,
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
            "recursion_limit": 4 * entry.initial.limits.max_steps + 4,
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
        return await self._drive(run_id, owner)

    async def read_request(self, request_id: str, owner: str) -> AgentState:
        """Read-only discovery while the original start request is in flight."""
        for entry in list(self.checkpoints._runs.values()):
            if entry.owner == owner and entry.request_id == request_id:
                return await self.read(entry.initial.run_id, owner)
        raise RunUnavailable()

    async def start(self, input_id: str, owner: str, request_id: str) -> AgentState:
        """Client-generated request IDs make retried starts safe as well."""
        initial: AgentState | None = None
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
                initial = await self.create(input_id, owner)
                entry = self.checkpoints.get(initial.run_id, owner, self.now())
                entry.request_id = request_id
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

        graph, config = build_graph(entry.saver), self._config(entry)
        budget = RunBudget(
            entry.initial.limits,
            entry.initial.usage,
            authorize,
            lambda: entry.cancel_requested,
            run_id=run_id,
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
            await graph.ainvoke(
                graph_input,
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

    async def cancel(self, run_id: str, owner: str) -> AgentState:
        entry = await self._owned(run_id, owner)
        entry.cancel_requested = True
        if entry.busy:
            return await self.read(run_id, owner)
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
        return await self.read(run_id, owner)

    async def cleanup(self) -> None:
        self.checkpoints.cleanup(self.now())
        for run_id, entry in list(self.checkpoints._runs.items()):
            try:
                await self._owned(run_id, entry.owner)
            except RunUnavailable:
                pass
