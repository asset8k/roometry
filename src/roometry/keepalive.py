"""Token-protected, read-only readiness probe; no model or storage calls."""

import secrets
from threading import Lock

import anyio
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()
PROBE_TIMEOUT_SECONDS = 12


@router.get("/internal/keepalive", include_in_schema=False)
async def keepalive(request: Request):
    headers = {"Cache-Control": "no-store"}
    configured = request.app.state.settings.keepalive_token
    supplied = request.headers.get("X-Keepalive-Token", "")
    expected = configured.get_secret_value() if configured else ""
    if not expected.strip() or not supplied or not secrets.compare_digest(
        supplied.encode("utf-8"), expected.encode("utf-8")
    ):
        return JSONResponse({"detail": "Forbidden"}, status_code=403, headers=headers)

    def probe():
        # An abandoned driver call must not create an unbounded queue of probes.
        lock: Lock = request.app.state.keepalive_lock
        if not lock.acquire(blocking=False):
            raise RuntimeError("Probe busy")
        try:
            database = request.app.state.database
            if database is None:
                raise RuntimeError("Database unavailable")
            database.keepalive()
        finally:
            lock.release()

    try:
        with anyio.fail_after(PROBE_TIMEOUT_SECONDS):
            await anyio.to_thread.run_sync(probe, abandon_on_cancel=True)
    except Exception:
        return JSONResponse({"detail": "Service unavailable"}, status_code=503, headers=headers)
    return JSONResponse({"status": "ok", "database": "ok"}, headers=headers)
