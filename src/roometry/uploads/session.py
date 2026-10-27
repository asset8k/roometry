"""Opaque guest scope for temporary uploads, not account or quota authentication."""

import hashlib
import hmac
import re
import secrets

from fastapi import Request

from roometry.config import Settings
from roometry.uploads.errors import UploadFailure

COOKIE_NAME = "roometry_upload_session"
TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")


def owner_scope(token: str) -> str:
    return hashlib.sha256(token.partition(".")[0].encode("ascii")).hexdigest()


def session(request: Request) -> tuple[str, str]:
    supplied = request.cookies.get(COOKIE_NAME, "")
    settings = request.app.state.settings
    raw, _, signature = supplied.partition(".")
    key = settings.jwt_secret_key.get_secret_value().encode() if settings.jwt_secret_key else None
    expected = hmac.new(key, raw.encode(), hashlib.sha256).hexdigest() if key else ""
    signed = bool(re.fullmatch(r"[a-f0-9]{64}", signature) and key and hmac.compare_digest(signature, expected))
    valid = TOKEN.fullmatch(raw) and (signed or (not signature and not settings.quota_enabled))
    token = raw if valid else secrets.token_urlsafe(32)
    cookie = token
    if settings.quota_enabled:
        cookie += "." + hmac.new(key, token.encode(), hashlib.sha256).hexdigest()
    return cookie, owner_scope(token)


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
