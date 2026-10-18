"""Opaque guest scope for temporary uploads, not account or quota authentication."""

import hashlib
import re
import secrets

from fastapi import Request

from roometry.config import Settings
from roometry.uploads.errors import UploadFailure

COOKIE_NAME = "roometry_upload_session"
TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")


def owner_scope(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def session(request: Request) -> tuple[str, str]:
    supplied = request.cookies.get(COOKIE_NAME, "")
    token = supplied if TOKEN.fullmatch(supplied) else secrets.token_urlsafe(32)
    return token, owner_scope(token)


def check_origin(request: Request, settings: Settings) -> None:
    # Non-browser clients may omit Origin. Browsers sending credentials may not
    # mutate private inputs from an untrusted (including opaque/null) origin.
    origin = request.headers.get("origin")
    if origin is None:
        return
    allowed = {str(request.base_url).rstrip("/"), *settings.cors_origins}
    if settings.app_environment == "development":
        allowed.update({"http://localhost:5173", "http://127.0.0.1:5173"})
    if origin not in allowed:
        raise UploadFailure(
            403, "forbidden_origin", "This origin cannot change room inputs."
        )
