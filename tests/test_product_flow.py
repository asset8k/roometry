"""The product journey shares one quota ledger across private work and publishing."""

from uuid import uuid4

import httpx
import pytest

from roometry.main import create_app
from tests import test_quota as quota
from tests.test_auth import PASS
from tests.test_chat_catalog_attachments import tv
from tests.test_optimization_execution import proposal, provider_action, review
from tests.test_optimization_redesign import patch
from tests.test_scene_proposals import scene_action

anyio_backend = quota.anyio_backend
isolated_environment = quota.isolated_environment
harness = quota.harness
quota_app = quota.quota_app
pytestmark = pytest.mark.anyio


async def test_questions_improvement_tv_chat_save_publish_revoke_and_restart_share_one_allowance(
    quota_app,
):
    app, client, settings, sdk, calls, actions = quota_app
    assert (
        await client.post(
            "/api/auth/register",
            json={"username": "product_flow_user", "password": PASS},
        )
    ).status_code == 201
    state, start = await quota.room(quota_app, complete=False)
    assert state["status"] == "awaiting_answer"
    initial_calls = len(calls)
    actions.append(scene_action())
    answer = {
        "question_id": state["pending_question"]["id"],
        "choice_id": "enter_measurements",
        "dimensions": {"length_m": 4.0, "height_m": 2.6},
    }
    path = f"/api/agent-runs/{state['run_id']}"
    reply = await client.post(path + "/answer", json=answer)
    assert reply.status_code == 200, reply.text
    state = reply.json()
    assert state["status"] == "completed" and len(calls) == initial_calls + 1
    assert (await client.post(path + "/answer", json=answer)).json() == state
    assert (await client.post("/api/agent-runs", json=start)).json() == state
    usage_url = f"/api/usage?run_id={state['run_id']}"
    assert (await client.get(usage_url)).json()["actions_remaining"] == 2
    url = path + "/optimizations"
    runs = []
    requests = []
    current = state["working_scene"]
    for n in range(2):
        body = {"request_id": str(uuid4()), "focus": "aesthetics"}
        if n:
            body.update(
                instruction="Add a wall-mounted TV on the south wall.",
                context={"scene": current, "parent_run_id": runs[-1]["run_id"]},
            )
            planned = patch(
                additions=[
                    tv(
                        "wall",
                        wall="south",
                        elevation_m=1.4,
                        position={"x": 2.5, "z": 2.0},
                    )
                ]
            ).model_dump(mode="json")
        else:
            planned = proposal()
        actions.extend([provider_action(planned), review()])
        before = len(calls)
        prepared = await client.post(url, json=body)
        assert prepared.status_code == 200 and len(calls) == before
        executed = await client.post(
            f"{url}/{prepared.json()['run_id']}/execute", json={}
        )
        assert (
            executed.status_code == 200 and executed.json()["status"] == "completed"
        ), executed.text
        current = executed.json()["result_scene"]
        runs.append(executed.json())
        requests.append(body)
        assert (
            await client.post(f"{url}/{runs[-1]['run_id']}/execute", json={})
        ).json() == runs[-1]
        assert len(calls) == before + 2
    assert (
        next(o for o in current["objects"] if o["id"] == "new_tv")["placement"]
        == "wall"
    )
    paid = len(calls)
    assert (await client.get(usage_url)).json()["actions_remaining"] == 0
    blocked = (
        await client.post(url, json={**requests[-1], "request_id": str(uuid4())})
    ).json()
    rejection = await client.post(f"{url}/{blocked['run_id']}/execute", json={})
    assert (
        rejection.status_code == 429
        and rejection.json()["detail"][0]["code"] == "session_action_limit"
    )
    stream = await client.get(path + "/events")
    assert stream.status_code == 200 and "event: snapshot" in stream.text
    edited = await client.post(
        "/api/scenes/preview-edit",
        json={
            "scene": current,
            "edit": {
                "operation": "move",
                "object_id": "desk_observed",
                "position": {"x": 2.95, "z": 0.6},
            },
        },
    )
    assert edited.status_code == 200 and edited.json()["accepted"], edited.text
    current = edited.json()["scene"]
    body = {
        "request_id": str(uuid4()),
        "source_run_id": state["run_id"],
        "title": "Private account room",
        "improvement_run_ids": [r["run_id"] for r in runs],
        "manual_copies": [
            {"parent_run_id": None, "scene": state["working_scene"]},
            {"parent_run_id": runs[-1]["run_id"], "scene": current},
        ],
        "has_editable_copy": True,
        "view": "editing",
        "editing_parent_run_id": runs[-1]["run_id"],
        "selected_improvement_id": runs[-1]["run_id"],
        "messages": [
            {"id": "private_note", "role": "user", "text": "PRIVATE_CHAT_MARKER"}
        ],
    }
    saved = await client.post("/api/projects", json=body)
    assert saved.status_code == 200, saved.text
    project_id = saved.json()["id"]
    assert (await client.get("/api/feed")).json()["items"] == []
    options = (
        await client.post(
            "/api/projects/sharing-options",
            json={"source_run_id": state["run_id"], "scene": current},
        )
    ).json()
    pub_body = {
        "version_id": options["selected_version_id"],
        "expected_revision": options["revision"],
        "expected_publication_revision": 0,
        "title": "Shared test room",
        "summary": "Synthetic fixture.",
        "consent": True,
    }
    published = await client.post(
        f"/api/projects/{project_id}/publication", json=pub_body
    )
    assert published.status_code == 200, published.text
    public_id = published.json()["id"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as guest:
        public = await guest.get(f"/api/feed/{public_id}")
        assert public.status_code == 200 and "PRIVATE_CHAT_MARKER" not in public.text
        assert any(o["type"] == "tv" for o in public.json()["scene"]["objects"])
        assert (await guest.get(public.json()["preview_url"])).content.startswith(
            b"\x89PNG"
        )
        assert (await guest.get(f"/api/projects/{project_id}")).status_code == 401
    assert (
        await client.request(
            "DELETE",
            f"/api/projects/{project_id}/publication",
            json={"public_id": public_id, "expected_publication_revision": 1},
        )
    ).status_code == 204
    assert (await client.get(f"/api/feed/{public_id}")).status_code == 404
    restarted = create_app(settings, analysis_client=sdk)
    async with (
        restarted.router.lifespan_context(restarted),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted),
            base_url="http://testserver",
            cookies={"roometry_auth": client.cookies["roometry_auth"]},
        ) as again,
    ):
        opened = await again.post(f"/api/projects/{project_id}/open", json={})
        assert opened.status_code == 200, opened.text
        usage = (await again.get(usage_url)).json()
        assert usage["actions_remaining"] == 0 and usage["sessions_remaining"] == 2
        assert (await again.get(f"/api/feed/{public_id}/preview")).status_code == 404
    assert len(calls) == paid
