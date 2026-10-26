"""Owner-only private saves and reads; no public publishing endpoint."""

from uuid import UUID

import anyio
from fastapi import APIRouter, Query, Request, Response

from roometry.agents.access import execution_owner
from roometry.agents.persistence import seed_project
from roometry.agents.router import EmptyRequest, result
from roometry.auth.errors import AuthFailure
from roometry.auth.router import mutation
from roometry.auth.security import COOKIE_NAME
from roometry.projects.schemas import (
    ProjectRevision,
    RenameProject,
    SavedProject,
    SaveProjectRequest,
)

router = APIRouter(prefix="/api/projects", tags=["private rooms"])


async def account(request, response, *, writing=False):
    response.headers["Cache-Control"] = "no-store"
    if writing:
        mutation(request)
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise AuthFailure(
            401, "sign_in_required", "Sign in to save your room privately."
        )
    return (
        await anyio.to_thread.run_sync(request.app.state.auth_service.current, token)
    ).user.id


@router.post("", response_model=SavedProject)
async def save(body: SaveProjectRequest, request: Request, response: Response):
    user_id = await account(request, response, writing=True)
    owner = await execution_owner(request, reference=body.source_run_id)
    return await request.app.state.project_service.save(user_id, owner, body)


@router.get("")
async def list_rooms(
    request: Request, response: Response, offset: int = Query(default=0, ge=0, le=10000)
):
    user = await account(request, response)
    return await anyio.to_thread.run_sync(
        request.app.state.project_service.list_rooms, user, offset
    )


@router.get("/runs")
async def recent_runs(request: Request, response: Response):
    user = await account(request, response)
    return await anyio.to_thread.run_sync(
        request.app.state.registered_runs.recent, user
    )


@router.get("/runs/{run_id}")
async def recover_run(run_id: UUID, request: Request, response: Response):
    user = await account(request, response)
    owner = f"account:{user}"
    run = await result(request.app.state.agent_runs.read(str(run_id), owner))
    prepared = await anyio.to_thread.run_sync(
        request.app.state.registered_runs.resolve, run.input_id, owner
    )
    metadata = await anyio.to_thread.run_sync(
        request.app.state.registered_runs.load, owner, str(run_id)
    )
    return {
        "run": run.model_dump(mode="json"),
        "input": prepared.model_dump(mode="json"),
        "request_id": metadata["request_id"],
    }


@router.post("/{project_id}/open")
async def open_room(project_id: UUID, body: EmptyRequest, request: Request, response: Response):
    user = await account(request, response, writing=True)
    service = request.app.state.project_service
    detail = await anyio.to_thread.run_sync(service.read, user, project_id)
    source, reseeded = await anyio.to_thread.run_sync(
        seed_project, request.app.state.registered_runs, user, detail
    )
    owner = f"account:{user}"
    if reseeded:
        request.app.state.agent_runs.checkpoints._runs.pop(
            f"{owner}:{source.run_id}", None
        )
        for candidate in detail["snapshot"]["improvements"]:
            request.app.state.optimization_runs._runs.pop(f"{owner}:{candidate}", None)
    source = await result(request.app.state.agent_runs.read(source.run_id, owner))
    return {"project": detail, "run": source.model_dump(mode="json")}


@router.patch("/{project_id}")
async def rename_room(
    project_id: UUID, body: RenameProject, request: Request, response: Response
):
    user = await account(request, response, writing=True)
    return await anyio.to_thread.run_sync(
        request.app.state.project_service.rename,
        user,
        project_id,
        body.title,
        body.expected_revision,
    )


@router.delete("/{project_id}", status_code=204)
async def delete_room(
    project_id: UUID, body: ProjectRevision, request: Request, response: Response
):
    user = await account(request, response, writing=True)
    await anyio.to_thread.run_sync(
        request.app.state.project_service.delete_room,
        user,
        project_id,
        body.expected_revision,
    )


@router.get("/{project_id}")
async def read(project_id: UUID, request: Request, response: Response):
    user_id = await account(request, response)
    return await anyio.to_thread.run_sync(
        request.app.state.project_service.read, user_id, project_id
    )


@router.get("/{project_id}/images/{image_id}")
async def image(project_id: UUID, image_id: UUID, request: Request, response: Response):
    user_id = await account(request, response)
    data = await anyio.to_thread.run_sync(
        request.app.state.project_service.image, user_id, project_id, image_id
    )
    return Response(
        data,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": 'inline; filename="room-photo.jpg"',
        },
    )
