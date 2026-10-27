"""Explicit authenticated publishing and anonymous allowlisted room browsing."""

from uuid import UUID

import anyio
from fastapi import APIRouter, Query, Request, Response

from roometry.feed.schemas import (
    FeedPage,
    PublishRequest,
    SharedRoom,
    SharingOptionsRequest,
    UnpublishRequest,
)
from roometry.projects.router import account

router = APIRouter(tags=["shared rooms"])


def headers(response):
    response.headers["Cache-Control"] = "no-store"


@router.get("/api/feed", response_model=FeedPage)
async def feed(
    request: Request,
    response: Response,
    cursor: str | None = Query(default=None, max_length=200),
):
    headers(response)
    return await anyio.to_thread.run_sync(request.app.state.feed_service.list, cursor)


@router.get("/api/feed/{public_id}", response_model=SharedRoom)
async def room(public_id: UUID, request: Request, response: Response):
    headers(response)
    return await anyio.to_thread.run_sync(
        request.app.state.feed_service.read, public_id
    )


@router.get("/api/feed/{public_id}/preview")
async def public_preview(public_id: UUID, request: Request):
    data = await anyio.to_thread.run_sync(
        request.app.state.feed_service.read, public_id, True
    )
    return Response(
        data,
        media_type="image/png",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/api/projects/sharing-options")
async def sharing_options(
    body: SharingOptionsRequest, request: Request, response: Response
):
    user = await account(request, response, writing=True)
    return await anyio.to_thread.run_sync(
        request.app.state.feed_service.options, user, body
    )


@router.get("/api/projects/{project_id}/versions/{version_id}/preview")
async def saved_preview(
    project_id: UUID, version_id: UUID, request: Request, response: Response
):
    user = await account(request, response)
    async with request.app.state.feed_render_limiter:
        data = await anyio.to_thread.run_sync(
            request.app.state.feed_service.private_preview, user, project_id, version_id
        )
    return Response(
        data,
        media_type="image/png",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/api/projects/{project_id}/publication")
async def publish(
    project_id: UUID, body: PublishRequest, request: Request, response: Response
):
    user = await account(request, response, writing=True)
    async with request.app.state.feed_render_limiter:
        return await anyio.to_thread.run_sync(
            request.app.state.feed_service.publish, user, project_id, body
        )


@router.delete("/api/projects/{project_id}/publication", status_code=204)
async def unpublish(
    project_id: UUID, body: UnpublishRequest, request: Request, response: Response
):
    user = await account(request, response, writing=True)
    await anyio.to_thread.run_sync(
        request.app.state.feed_service.unpublish, user, project_id, body
    )
