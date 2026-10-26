"""Real SQL checkpoints and LangGraph interrupts over mocked provider HTTP."""

from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select

from roometry.agents.graph import build_graph
from roometry.agents.persistence_model import GraphCheckpoint, GraphWrite, RuntimeRun
from roometry.config import Settings
from roometry.main import create_app
from tests import test_vision_api
from tests.test_agent_clarification import measurements
from tests.test_agent_tools import decision, photo, prepared
from tests.test_auth import KEY, PASS, migrate
from tests.test_scene_proposals import scene_action

anyio_backend = test_vision_api.anyio_backend
isolated_environment = test_vision_api.isolated_environment
harness = test_vision_api.harness
pytestmark = pytest.mark.anyio


async def setup(harness, tmp_path):
    original, _, calls, actions = harness
    url = f"sqlite+pysqlite:///{tmp_path / 'durable.db'}"
    migrate(url)
    settings = original.state.settings.model_copy(
        update={
            "database_url": url,
            "jwt_secret_key": KEY,
            "project_storage_dir": tmp_path / "private",
            "upload_storage_dir": tmp_path / "guest",
        }
    )
    # model_copy bypasses validators; construct SecretStr settings normally.
    settings = Settings(
        _env_file=None,
        **{
            **settings.model_dump(exclude={"database_url", "jwt_secret_key"}),
            "database_url": url,
            "jwt_secret_key": KEY,
        },
    )
    sdk = original.state.analysis_service.analyzer._client
    return create_app(settings, analysis_client=sdk), settings, sdk, calls, actions


async def paused(app, client, actions):
    response = await client.post(
        "/api/auth/register", json={"username": "durable_user", "password": PASS}
    )
    assert response.status_code == 201, response.text
    owner = f"account:{response.json()['user']['id']}"
    _, _, uploaded = await prepared((app, client, None, None), complete=False)
    actions.extend([decision("analyze_photos"), photo, decision("propose_scene")])
    request = {"input_id": uploaded["input_id"], "request_id": str(uuid4())}
    response = await client.post("/api/agent-runs", json=request)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "awaiting_answer", response.text
    return response.json(), owner, request


@pytest.mark.parametrize('use_estimates', [False, True])
async def test_registered_interrupt_and_photos_survive_fresh_app_without_replaying_analysis(
    harness, tmp_path, use_estimates
):
    app, settings, sdk, calls, actions = await setup(harness, tmp_path)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        state, owner, request = await paused(app, client, actions)
        assert state["persistence"] == "account" and len(calls) == 3
        with app.state.database.sessions() as db:
            assert db.scalar(select(func.count()).select_from(GraphCheckpoint)) > 0
            assert db.scalar(select(func.count()).select_from(GraphWrite)) > 0
        restarted = create_app(settings, analysis_client=sdk)
        cookies = {"roometry_auth": client.cookies["roometry_auth"]}
        async with (
            restarted.router.lifespan_context(restarted),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=restarted),
                base_url="http://testserver",
                cookies=cookies,
            ) as again,
        ):
            read = await again.get(f"/api/agent-runs/{state['run_id']}")
            assert read.status_code == 200, read.text
            assert read.json() == state
            recovered = await again.get(f"/api/projects/runs/{state['run_id']}")
            assert (
                recovered.status_code == 200
                and recovered.json()["input"]["input_id"] == state["input_id"]
            )
            entry = restarted.state.agent_runs.checkpoints.get(
                state["run_id"], owner, restarted.state.agent_runs.now()
            )
            snapshot = await build_graph(entry.saver).aget_state(
                restarted.state.agent_runs._config(entry)
            )
            assert snapshot.interrupts and snapshot.next == ("clarify",)
            assert (await again.post("/api/agent-runs", json=request)).json() == state
            assert len(calls) == 3
            actions.extend(
                [decision("propose_scene"), scene_action(), decision("finish")]
            )
            answer = {'question_id': 'q1', 'choice_id': 'leave_unknown'} if use_estimates else measurements()
            result = await again.post(
                f"/api/agent-runs/{state['run_id']}/answer", json=answer
            )
            assert result.status_code == 200, result.text
            assert result.json()["status"] == "completed", result.text
            assert result.json()["analysis"] == state["analysis"]
            assert len(calls) == 6
            assert result.json()['proposal']['assumed_axes'] == (['length_m', 'height_m'] if use_estimates else [])
            assert (
                await again.post(
                    f"/api/agent-runs/{state['run_id']}/answer", json=answer
                )
            ).json() == result.json()
            assert len(calls) == 6


async def test_registered_run_is_not_owned_by_its_old_guest_cookie(harness, tmp_path):
    app, _, _, calls, actions = await setup(harness, tmp_path)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        state, _owner, _ = await paused(app, client, actions)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            cookies={
                "roometry_upload_session": client.cookies["roometry_upload_session"]
            },
        ) as outsider:
            assert (
                await outsider.get(f"/api/agent-runs/{state['run_id']}")
            ).status_code == 404
            assert (await outsider.get("/api/projects/runs")).status_code == 401
            assert (
                await outsider.post(
                    "/api/auth/register",
                    json={"username": "other_durable", "password": PASS},
                )
            ).status_code == 201
            assert (
                await outsider.get(f"/api/projects/runs/{state['run_id']}")
            ).status_code == 404
            assert (await outsider.get("/api/projects/runs")).json() == []
        assert len(calls) == 3


async def test_registered_optimization_and_saved_room_reopen_do_not_replay_paid_calls(
    harness, tmp_path
):
    from tests.test_optimization_execution import proposal, provider_action, review

    app, settings, sdk, calls, actions = await setup(harness, tmp_path)
    app.state.optimization_runs.catalog = None
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        state, _owner, _ = await paused(app, client, actions)
        actions.extend([decision("propose_scene"), scene_action(), decision("finish")])
        response = await client.post(
            f"/api/agent-runs/{state['run_id']}/answer", json=measurements()
        )
        assert response.status_code == 200, response.text
        complete = response.json()
        before = len(calls)
        request = {"request_id": str(uuid4()), "focus": "aesthetics"}
        child = await client.post(
            f"/api/agent-runs/{state['run_id']}/optimizations", json=request
        )
        assert child.status_code == 200, child.text
        actions.extend([provider_action(proposal()), review()])
        url = f"/api/agent-runs/{state['run_id']}/optimizations/{child.json()['run_id']}/execute"
        response = await client.post(url, json={})
        assert (
            response.status_code == 200 and response.json()["status"] == "completed"
        ), response.text
        count = len(calls)
        assert count == before + 2
        saved_body = {
            "request_id": str(uuid4()),
            "source_run_id": state["run_id"],
            "title": "Recovered workspace",
            "improvement_run_ids": [child.json()["run_id"]],
            "manual_copies": [
                {"parent_run_id": None, "scene": complete["working_scene"]},
                {
                    "parent_run_id": child.json()["run_id"],
                    "scene": response.json()["result_scene"],
                },
            ],
            "view": "improved",
            "editing_parent_run_id": None,
            "selected_improvement_id": child.json()["run_id"],
            "messages": [{"id": "one", "role": "user", "text": "Use warmer colors."}],
        }
        saved = await client.post("/api/projects", json=saved_body)
        assert saved.status_code == 200, saved.text
        restarted = create_app(settings, analysis_client=sdk)
        async with (
            restarted.router.lifespan_context(restarted),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=restarted),
                base_url="http://testserver",
                cookies={"roometry_auth": client.cookies["roometry_auth"]},
            ) as again,
        ):
            retained = await again.post(url, json={})
            assert retained.status_code == 200 and retained.json() == response.json(), (
                retained.text
            )
            assert len(calls) == count
            opened = await again.post(
                f"/api/projects/{saved.json()['id']}/open", json={}
            )
            assert (
                opened.status_code == 200
                and opened.json()["run"]["status"] == "completed"
            ), opened.text
            assert (
                opened.json()["project"]["snapshot"]["messages"]
                == saved_body["messages"]
            )
            assert (
                opened.json()["project"]["snapshot"]["comparison"]["view"] == "improved"
            )
            assert len(calls) == count


async def test_ambiguous_inflight_call_is_not_replayed_after_restart(harness, tmp_path):
    from datetime import UTC, datetime

    app, settings, sdk, calls, actions = await setup(harness, tmp_path)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        state, owner, request = await paused(app, client, actions)
        entry = app.state.agent_runs.checkpoints.get(
            state["run_id"], owner, app.state.agent_runs.now()
        )
        graph = build_graph(entry.saver)
        await graph.aupdate_state(
            app.state.agent_runs._config(entry),
            {
                "status": "running",
                "pending_question": None,
                "pending_tool": {
                    "call_id": "uncertain_paid_call",
                    "name": "propose_scene",
                    "arguments": {},
                },
            },
            as_node="advance",
        )
        with app.state.database.sessions.begin() as db:
            row = db.get(RuntimeRun, (UUID(owner.split(":")[1]), state["run_id"]))
            row.execution_owner = str(uuid4())
            row.lease_until = datetime.now(UTC) - timedelta(seconds=1)
            row.usage = {**state["usage"], "model_calls": 4, "unknown_usage_calls": 1}
        restarted = create_app(settings, analysis_client=sdk)
        async with (
            restarted.router.lifespan_context(restarted),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=restarted),
                base_url="http://testserver",
                cookies={"roometry_auth": client.cookies["roometry_auth"]},
            ) as again,
        ):
            response = await again.get(f"/api/agent-runs/{state['run_id']}")
            assert response.status_code == 200, response.text
            assert (
                response.json()["status"] == "failed"
                and response.json()["usage"]["unknown_usage_calls"] == 1
            )
            assert (
                await again.post("/api/agent-runs", json=request)
            ).json() == response.json()
            assert (
                await again.post(
                    f"/api/agent-runs/{state['run_id']}/answer", json=measurements()
                )
            ).status_code == 409
            assert len(calls) == 3


async def test_expired_registered_runs_remove_checkpoint_and_private_photo_bytes(
    harness, tmp_path
):
    app, _, _, calls, actions = await setup(harness, tmp_path)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        state, _owner, _ = await paused(app, client, actions)
        registered = app.state.registered_runs
        future = registered.now() + timedelta(days=8)
        registered.now = lambda: future
        assert (
            await client.get(f"/api/agent-runs/{state['run_id']}")
        ).status_code == 404
        registered.cleanup()
        with app.state.database.sessions() as db:
            assert db.scalar(select(func.count()).select_from(RuntimeRun)) == 0
            assert db.scalar(select(func.count()).select_from(GraphCheckpoint)) == 0
            assert db.scalar(select(func.count()).select_from(GraphWrite)) == 0
        assert not list(app.state.settings.project_storage_dir.rglob("*.jpg"))
        assert len(calls) == 3
