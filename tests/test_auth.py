"""Accounts use real password hashing, JWT validation and migrated SQL storage; no models."""

import asyncio
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import httpx
import jwt
import pytest
from alembic import command
from alembic.config import Config
from PIL import Image
from sqlalchemy import func, inspect, select

from roometry.auth.security import COOKIE_NAME
from roometry.config import Settings
from roometry.database import Database
from roometry.main import create_app
from roometry.uploads.session import COOKIE_NAME as GUEST_COOKIE_NAME
from roometry.uploads.session import owner_scope
from roometry.users.model import AuthSession, User

pytestmark = pytest.mark.anyio
PASS = "correct horse battery test!"
KEY = "test-only-signing-key-" * 4


@pytest.fixture
def anyio_backend():
    return "asyncio"


def migrate(url):
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    return config


@pytest.fixture
async def accounts(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'accounts.db'}"
    migrate(url)
    settings = Settings(
        _env_file=None,
        database_url=url,
        jwt_secret_key=KEY,
        upload_storage_dir=tmp_path / "photos",
        auth_attempts_per_minute=120,
    )
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        yield app, client, settings


async def signup(client, username="test_user"):
    response = await client.post(
        "/api/auth/register", json={"username": username, "password": PASS}
    )
    assert response.status_code == 201, response.text
    return response


async def with_token(app, token):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        cookies={COOKIE_NAME: token},
    ) as client:
        return await client.get("/api/auth/me")


async def test_registration_hashes_password_and_sets_only_an_httponly_session(accounts):
    app, client, _ = accounts
    assert (await client.get("/api/auth/me")).json() == {
        "user": None,
        "expires_at": None,
    }
    result = await signup(client, " Test_User ")
    assert set(result.json()) == {"user", "expires_at"}
    assert result.json()["user"]["username"] == "test_user"
    assert set(result.json()["user"]) == {"id", "username"}
    cookie = result.headers["set-cookie"].lower()
    assert (
        "httponly" in cookie
        and "samesite=lax" in cookie
        and "path=/api" in cookie
        and "max-age=3600" in cookie
    )
    assert (
        PASS not in result.text
        and "password_hash" not in result.text
        and "access_token" not in result.text
    )
    assert result.headers["cache-control"] == "no-store"
    assert (await client.get("/api/auth/me")).json() == result.json()
    with app.state.database.sessions() as db:
        user = db.scalar(select(User))
        session = db.scalar(select(AuthSession))
        assert (
            user.password_hash.startswith("$argon2id$") and user.password_hash != PASS
        )
        assert (
            len(session.token_hash) == 64
            and session.token_hash not in client.cookies[COOKIE_NAME]
        )
    assert not app.state.analysis_service.analyzer.usage_records


async def test_duplicate_usernames_are_case_insensitive_and_atomic(accounts):
    app, client, _ = accounts
    results = await asyncio.gather(
        *[
            client.post("/api/auth/register", json={"username": name, "password": PASS})
            for name in ("Same_User", "same_user")
        ]
    )
    assert sorted(r.status_code for r in results) == [201, 409]
    with app.state.database.sessions() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 1


async def test_account_transitions_preserve_guest_upload_ownership(accounts):
    app, client, _ = accounts
    photo = BytesIO()
    with Image.new("RGB", (256, 256), "gray") as image:
        image.save(photo, "PNG")
    uploaded = await client.post(
        "/api/rooms/inputs",
        files=[
            ("options", (None, "{}")),
            ("images", ("room.png", photo.getvalue(), "image/png")),
        ],
    )
    assert uploaded.status_code == 201
    input_id = uploaded.json()["input_id"]
    guest_cookie = client.cookies[GUEST_COOKIE_NAME]
    storage = app.state.upload_service.storage
    await signup(client)
    assert client.cookies[GUEST_COOKIE_NAME] == guest_cookie
    assert storage.resolve(input_id, owner_scope(guest_cookie)).input_id == input_id
    # An account cookie alone cannot take over a different browser's guest upload.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        cookies={COOKIE_NAME: client.cookies[COOKIE_NAME]},
    ) as other_browser:
        assert (
            await other_browser.delete(f"/api/rooms/inputs/{input_id}")
        ).status_code == 404
    assert (await client.post("/api/auth/logout", json={})).status_code == 204
    assert client.cookies[GUEST_COOKIE_NAME] == guest_cookie
    assert storage.resolve(input_id, owner_scope(guest_cookie)).input_id == input_id
    assert (await client.delete(f"/api/rooms/inputs/{input_id}")).status_code == 204


async def test_login_error_is_generic_and_does_not_destroy_a_valid_session(accounts):
    _, client, _ = accounts
    initial = (await signup(client)).json()
    errors = []
    for username in ("test_user", "unknown_user"):
        response = await client.post(
            "/api/auth/login",
            json={"username": username, "password": "a completely wrong password"},
        )
        assert response.status_code == 401
        errors.append(response.json())
    assert errors[0] == errors[1]
    assert (await client.get("/api/auth/me")).json() == initial


async def test_login_rotates_the_current_session_and_logout_revokes_it(accounts):
    app, client, _ = accounts
    await signup(client)
    previous = client.cookies[COOKIE_NAME]
    result = await client.post(
        "/api/auth/login", json={"username": "TEST_USER", "password": PASS}
    )
    assert result.status_code == 200 and client.cookies[COOKIE_NAME] != previous
    assert (await with_token(app, previous)).status_code == 401
    token = client.cookies[COOKIE_NAME]
    assert (await client.post("/api/auth/logout", json={})).status_code == 204
    assert (await client.get("/api/auth/me")).json()["user"] is None
    assert (await with_token(app, token)).status_code == 401
    assert (await client.post("/api/auth/logout", json={})).status_code == 204


async def test_valid_session_survives_app_restart(accounts, tmp_path):
    _, client, settings = accounts
    user = (await signup(client)).json()["user"]
    token = client.cookies[COOKIE_NAME]
    restarted = create_app(
        settings.model_copy(update={"upload_storage_dir": tmp_path / "other-photos"})
    )
    async with restarted.router.lifespan_context(restarted):
        result = await with_token(restarted, token)
        assert result.status_code == 200 and result.json()["user"] == user


@pytest.mark.parametrize(
    "tamper",
    [
        "expired",
        "future",
        "issuer",
        "audience",
        "missing",
        "user",
        "signature",
        "algorithm",
        "unsigned",
    ],
)
async def test_invalid_tokens_are_rejected_and_cleared(accounts, tamper):
    app, client, _ = accounts
    await signup(client)
    token = client.cookies[COOKIE_NAME]
    claims = jwt.decode(
        token, KEY, algorithms=["HS256"], audience="roometry-web", issuer="roometry"
    )
    if tamper == "expired":
        claims["exp"] = int(datetime.now(UTC).timestamp()) - 10
    if tamper == "future":
        claims["nbf"] = int(datetime.now(UTC).timestamp()) + 120
    if tamper == "issuer":
        claims["iss"] = "another-app"
    if tamper == "audience":
        claims["aud"] = "another-audience"
    if tamper == "missing":
        claims.pop("jti")
    if tamper == "user":
        claims["sub"] = str(uuid4())
    bad = jwt.encode(
        claims,
        "wrong-key" * 12 if tamper == "signature" else KEY,
        algorithm="HS512" if tamper == "algorithm" else "HS256",
    )
    if tamper == "unsigned":
        bad = jwt.encode(claims, key="", algorithm="none")
    response = await with_token(app, bad)
    assert (
        response.status_code == 401
        and "max-age=0" in response.headers["set-cookie"].lower()
    )
    assert response.json()["detail"][0]["code"] == "invalid_session"


async def test_disabled_users_and_database_expired_sessions_are_rejected(accounts):
    app, client, _ = accounts
    await signup(client)
    token = client.cookies[COOKIE_NAME]
    with app.state.database.sessions.begin() as db:
        db.scalar(select(User)).is_active = False
    assert (await with_token(app, token)).status_code == 401
    with app.state.database.sessions.begin() as db:
        db.scalar(select(User)).is_active = True
        db.scalar(select(AuthSession)).expires_at = datetime(2000, 1, 1, tzinfo=UTC)
    assert (await with_token(app, token)).status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {"username": "ab", "password": PASS},
        {"username": "bad@name", "password": PASS},
        {"username": "test_user", "password": "short"},
        {"username": "test_user", "password": "x" * 129},
        {"username": "test_user", "password": PASS, "role": "admin"},
    ],
)
async def test_invalid_credentials_never_echo_passwords(accounts, body):
    _, client, _ = accounts
    response = await client.post("/api/auth/register", json=body)
    assert response.status_code == 422
    assert (
        body["password"] not in response.text
        and "input" not in response.json()["detail"][0]
    )
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "origin", ["null", "https://untrusted.example", "http://testserver.evil.example"]
)
async def test_untrusted_origins_cannot_mutate_sessions(accounts, origin):
    _, client, _ = accounts
    response = await client.post(
        "/api/auth/register",
        headers={"origin": origin},
        json={"username": "test_user", "password": PASS},
    )
    assert response.status_code == 403
    assert (
        await client.post("/api/auth/logout", headers={"origin": origin}, json={})
    ).status_code == 403


async def test_limits_reject_large_or_duplicate_json_and_throttle_password_attempts(
    accounts, monkeypatch
):
    app, client, _ = accounts
    assert (
        await client.post(
            "/api/auth/login",
            content=b"x" * 4097,
            headers={"content-type": "application/json"},
        )
    ).status_code == 413
    assert (
        await client.post(
            "/api/auth/login", content=b"{}", headers={"content-type": "text/plain"}
        )
    ).status_code == 415
    duplicate = b'{"username":"test_user","password":"one secret password","password":"another secret password"}'
    assert (
        await client.post(
            "/api/auth/login",
            content=duplicate,
            headers={"content-type": "application/json"},
        )
    ).status_code == 422
    app.state.auth_attempts.limit = 2
    for _ in range(2):
        assert (
            await client.post(
                "/api/auth/login", json={"username": "no_such_user", "password": PASS}
            )
        ).status_code == 401
    response = await client.post(
        "/api/auth/login", json={"username": "no_such_user", "password": PASS}
    )
    assert response.status_code == 429 and response.headers["retry-after"] == "60"


async def test_unconfigured_auth_keeps_guest_and_health_available(tmp_path):
    app = create_app(Settings(_env_file=None, upload_storage_dir=tmp_path / "guest"))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        assert (await client.get("/api/health")).status_code == 200
        assert (await client.get("/api/rooms/input-limits")).status_code == 200
        assert (await client.get("/api/auth/me")).json() == {
            "user": None,
            "expires_at": None,
        }
        assert (
            await client.post(
                "/api/auth/register",
                json={"username": "test_user", "password": PASS},
            )
        ).status_code == 503


async def test_missing_schema_is_a_safe_503_not_an_implicit_migration(tmp_path):
    path = tmp_path / "not-migrated.db"
    app = create_app(
        Settings(
            _env_file=None,
            database_url=f"sqlite+pysqlite:///{path}",
            jwt_secret_key=KEY,
            upload_storage_dir=tmp_path / "guest",
        )
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        response = await client.post(
            "/api/auth/register", json={"username": "test_user", "password": PASS}
        )
        assert (
            response.status_code == 503
            and "sqlite" not in response.text
            and PASS not in response.text
        )
        assert not inspect(app.state.database.engine).has_table("users")


async def test_secure_cookie_configuration_and_migrations(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'secure.db'}"
    config = migrate(url)
    command.upgrade(config, "head")
    command.check(config)
    settings = Settings(
        _env_file=None,
        app_environment="production",
        database_url=url,
        jwt_secret_key=KEY,
        auth_cookie_secure=True,
        auth_cookie_samesite="none",
        upload_storage_dir=tmp_path / "photos",
    )
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://roometry.test"
        ) as client,
    ):
        cookie = (await signup(client)).headers["set-cookie"].lower()
        assert "secure" in cookie and "httponly" in cookie and "samesite=none" in cookie
    command.downgrade(config, "base")
    db = Database(url)
    assert not inspect(db.engine).has_table("users")
    db.close()


@pytest.mark.parametrize(
    "options",
    [
        {"jwt_secret_key": "short"},
        {"auth_cookie_samesite": "none"},
        {
            "app_environment": "production",
            "database_url": "postgresql://localhost/test",
            "jwt_secret_key": KEY,
        },
        {
            "database_url": "postgresql://localhost/test",
            "jwt_secret_key": KEY,
            "cors_origins": ["*"],
        },
        {"jwt_algorithm": "none"},
    ],
)
def test_insecure_auth_configuration_is_rejected(options):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(_env_file=None, **options)
