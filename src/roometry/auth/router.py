from collections import deque
from time import monotonic

import anyio
from fastapi import APIRouter, Request, Response

from roometry.auth.errors import AuthFailure
from roometry.auth.schemas import (
    AuthResponse,
    Credentials,
    LogoutRequest,
    SessionResponse,
)
from roometry.auth.security import COOKIE_NAME, COOKIE_PATH

router = APIRouter(prefix="/api/auth", tags=["accounts"])


class AuthAttempts:
    """Small single-worker credential throttle; generation quotas remain separate."""

    def __init__(self, limit):
        self.limit, self.entries = limit, {}

    def check(self, address):
        now = monotonic()
        for key in list(self.entries):
            values = self.entries[key]
            while values and values[0] <= now - 60:
                values.popleft()
            if not values:
                del self.entries[key]
        if address not in self.entries and len(self.entries) >= 1024:
            raise AuthFailure(
                429, "auth_rate_limited", "Too many attempts. Try again in a minute."
            )
        values = self.entries.setdefault(address, deque())
        if len(values) >= self.limit:
            raise AuthFailure(
                429, "auth_rate_limited", "Too many attempts. Try again in a minute."
            )
        values.append(now)


def mutation(request):
    settings = request.app.state.settings
    origin = request.headers.get("origin")
    allowed = {str(request.base_url).rstrip("/"), *settings.cors_origins}
    if settings.app_environment == "development":
        allowed.update({"http://localhost:5173", "http://127.0.0.1:5173"})
    if (origin is not None and origin not in allowed) or (
        origin is None and request.headers.get("sec-fetch-site") == "cross-site"
    ):
        raise AuthFailure(
            403, "forbidden_origin", "This site cannot change your account session."
        )


def clear_cookie(response, settings):
    response.delete_cookie(
        COOKIE_NAME,
        path=COOKIE_PATH,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
    )


async def authenticate(request, response, credentials, method):
    mutation(request)
    request.app.state.auth_service.available()
    request.app.state.auth_attempts.check(
        request.client.host if request.client else "unknown"
    )
    token, result = await anyio.to_thread.run_sync(
        method,
        credentials,
        request.cookies.get(COOKIE_NAME),
        limiter=request.app.state.auth_hash_limiter,
    )
    settings = request.app.state.settings
    response.set_cookie(
        COOKIE_NAME,
        token,
        path=COOKIE_PATH,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        max_age=settings.access_token_expire_minutes * 60,
    )
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/register", response_model=AuthResponse, status_code=201)
async def register(body: Credentials, request: Request, response: Response):
    return await authenticate(
        request, response, body, request.app.state.auth_service.register
    )


@router.post("/login", response_model=AuthResponse)
async def login(body: Credentials, request: Request, response: Response):
    return await authenticate(
        request, response, body, request.app.state.auth_service.login
    )


@router.get("/me", response_model=SessionResponse)
async def me(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return SessionResponse(user=None, expires_at=None)
    return await anyio.to_thread.run_sync(request.app.state.auth_service.current, token)


@router.post("/logout", status_code=204)
async def logout(body: LogoutRequest, request: Request, response: Response):
    mutation(request)
    token = request.cookies.get(COOKIE_NAME)
    if token:
        await anyio.to_thread.run_sync(request.app.state.auth_service.logout, token)
    clear_cookie(response, request.app.state.settings)
    response.headers["Cache-Control"] = "no-store"
