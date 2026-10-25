"""Password hashes and strictly scoped JWTs; raw tokens stay in HttpOnly cookies."""

import re
import secrets
from datetime import UTC, datetime
from functools import lru_cache
from hashlib import sha256
from uuid import UUID

import jwt
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError

COOKIE_NAME = "roometry_auth"
COOKIE_PATH = "/api"
ISSUER, AUDIENCE = "roometry", "roometry-web"
HASHER = PasswordHash.recommended()
JTI = re.compile(r"^[A-Za-z0-9_-]{43}$")


@lru_cache(maxsize=1)
def dummy_hash():
    return HASHER.hash(secrets.token_urlsafe(32))


def hash_password(value: str):
    return HASHER.hash(value)


def verify_password(value: str, hashed: str | None):
    try:
        valid = HASHER.verify(value, hashed or dummy_hash())
        return bool(hashed) and valid
    except (ValueError, UnknownHashError):
        return False


def token_hash(jti: str):
    return sha256(jti.encode("ascii")).hexdigest()


def issue_token(user_id: UUID, secret: str, lifetime_seconds: int):
    now = int(datetime.now(UTC).timestamp())
    jti = secrets.token_urlsafe(32)
    claims = {
        "sub": str(user_id),
        "jti": jti,
        "iat": now,
        "nbf": now,
        "exp": now + lifetime_seconds,
        "iss": ISSUER,
        "aud": AUDIENCE,
    }
    return (
        jwt.encode(claims, secret, algorithm="HS256"),
        token_hash(jti),
        datetime.fromtimestamp(claims["exp"], UTC),
    )


def decode_token(token: str, secret: str):
    if len(token) > 2048:
        raise ValueError("Invalid session.")
    claims = jwt.decode(
        token,
        secret,
        algorithms=["HS256"],
        issuer=ISSUER,
        audience=AUDIENCE,
        options={"require": ["sub", "jti", "iat", "nbf", "exp", "iss", "aud"]},
    )
    if not isinstance(claims["jti"], str) or not JTI.fullmatch(claims["jti"]):
        raise ValueError("Invalid session.")
    return UUID(claims["sub"]), token_hash(claims["jti"])
