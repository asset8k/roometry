"""Private improvement requests with one-shot, bounded graph execution."""

from dataclasses import dataclass, field
import logging

import anyio
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from roometry.agents.checkpointing import RunCapacityReached, RunUnavailable, new_run_id
from roometry.agents.runs import AgentRuns
from roometry.agents.state import AgentState
from roometry.agents.state import RunLimits
from roometry.agents.budget import RunBudget
from roometry.optimization.graph import OptimizationContext, build_graph
from roometry.optimization.schemas import OptimizationRun, StartOptimizationRequest
from roometry.mcp.catalog import CatalogSearch
from roometry.mcp.furniture_client import CatalogFailure
from roometry.vision.errors import AnalysisFailure
from roometry.scenes.hashing import scene_hash
from roometry.scenes.validation import validate_geometry
from roometry.progress import ProgressLog
from roometry.optimization.context import InvalidSceneContext, refinement_message, validate_context


class OptimizationConflict(Exception):
    pass


class ReconstructionRequired(Exception):
    pass


@dataclass
class ImprovementEntry:
    owner: str
    request: StartOptimizationRequest
    source: AgentState
    result: OptimizationRun
    saver: InMemorySaver = field(default_factory=lambda: InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=[])))
    busy: bool = False
    execution_started: bool = False
    finished: bool = False
    cancel_requested: bool = False
    budget: RunBudget | None = None
    progress: ProgressLog = field(default_factory=ProgressLog)


class OptimizationRuns:
    def __init__(self, reconstructions: AgentRuns, *, max_runs: int = 128, provider=None, catalog=None,
                 max_candidates=3, limits=None):
        if not 1 <= max_runs <= 1024:
            raise ValueError("Temporary improvement capacity must be between 1 and 1024.")
        self.reconstructions, self.max_runs = reconstructions, max_runs
        self.provider, self.catalog = provider, catalog
        if not 1 <= max_candidates <= 5: raise ValueError("Candidate limit must be between 1 and 5.")
        self.max_candidates, self.limits = max_candidates, limits or RunLimits(max_model_calls=6)
        self._runs: dict[str, ImprovementEntry] = {}
        self._lock = anyio.Lock()

    async def _owned(self, run_id: str, owner: str) -> ImprovementEntry:
        entry = self._runs.get(run_id)
        if entry is None or entry.owner != owner:
            raise RunUnavailable()
        try:
            if entry.result.expires_at <= self.reconstructions.now():
                raise RunUnavailable()
            await self.reconstructions.read(entry.result.source_run_id, owner)
        except RunUnavailable:
            self._runs.pop(run_id, None)
            raise
        return entry

    async def prepare(self, source_run_id: str, owner: str, request: StartOptimizationRequest) -> OptimizationRun:
        request = StartOptimizationRequest.model_validate(request).model_copy(deep=True)
        async with self._lock:
            await self.cleanup()
            for entry in self._runs.values():
                if entry.owner == owner and entry.request.request_id == request.request_id:
                    if entry.result.source_run_id != source_run_id or entry.request != request:
                        raise OptimizationConflict()
                    return await self.read(entry.result.run_id, owner)
            source = await self.reconstructions.read(source_run_id, owner)
            if (source.status not in {"completed", "partial"} or source.working_scene is None
                or source.proposal is None or source.proposal.validation is None
                or not source.proposal.validation.valid):
                raise ReconstructionRequired()
            valid = await anyio.to_thread.run_sync(lambda: validate_geometry(source.working_scene).valid)
            if not valid:
                raise ReconstructionRequired()
            # Reauthorize after geometry work; revoked/expired photos cannot seed new work.
            await self.reconstructions.read(source_run_id, owner)
            parent_id = request.context.parent_run_id if request.context else None
            base = source.working_scene
            if parent_id:
                parent = await self.read(parent_id, owner)
                if (parent.source_run_id != source_run_id or parent.status in {"prepared", "running"}
                    or parent.result_scene is None or not any(a.outcome == "accepted" for a in parent.attempts)):
                    raise InvalidSceneContext("Choose a retained candidate from this room as the starting scene.")
                base = parent.result_scene
            starting = base
            if request.context:
                try:
                    starting = await anyio.to_thread.run_sync(lambda: validate_context(base, request.context.scene))
                except ValueError as error:
                    raise InvalidSceneContext(str(error)) from None
            if request.instruction and sum(e.result.source_run_id == source_run_id and e.request.instruction is not None for e in self._runs.values()) >= 5:
                raise InvalidSceneContext("This temporary room has reached its five chat refinements. Your scenes remain available.")
            await self.reconstructions.read(source_run_id, owner)
            if len(self._runs) >= self.max_runs:
                raise RunCapacityReached()
            result = OptimizationRun(
                run_id=new_run_id(), source_run_id=source_run_id, input_id=source.input_id,
                source_scene_hash=scene_hash(starting), source_scene=starting.model_copy(deep=True),
                source_status=source.status, baseline_assessment=source.room_assessment if starting == source.working_scene else None,
                focus=request.focus, preferences=request.preferences,
                instruction=request.instruction, parent_run_id=parent_id,
                created_at=self.reconstructions.now(), expires_at=source.expires_at,
                max_candidates=min(self.max_candidates, 2) if request.instruction else self.max_candidates,
                limits=self.limits.model_copy(update={"max_model_calls": min(self.limits.max_model_calls, 4)}) if request.instruction else self.limits,
            )
            self._runs[result.run_id] = ImprovementEntry(owner, request, source.model_copy(deep=True), result)
            self._runs[result.run_id].progress.publish(result)
            return result.model_copy(deep=True)

    async def read(self, run_id: str, owner: str) -> OptimizationRun:
        entry = await self._owned(run_id, owner)
        result = entry.result
        if entry.execution_started and not entry.finished:
            snapshot = await build_graph(entry.saver).aget_state(self._config(entry))
            if snapshot.values: result = OptimizationRun.model_validate(snapshot.values['run'])
        if entry.budget: result = result.model_copy(update={"usage": entry.budget.usage})
        await self._owned(run_id, owner)
        return result.model_copy(deep=True)

    async def observe(self, run_id: str, owner: str):
        entry = await self._owned(run_id, owner)
        state = await self.read(run_id, owner)
        if not entry.progress.events: entry.progress.publish(state)
        return state, entry.progress

    @staticmethod
    def _config(entry):
        return {"configurable": {"thread_id": entry.result.run_id}, "recursion_limit": 4 * entry.result.max_candidates + 8}

    async def execute(self, run_id: str, owner: str) -> OptimizationRun:
        entry = await self._owned(run_id, owner)
        # Reserve execution synchronously. Retry/read never replays paid work,
        # including a disconnected request with an ambiguous provider outcome.
        if entry.execution_started or entry.result.status != "prepared":
            return await self.read(run_id, owner)
        entry.execution_started, entry.busy = True, True
        entry.result = entry.result.model_copy(update={"status": "running", "stage": "proposing"})
        entry.progress.publish(entry.result)
        async def authorize(): await self._owned(run_id, owner)
        budget = entry.budget = RunBudget(entry.result.limits, entry.result.usage, authorize,
            lambda: entry.cancel_requested, run_id=run_id)
        try:
            if self.provider is None:
                raise AnalysisFailure(503, "analysis_unavailable", "Optimization is not configured.")
            catalog = None
            if self.catalog is not None:
                await budget.reserve_tool()
                try:
                    filters = CatalogSearch(object_type="desk" if entry.result.focus == "workspace" else "shelf" if entry.result.focus == "storage" else None, limit=4)
                    catalog = (await self.catalog.call("search_furniture", {"filters": filters.model_dump(mode="json")})).model_dump(mode="json")
                except CatalogFailure:
                    catalog = {"unavailable": True, "message": "Continue using existing items. Catalog purchases and prices are unavailable."}
            await authorize()
            context = OptimizationContext(entry.source, authorize, lambda: entry.cancel_requested, budget, self.provider, catalog)
            async for values in build_graph(entry.saver).astream(
                {"run": entry.result.model_dump(), "proposal": None, "candidate": None},
                self._config(entry), context=context, stream_mode="values",
            ):
                if "run" in values:
                    state = OptimizationRun.model_validate(values["run"])
                    if state.status in {"prepared", "running"}:
                        entry.progress.publish(state, cancelling=entry.cancel_requested)
            entry.result = await self.read(run_id, owner)
            entry.finished = True
        except anyio.get_cancelled_exc_class():
            entry.cancel_requested = True
            with anyio.CancelScope(shield=True):
                await self._finish_error(entry, owner, "cancelled", "Execution was interrupted. No further paid work will run; validated results are retained.")
            raise
        except RunUnavailable:
            raise
        except Exception as error:
            logging.getLogger(__name__).warning("Optimization stopped: run_id=%s error_type=%s code=%s",
                run_id, type(error).__name__, error.code if isinstance(error, AnalysisFailure) else "optimization_failed")
            status = "cancelled" if entry.cancel_requested else "budget_exhausted" if budget.exhausted else "failed"
            message = "The improvement stopped before another candidate could be accepted. The original and any earlier validated candidate are retained."
            if isinstance(error, AnalysisFailure) and error.code == "analysis_incomplete": message = "The model did not finish within its output limit. The original and any earlier validated candidate are retained."
            await self._finish_error(entry, owner, status, message)
        finally:
            entry.busy = False
        current = await self.read(run_id, owner)
        entry.progress.publish(current, force=True)
        return current

    async def _finish_error(self, entry, owner, status, message):
        if entry.result.instruction: message = refinement_message(message)
        try:
            current = await self.read(entry.result.run_id, owner)
            entry.result = current.model_copy(update={"status": status, "stage": "finished", "message": message})
            entry.finished = True
            entry.progress.publish(entry.result, force=True)
        except RunUnavailable:
            pass

    async def cancel(self, run_id: str, owner: str) -> OptimizationRun:
        entry = await self._owned(run_id, owner)
        entry.cancel_requested = True
        if entry.busy:
            current = await self.read(run_id, owner)
            entry.progress.publish(current, cancelling=True)
            return current
        if entry.finished: return await self.read(run_id, owner)
        entry.result = entry.result.model_copy(update={"status": "cancelled", "stage": "finished",
            "message": "Refinement cancelled; your starting layout is retained." if entry.result.instruction else entry.result.message})
        entry.finished = True
        entry.progress.publish(entry.result, force=True)
        return entry.result.model_copy(deep=True)

    async def cleanup(self) -> None:
        for run_id, entry in list(self._runs.items()):
            try:
                await self._owned(run_id, entry.owner)
            except RunUnavailable:
                pass
