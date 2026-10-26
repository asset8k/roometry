"""Cross-user, revocation, deletion and crash recovery audit; no external requests."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from starlette.requests import Request

from roometry.agents.access import stream_reader
from roometry.agents.persistence import seed_project
from roometry.agents.persistence_model import RuntimeInput, RuntimeRun
from roometry.main import create_app
from roometry.progress import progress_response
from tests import test_projects, test_vision_api
from tests.test_agent_clarification import measurements
from tests.test_agent_tools import decision, photo
from tests.test_auth import PASS
from tests.test_projects import saved
from tests.test_registered_recovery import paused, setup
from tests.test_run_progress import Connected, collect, messages
from tests.test_scene_proposals import scene_action

anyio_backend = test_vision_api.anyio_backend
isolated_environment = test_vision_api.isolated_environment
harness = test_vision_api.harness
room = test_projects.room
pytestmark = pytest.mark.anyio


@pytest.fixture
async def registered_room(harness, tmp_path):
    app, settings, sdk, calls, actions = await setup(harness, tmp_path)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        state, owner, request = await paused(app, client, actions)
        actions.extend([decision("propose_scene"), scene_action(), decision("finish")])
        complete = await client.post(
            f"/api/agent-runs/{state['run_id']}/answer", json=measurements()
        )
        assert (
            complete.status_code == 200 and complete.json()["status"] == "completed"
        ), complete.text
        body = {
            "request_id": str(uuid4()),
            "source_run_id": state["run_id"],
            "title": "Private audit room",
            "manual_copies": [
                {"parent_run_id": None, "scene": complete.json()["working_scene"]}
            ],
            "view": "editing",
            "messages": [
                {"id": "private", "role": "user", "text": "PRIVATE_AUDIT_CHAT"}
            ],
            "editing_parent_run_id": None,
            "selected_improvement_id": None,
        }
        receipt = await client.post("/api/projects", json=body)
        assert receipt.status_code == 200, receipt.text
        child_request = {"request_id": str(uuid4()), "focus": "aesthetics"}
        base = f"/api/agent-runs/{state['run_id']}/optimizations"
        child = await client.post(base, json=child_request)
        assert child.status_code == 200, child.text
        yield SimpleNamespace(
            app=app,
            client=client,
            settings=settings,
            sdk=sdk,
            calls=calls,
            actions=actions,
            state=complete.json(),
            owner=owner,
            request=request,
            body=body,
            project=receipt.json(),
            child=child.json(),
            child_request=child_request,
        )


async def test_owner_matrix_rejects_other_accounts_and_revoked_sessions(
    registered_room,
):
    r = registered_room
    root = f"/api/projects/{r.project['id']}"
    photo = (await r.client.get(root)).json()["images"][0]["url"]
    run = f"/api/agent-runs/{r.state['run_id']}"
    child = f"{run}/optimizations/{r.child['run_id']}"
    operations = [
        ("GET", root, None),
        ("GET", photo, None),
        ("POST", root + "/open", {}),
        ("PATCH", root, {"title": "Stolen", "expected_revision": 1}),
        ("DELETE", root, {"expected_revision": 1}),
        (
            "POST",
            "/api/projects",
            {**r.body, "project_id": r.project["id"], "expected_revision": 1},
        ),
        ("GET", run, None),
        ("GET", run + "/events", None),
        ("POST", run + "/cancel", {}),
        ("POST", run + "/answer", measurements()),
        ("GET", f"/api/agent-runs?request_id={r.request['request_id']}", None),
        ("GET", f"/api/projects/runs/{r.state['run_id']}", None),
        ("GET", child, None),
        ("GET", child + "/events", None),
        ("POST", child + "/execute", {}),
        ("POST", child + "/cancel", {}),
        ("POST", run + "/optimizations", r.child_request),
    ]
    count = len(r.calls)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=r.app), base_url="http://testserver"
    ) as other:
        assert (
            await other.post(
                "/api/auth/register",
                json={"username": "audit_outsider", "password": PASS},
            )
        ).status_code == 201
        for method, path, body in operations:
            response = await other.request(
                method, path, **({"json": body} if body is not None else {})
            )
            assert response.status_code == 404, (method, path, response.text)
            assert response.headers["cache-control"] == "no-store"
            assert "PRIVATE_AUDIT_CHAT" not in response.text
        assert (await other.get("/api/projects")).json() == []
        assert (await other.get("/api/projects/runs")).json() == []
    token = r.client.cookies["roometry_auth"]
    assert (await r.client.post("/api/auth/logout", json={})).status_code == 204
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=r.app),
        base_url="http://testserver",
        cookies={"roometry_auth": token},
    ) as revoked:
        for method, path, body in operations:
            response = await revoked.request(
                method, path, **({"json": body} if body is not None else {})
            )
            assert response.status_code == 401, (method, path, response.text)
    assert len(r.calls) == count


async def test_run_kinds_and_nested_parent_ids_cannot_be_substituted(registered_room):
    r = registered_room
    source, candidate = r.state["run_id"], r.child["run_id"]
    for path in [
        f"/api/agent-runs/{candidate}",
        f"/api/projects/runs/{candidate}",
        f"/api/agent-runs/{source}/optimizations/{source}",
        f"/api/agent-runs/{uuid4()}/optimizations/{candidate}",
    ]:
        response = await r.client.get(path)
        assert response.status_code == 404, (path, response.text)
    assert len(r.calls) == 6


async def test_explicit_upload_reuse_isolates_analysis_between_accounts(
    registered_room,
):
    r = registered_room
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=r.app),
        base_url="http://testserver",
        cookies={
            "roometry_upload_session": r.client.cookies["roometry_upload_session"]
        },
    ) as other:
        assert (
            await other.post(
                "/api/auth/register",
                json={"username": "upload_reuser", "password": PASS},
            )
        ).status_code == 201
        r.actions.extend([decision("analyze_photos"), photo, decision("propose_scene")])
        response = await other.post(
            "/api/agent-runs",
            json={"input_id": r.state["input_id"], "request_id": str(uuid4())},
        )
        assert (
            response.status_code == 200
            and response.json()["status"] == "awaiting_answer"
        ), response.text
        assert (
            response.json()["analysis"]["analysis_id"]
            != r.state["analysis"]["analysis_id"]
        )
        assert len(r.calls) == 9
    assert (await r.client.get(f"/api/agent-runs/{r.state['run_id']}")).json()[
        "analysis"
    ] == r.state["analysis"]


async def test_all_project_mutations_reject_foreign_origins(registered_room):
    r = registered_room
    root = f"/api/projects/{r.project['id']}"
    for method, path, body in [
        ("POST", root + "/open", {}),
        ("PATCH", root, {"title": "Changed", "expected_revision": 1}),
        ("DELETE", root, {"expected_revision": 1}),
    ]:
        for headers in [
            {"origin": "https://untrusted.example"},
            {"sec-fetch-site": "cross-site"},
        ]:
            result = await r.client.request(method, path, json=body, headers=headers)
            assert result.status_code == 403, (method, path, result.text)
    assert (await r.client.get(root)).json()["revision"] == 1
    assert len(r.calls) == 6


async def test_delete_revokes_cached_runs_candidates_checkpoints_and_photos(
    registered_room,
):
    r = registered_room
    root = f"/api/projects/{r.project['id']}"
    photo = (await r.client.get(root)).json()["images"][0]["url"]
    assert (await r.client.post(root + "/open", json={})).status_code == 200
    source = f"/api/agent-runs/{r.state['run_id']}"
    child = source + f"/optimizations/{r.child['run_id']}"
    assert (await r.client.get(child)).status_code == 200  # Cache before revocation.
    assert (
        await r.client.request("DELETE", root, json={"expected_revision": 1})
    ).status_code == 204
    for path in [root, photo, source, source + "/events", child, child + "/events"]:
        assert (await r.client.get(path)).status_code == 404
    assert (await r.client.post(child + "/execute", json={})).status_code == 404
    assert (await r.client.post(root + "/open", json={})).status_code == 404
    assert not list(r.settings.project_storage_dir.rglob("*.jpg"))
    assert len(r.calls) == 6


async def test_saved_photos_survive_runtime_expiry_and_reopen_without_models(
    registered_room,
):
    r = registered_room
    future = r.app.state.agent_runs.now() + timedelta(days=8)
    r.app.state.agent_runs.now = r.app.state.registered_runs.now = lambda: future
    r.app.state.registered_runs.cleanup()
    root = f"/api/projects/{r.project['id']}"
    detail = (await r.client.get(root)).json()
    photo = await r.client.get(detail["images"][0]["url"])
    assert (
        photo.status_code == 200
        and photo.headers["cache-control"] == "private, no-store"
    )
    opened = await r.client.post(root + "/open", json={})
    assert opened.status_code == 200, opened.text
    state = opened.json()["run"]
    assert state["working_scene"] == r.state["working_scene"]
    assert (
        state["input_id"] != r.state["input_id"] and state["persistence"] == "account"
    )
    repeated = await r.client.post(root + "/open", json={})
    assert (
        repeated.status_code == 200
        and repeated.json()["run"]["input_id"] == state["input_id"]
    )
    assert len(r.calls) == 6


async def test_reseeding_failure_rolls_back_and_stale_open_cannot_replace_state(
    room, monkeypatch
):
    app, client, _, _, _ = room
    receipt = await saved(room)
    root = f"/api/projects/{receipt['id']}"
    detail = (await client.get(root)).json()
    original = app.state.registered_runs.put

    def broken(*args, **kwargs):
        raise OperationalError("synthetic", {}, Exception("PRIVATE_DB_DETAIL"))

    monkeypatch.setattr(app.state.registered_runs, "put", broken)
    response = await client.post(root + "/open", json={})
    assert response.status_code == 503 and "PRIVATE_DB_DETAIL" not in response.text
    with app.state.database.sessions() as db:
        assert db.scalar(select(func.count()).select_from(RuntimeInput)) == 0
        assert db.scalar(select(func.count()).select_from(RuntimeRun)) == 0
    monkeypatch.setattr(app.state.registered_runs, "put", original)
    assert (
        await client.patch(root, json={"title": "Latest room", "expected_revision": 1})
    ).status_code == 200
    user = UUID((await client.get("/api/auth/me")).json()["user"]["id"])
    from roometry.auth.errors import AuthFailure

    with pytest.raises(AuthFailure) as error:
        seed_project(app.state.registered_runs, user, detail)
    assert error.value.status == 409
    opened = await client.post(root + "/open", json={})
    assert (
        opened.status_code == 200 and opened.json()["project"]["title"] == "Latest room"
    )


async def test_progress_connection_stops_on_logout_before_replay_or_next_event(
    harness, tmp_path
):
    app, _, _, calls, actions = await setup(harness, tmp_path)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        state, owner, _ = await paused(app, client, actions)
        request = Request(
            {
                "type": "http",
                "headers": [
                    (
                        b"cookie",
                        f"roometry_auth={client.cookies['roometry_auth']}".encode(),
                    )
                ],
                "app": app,
            }
        )
        read = stream_reader(
            request, owner, lambda: app.state.agent_runs.read(state["run_id"], owner)
        )
        _, log = await app.state.agent_runs.observe(state["run_id"], owner)
        response = progress_response(Connected(), log, read, recheck_seconds=0.001)
        iterator = response.body_iterator
        assert "retry:" in await anext(iterator)
        assert messages(await anext(iterator))
        assert (await client.post("/api/auth/logout", json={})).status_code == 204
        assert "event: unavailable" in "".join([chunk async for chunk in iterator])
        assert log.subscribers == 0
        replay = await collect(
            progress_response(Connected(), log, read, cursor=log.events[0].cursor)
        )
        assert "event: unavailable" in replay and not messages(replay)
        assert len(calls) == 3


async def test_cancelled_question_stays_cancelled_after_restart(harness, tmp_path):
    app, settings, sdk, calls, actions = await setup(harness, tmp_path)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        state, _, request = await paused(app, client, actions)
        root = f"/api/agent-runs/{state['run_id']}"
        cancelled = await client.post(root + "/cancel", json={})
        assert (
            cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
        )
        restarted = create_app(settings, analysis_client=sdk)
        async with (
            restarted.router.lifespan_context(restarted),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=restarted),
                base_url="http://testserver",
                cookies={"roometry_auth": client.cookies["roometry_auth"]},
            ) as again,
        ):
            assert (await again.get(root)).json() == cancelled.json()
            assert (
                await again.post("/api/agent-runs", json=request)
            ).json() == cancelled.json()
            assert (
                await again.post(root + "/answer", json=measurements())
            ).status_code == 409
            assert (
                await again.post(root + "/cancel", json={})
            ).json() == cancelled.json()
            assert len(calls) == 3


@pytest.mark.parametrize("cancel", [False, True])
async def test_recovered_optimization_rechecks_cached_lease_until_terminal(
    registered_room, cancel
):
    r = registered_room
    account, run_id = UUID(r.owner.split(":")[1]), r.child["run_id"]
    now = datetime.now(UTC)
    with r.app.state.database.sessions.begin() as db:
        row = db.get(RuntimeRun, (account, run_id))
        data = {**row.data, "execution_started": True}
        data["result"] = {**data["result"], "status": "running", "stage": "proposing"}
        row.data = data
        row.execution_owner = str(uuid4())
        row.lease_until = now + timedelta(seconds=30)
        row.cancel_requested = cancel
        row.usage = {**r.child["usage"], "model_calls": 1, "unknown_usage_calls": 1}
    restarted = create_app(r.settings, analysis_client=r.sdk)
    async with (
        restarted.router.lifespan_context(restarted),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted),
            base_url="http://testserver",
            cookies={"roometry_auth": r.client.cookies["roometry_auth"]},
        ) as client,
    ):
        path = f"/api/agent-runs/{r.state['run_id']}/optimizations/{run_id}"
        first = await client.get(path)
        assert first.status_code == 200 and first.json()["status"] == "running"
        restarted.state.agent_runs.now = lambda: now + timedelta(seconds=31)
        stopped = await client.get(path)
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()["status"] == ("cancelled" if cancel else "failed")
        assert stopped.json()["usage"]["unknown_usage_calls"] == 1
        retry = await client.post(path + "/execute", json={})
        assert retry.status_code == 200 and retry.json() == stopped.json()
        assert len(r.calls) == 6
