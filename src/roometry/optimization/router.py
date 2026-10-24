"""Use the same session, origin and body limits as reconstruction runs."""

from fastapi import APIRouter, Request, Response

from roometry.agents.router import EmptyRequest, RunFailure, access, result
from roometry.optimization.runs import OptimizationConflict, OptimizationRuns, ReconstructionRequired
from roometry.optimization.schemas import OptimizationRun, StartOptimizationRequest
from roometry.uploads.schemas import OpaqueId

router = APIRouter(prefix="/api/agent-runs", tags=["room improvements"])


async def improvement_result(operation):
    try:
        return await result(operation)
    except OptimizationConflict:
        raise RunFailure(409, "optimization_conflict", "This request ID already identifies a different improvement request.") from None
    except ReconstructionRequired:
        raise RunFailure(409, "reconstruction_required", "Finish a complete or partial room reconstruction before requesting an improvement.") from None


@router.post("/{source_run_id}/optimizations", response_model=OptimizationRun,
             summary="Prepare a separate improvement request; no optimization is executed yet")
async def prepare_improvement(source_run_id: OpaqueId, body: StartOptimizationRequest,
                              request: Request, response: Response) -> OptimizationRun:
    _, owner = access(request, response, mutation=True)
    runs: OptimizationRuns = request.app.state.optimization_runs
    return await improvement_result(runs.prepare(source_run_id, owner, body))


@router.get("/{source_run_id}/optimizations/{run_id}", response_model=OptimizationRun)
async def read_improvement(source_run_id: OpaqueId, run_id: OpaqueId,
                           request: Request, response: Response) -> OptimizationRun:
    _, owner = access(request, response)
    runs: OptimizationRuns = request.app.state.optimization_runs
    found = await improvement_result(runs.read(run_id, owner))
    if found.source_run_id != source_run_id:
        raise RunFailure(404, "run_not_found", "This run is unavailable or has expired.")
    return found


@router.post("/{source_run_id}/optimizations/{run_id}/execute", response_model=OptimizationRun,
             summary="Execute the bounded improvement graph once; retries read the same execution")
async def execute_improvement(source_run_id: OpaqueId, run_id: OpaqueId, body: EmptyRequest,
                              request: Request, response: Response) -> OptimizationRun:
    _, owner = access(request, response, mutation=True)
    runs: OptimizationRuns = request.app.state.optimization_runs
    found = await improvement_result(runs.read(run_id, owner))
    if found.source_run_id != source_run_id:
        raise RunFailure(404, "run_not_found", "This run is unavailable or has expired.")
    return await improvement_result(runs.execute(run_id, owner))


@router.post("/{source_run_id}/optimizations/{run_id}/cancel", response_model=OptimizationRun)
async def cancel_improvement(source_run_id: OpaqueId, run_id: OpaqueId, body: EmptyRequest,
                             request: Request, response: Response) -> OptimizationRun:
    _, owner = access(request, response, mutation=True)
    runs: OptimizationRuns = request.app.state.optimization_runs
    found = await improvement_result(runs.read(run_id, owner))
    if found.source_run_id != source_run_id:
        raise RunFailure(404, "run_not_found", "This run is unavailable or has expired.")
    return await improvement_result(runs.cancel(run_id, owner))
