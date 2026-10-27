"""Read-only public allowance summary, with authorization for room-specific usage."""

import anyio
from fastapi import APIRouter, Query, Request, Response

from roometry.agents.access import execution_owner
from roometry.agents.router import result
from roometry.auth.security import COOKIE_NAME as AUTH_COOKIE
from roometry.uploads.schemas import OpaqueId
from roometry.uploads.session import session

router = APIRouter(prefix="/api/usage", tags=["AI allowance"])


@router.get("")
async def usage(
    request: Request, response: Response, run_id: OpaqueId | None = Query(default=None)
):
    quotas = request.app.state.quotas
    response.headers["Cache-Control"] = "no-store"
    if not quotas.enabled:
        return quotas.status("")
    _, guest = session(request)
    owner = guest
    if run_id:
        owner = await execution_owner(request, reference=run_id)
        await result(request.app.state.agent_runs.read(run_id, owner))
    elif request.cookies.get(AUTH_COOKIE):
        user = (
            await anyio.to_thread.run_sync(
                request.app.state.auth_service.current, request.cookies[AUTH_COOKIE]
            )
        ).user
        owner = f"account:{user.id}"
    return await anyio.to_thread.run_sync(quotas.status, owner, run_id)
