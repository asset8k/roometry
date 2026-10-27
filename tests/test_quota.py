"""Durable product allowances over real SQL/HTTP/graphs; all model HTTP is mocked."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError
from starlette.requests import Request

from roometry.config import Settings
from roometry.database import Database
from roometry.main import create_app
from roometry.quota.service import QuotaFailure, Quotas
from roometry.uploads.session import owner_scope, session
from tests import test_vision_api as api
from tests.test_agent_tools import photo, prepared
from tests.test_auth import KEY, PASS, migrate
from tests.test_optimization_execution import change, proposal, provider_action, review
from tests.test_registered_recovery import setup
from tests.test_scene_proposals import scene_action

anyio_backend = api.anyio_backend
isolated_environment = api.isolated_environment
harness = api.harness
pytestmark = pytest.mark.anyio


@pytest.fixture
def ledger(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'quota.db'}"
    migrate(url)
    settings = Settings(
        _env_file=None,
        database_url=url,
        jwt_secret_key=KEY,
        generation_quota_enabled=True,
    )
    database = Database(url)
    clock = [datetime(2026, 10, 27, 23, 59, tzinfo=UTC)]
    service = Quotas(database, settings, lambda: clock[0])
    yield service, clock, settings, database
    database.close()


def test_three_rooms_with_three_actions_each_and_durable_idempotent_receipts(ledger):
    service, _, settings, database = ledger
    for n in range(3):
        room = str(uuid4())
        service.reserve("account:alice", room, room, initial=True)
        service.started(room)
        for _ in range(2):
            action = str(uuid4())
            service.reserve("account:alice", room, action)
            service.reserve("account:alice", room, action)
            service.started(action)
            service.release_unused(action)
        assert service.status("account:alice", room)["actions_remaining"] == 0
        with pytest.raises(QuotaFailure) as error:
            service.reserve("account:alice", room, str(uuid4()))
        assert error.value.code == "session_action_limit"
        assert service.status("account:alice")["sessions_remaining"] == 2 - n
    fresh = Quotas(database, settings, service.now)
    with pytest.raises(QuotaFailure) as error:
        fresh.reserve("account:alice", str(uuid4()), str(uuid4()), initial=True)
    assert error.value.code == "daily_session_limit" and error.value.retry_after == 60
    assert fresh.status("account:bob")["sessions_remaining"] == 3


def test_reset_only_refreshes_daily_sessions_not_existing_room_actions(ledger):
    service, clock, _, _ = ledger
    room = str(uuid4())
    service.reserve("guest", room, room, initial=True)
    service.started(room)
    for _ in range(2):
        action = str(uuid4())
        service.reserve("guest", room, action)
        service.started(action)
    clock[0] += timedelta(minutes=2)
    assert service.status("guest", room)["sessions_remaining"] == 3
    assert service.status("guest", room)["actions_remaining"] == 0
    with pytest.raises(QuotaFailure):
        service.reserve("guest", room, str(uuid4()))
    # Importing/saving the same root under an account cannot reset its action cap.
    with pytest.raises(QuotaFailure):
        service.reserve("account:alice", room, str(uuid4()))


def test_release_before_dispatch_refunds_but_started_or_ambiguous_calls_count(ledger):
    service, _, _, _ = ledger
    room = str(uuid4())
    service.reserve("guest", room, room, initial=True)
    service.release_unused(room)
    service.release_unused(room)
    assert service.status("guest")["sessions_remaining"] == 3
    service.reserve("guest", room, room, initial=True)
    service.started(room)
    action = str(uuid4())
    service.reserve("guest", room, action)
    service.release_unused(action)
    assert service.status("guest", room)["actions_remaining"] == 2
    service.release_unused(room)
    assert service.status("guest")["sessions_remaining"] == 2


def test_concurrent_workers_cannot_exceed_daily_or_room_allowances(ledger):
    service, _, settings, database = ledger
    second = Quotas(database, settings, service.now)

    def reserve(index):
        room = str(uuid4())
        try:
            (service if index % 2 else second).reserve(
                "guest", room, room, initial=True
            )
            return room
        except QuotaFailure:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        rooms = [r for r in pool.map(reserve, range(12)) if r]
    assert len(rooms) == 3
    room = rooms[0]

    def refine(index):
        try:
            (service if index % 2 else second).reserve("guest", room, str(uuid4()))
            return True
        except QuotaFailure:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(refine, range(12))) == 2
    assert service.status("guest", room)["actions_remaining"] == 0


def test_environment_defaults_explicit_override_and_disabled_mode(ledger):
    assert not Settings(_env_file=None).quota_enabled
    assert not Settings(_env_file=None, app_environment="test").quota_enabled
    production = Settings(
        _env_file=None,
        app_environment="production",
        database_url="sqlite://",
        jwt_secret_key=KEY,
        auth_cookie_secure=True,
    )
    assert production.quota_enabled
    assert not Settings(
        _env_file=None, app_environment="production", generation_quota_enabled=False
    ).quota_enabled
    with pytest.raises(ValidationError):
        Settings(_env_file=None, generation_quota_enabled=True)
    service, _, settings, _ = ledger
    settings.generation_quota_enabled = False
    for _ in range(12):
        service.reserve("guest", "same", str(uuid4()), initial=True)
    assert service.status("guest")["enabled"] is False
    assert service.status("guest")["sessions_remaining"] is None


def test_guest_signatures_are_verified_and_malformed_cookies_cannot_choose_identity(
    ledger,
):
    _, _, settings, _ = ledger
    app = SimpleNamespace(state=SimpleNamespace(settings=settings))

    def request(cookie=""):
        return Request(
            {
                "type": "http",
                "app": app,
                "headers": [
                    (b"cookie", ("roometry_upload_session=" + cookie).encode("latin1"))
                ],
            }
        )

    valid, identity = session(request())
    assert session(request(valid)) == (valid, identity)
    assert owner_scope(valid) == identity
    for cookie in ["A" * 43, "A" * 43 + "." + "0" * 64, "A" * 43 + ".é"]:
        replacement, owner = session(request(cookie))
        assert replacement != cookie and owner != owner_scope(cookie)


@pytest.fixture
async def quota_app(harness, tmp_path):
    app, settings, sdk, calls, actions = await setup(harness, tmp_path)
    settings.generation_quota_enabled = True
    settings.agent_deterministic_routing = True
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        yield app, client, settings, sdk, calls, actions


async def room(quota_app, complete=True):
    app, client, _, _, calls, actions = quota_app
    _, _, upload = await prepared((app, client, calls, actions), complete=complete)
    actions.extend([photo, *([scene_action()] if complete else [])])
    body = {"input_id": upload["input_id"], "request_id": str(uuid4())}
    response = await client.post("/api/agent-runs", json=body)
    assert response.status_code == 200, response.text
    return response.json(), body


async def test_http_room_and_improvements_share_three_actions_without_charging_reads_or_retries(
    quota_app,
):
    _, client, _, _, calls, actions = quota_app
    state, body = await room(quota_app)
    assert state["status"] == "completed" and len(calls) == 2
    assert (await client.post("/api/agent-runs", json=body)).json() == state
    usage_url = f"/api/usage?run_id={state['run_id']}"
    assert (await client.get(usage_url)).json()["actions_remaining"] == 2
    current, parent = state["working_scene"], None
    url = f"/api/agent-runs/{state['run_id']}/optimizations"
    for index in range(3):
        request = {
            "request_id": str(uuid4()),
            "focus": "storage" if index % 2 else "aesthetics",
            "instruction": "Change the bed color.",
            "context": {"scene": current, "parent_run_id": parent},
        }
        prepared_response = await client.post(url, json=request)
        assert prepared_response.status_code == 200, prepared_response.text
        run_id = prepared_response.json()["run_id"]
        before = len(calls)
        if index < 2:
            actions.extend(
                [
                    provider_action(
                        proposal([change(color="#445566" if index else "#746A5B")])
                    ),
                    review(),
                ]
            )
        result = await client.post(f"{url}/{run_id}/execute", json={})
        if index == 2:
            assert (
                result.status_code == 429
                and result.json()["detail"][0]["code"] == "session_action_limit"
            )
            assert len(calls) == before
        else:
            assert (
                result.status_code == 200 and result.json()["status"] == "completed"
            ), result.text
            assert len(calls) == before + 2
            assert (
                await client.post(f"{url}/{run_id}/execute", json={})
            ).json() == result.json()
            current, parent = result.json()["result_scene"], run_id
    usage = (await client.get(usage_url)).json()
    assert usage["actions_remaining"] == 0 and usage["sessions_remaining"] == 2
    assert len(calls) == 6
    assert (await client.get(f"/api/usage?run_id={uuid4()}")).status_code == 404
    assert (
        await client.post("/api/rooms/analyze", json={"input_id": state["input_id"]})
    ).status_code == 409
    assert len(calls) == 6


async def test_daily_http_limit_does_not_dispatch_provider_calls(quota_app):
    _, client, _, _, calls, _ = quota_app
    for _ in range(3):
        await room(quota_app)
    assert (await client.get("/api/usage")).json()["sessions_remaining"] == 0
    _, _, upload = await prepared((quota_app[0], client, None, None))
    before = len(calls)
    response = await client.post(
        "/api/agent-runs",
        json={"input_id": upload["input_id"], "request_id": str(uuid4())},
    )
    assert response.status_code == 429 and response.headers["Retry-After"]
    assert response.json()["detail"][0]["code"] == "daily_session_limit"
    assert len(calls) == before


async def test_clarification_resumes_one_session_without_spending_an_action(quota_app):
    _, client, _, _, calls, actions = quota_app
    state, _ = await room(quota_app, complete=False)
    assert state["status"] == "awaiting_answer" and len(calls) == 1
    actions.append(scene_action())
    answer = {
        "question_id": state["pending_question"]["id"],
        "choice_id": "enter_measurements",
        "dimensions": {"length_m": 4.0, "height_m": 2.6},
    }
    path = f"/api/agent-runs/{state['run_id']}/answer"
    response = await client.post(path, json=answer)
    assert response.status_code == 200 and response.json()["status"] == "completed", (
        response.text
    )
    await client.post(path, json=answer)
    usage = (await client.get(f"/api/usage?run_id={state['run_id']}")).json()
    assert (
        usage["sessions_remaining"] == 2
        and usage["actions_remaining"] == 2
        and len(calls) == 2
    )


async def test_account_usage_survives_cookie_changes_and_process_restart(quota_app):
    app, client, settings, sdk, calls, _ = quota_app
    assert (
        await client.post(
            "/api/auth/register", json={"username": "quota_user", "password": PASS}
        )
    ).status_code == 201
    state, _ = await room(quota_app)
    client.cookies.delete("roometry_upload_session")
    cookies = {"roometry_auth": client.cookies["roometry_auth"]}
    restarted = create_app(settings, analysis_client=sdk)
    async with (
        restarted.router.lifespan_context(restarted),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted),
            base_url="http://testserver",
            cookies=cookies,
        ) as other,
    ):
        result = await other.get(f"/api/usage?run_id={state['run_id']}")
        assert result.status_code == 200, result.text
        assert (
            result.json()["sessions_remaining"]
            == result.json()["actions_remaining"]
            == 2
        )
        assert len(calls) == 2


async def test_signed_guest_cookie_and_network_backstop(quota_app):
    _, client, settings, _, calls, _ = quota_app
    settings.ai_guest_sessions_per_ip_per_day = 2
    await room(quota_app)
    cookie = client.cookies["roometry_upload_session"]
    assert "." in cookie
    client.cookies.clear()
    await room(quota_app)
    client.cookies.clear()
    _, _, upload = await prepared((quota_app[0], client, None, None))
    response = await client.post(
        "/api/agent-runs",
        json={"input_id": upload["input_id"], "request_id": str(uuid4())},
    )
    assert (
        response.status_code == 429
        and response.json()["detail"][0]["code"] == "guest_network_limit"
    )
    assert len(calls) == 4


async def test_pre_dispatch_failure_refunds_session_but_paid_model_failure_does_not(
    quota_app,
):
    app, client, settings, _, calls, actions = quota_app
    settings.analysis_enabled = False
    _, _, upload = await prepared((app, client, None, None))
    response = await client.post(
        "/api/agent-runs",
        json={"input_id": upload["input_id"], "request_id": str(uuid4())},
    )
    assert response.status_code == 200 and response.json()["status"] == "failed", (
        response.text
    )
    assert (await client.get("/api/usage")).json()[
        "sessions_remaining"
    ] == 3 and not calls
    settings.analysis_enabled = True

    async def failed(request, payload):
        return httpx.Response(
            500, json={"error": {"message": "temporary provider failure"}}
        )

    actions.extend([failed, failed])
    response = await client.post(
        "/api/agent-runs",
        json={"input_id": upload["input_id"], "request_id": str(uuid4())},
    )
    assert response.status_code == 200 and response.json()["status"] == "failed", (
        response.text
    )
    assert (await client.get("/api/usage")).json()["sessions_remaining"] == 2 and len(
        calls
    ) == 2
