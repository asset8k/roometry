"""Account-owned execution is independent of a browser's temporary upload cookie."""

import anyio

from roometry.agents.checkpointing import RunUnavailable
from roometry.auth.errors import AuthFailure
from roometry.auth.security import COOKIE_NAME
from roometry.uploads.session import session


async def execution_owner(request, reference=None, input_id=None, request_id=None):
    _, guest = session(request)
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return guest
    user = (
        await anyio.to_thread.run_sync(request.app.state.auth_service.current, token)
    ).user
    registered = request.app.state.registered_runs
    if input_id is not None:
        await anyio.to_thread.run_sync(registered.pin, input_id, guest, user.id)
        return f"account:{user.id}"
    if reference and await anyio.to_thread.run_sync(
        registered.owned, reference, user.id
    ):
        return f"account:{user.id}"
    if request_id and await anyio.to_thread.run_sync(
        registered.find, f"account:{user.id}", request_id
    ):
        return f"account:{user.id}"
    return guest


def stream_reader(request, owner, read):
    """A long-lived connection must stop when its original session is revoked."""

    async def authorized_read():
        if owner.startswith("account:"):
            token = request.cookies.get(COOKIE_NAME)
            if not token:
                raise RunUnavailable()
            try:
                current = await anyio.to_thread.run_sync(
                    request.app.state.auth_service.current, token
                )
            except AuthFailure:
                raise RunUnavailable() from None
            if owner != f"account:{current.user.id}":
                raise RunUnavailable()
        return await read()

    return authorized_read
