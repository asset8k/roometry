"""Explicit saves preserve real uploaded bytes, scene edits and private account ownership."""

import copy
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from PIL import Image
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError

from roometry.assessment.metrics import measure_room
from roometry.auth.security import COOKIE_NAME as AUTH_COOKIE
from roometry.config import Settings
from roometry.main import create_app
from roometry.projects.model import Project, ProjectImage, SceneVersion
from roometry.uploads.session import COOKIE_NAME as GUEST_COOKIE
from roometry.uploads.session import owner_scope
from roometry.vision.schemas import RoomAnalysisResponse
from tests.test_auth import KEY, PASS, migrate

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def room(tmp_path, bedroom_scene):
    url = f"sqlite+pysqlite:///{tmp_path / 'projects.db'}"
    migrate(url)
    settings = Settings(
        _env_file=None,
        database_url=url,
        jwt_secret_key=KEY,
        app_environment="test",
        upload_storage_dir=tmp_path / "guest",
        project_storage_dir=tmp_path / "saved",
        auth_attempts_per_minute=120,
    )
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        assert (
            await client.post(
                "/api/auth/register",
                json={"username": "project_user", "password": PASS},
            )
        ).status_code == 201
        buffer = BytesIO()
        with Image.new("RGB", (256, 256), "gray") as image:
            image.save(buffer, "PNG")
        uploaded = (
            await client.post(
                "/api/rooms/inputs",
                files=[
                    ("options", (None, "{}")),
                    ("images", ("room.png", buffer.getvalue(), "image/png")),
                ],
            )
        ).json()
        owner = owner_scope(client.cookies[GUEST_COOKIE])
        initial = await app.state.agent_runs.create(uploaded["input_id"], owner)
        analysis = RoomAnalysisResponse.model_validate_json(
            (Path(__file__).parents[1] / "data/fixtures/room_analysis.json").read_text()
        )
        source = initial.model_copy(
            update={
                "status": "completed",
                "base_scene": bedroom_scene,
                "working_scene": bedroom_scene,
                "analysis": analysis,
                "room_assessment": measure_room(bedroom_scene),
            }
        )
        app.state.agent_runs.checkpoints.get(
            source.run_id, owner, app.state.agent_runs.now()
        ).initial = source
        body = {
            "request_id": str(uuid4()),
            "source_run_id": source.run_id,
            "title": "My private room",
            "manual_copies": [
                {"parent_run_id": None, "scene": bedroom_scene.model_dump(mode="json")}
            ],
            "view": "editing",
            "editing_parent_run_id": None,
            "selected_improvement_id": None,
            "messages": [
                {
                    "id": "private_request",
                    "role": "user",
                    "text": "Keep my desk near the window.",
                }
            ],
        }
        yield app, client, settings, source, body


def counts(app):
    with app.state.database.sessions() as db:
        return tuple(
            db.scalar(select(func.count()).select_from(model))
            for model in (Project, ProjectImage, SceneVersion)
        )


async def saved(room, body=None):
    _, client, _, _, default = room
    result = await client.post("/api/projects", json=body or default)
    assert result.status_code == 200, result.text
    return result.json()


async def test_private_save_preserves_source_chat_ratings_edits_and_photos_across_restart(
    room,
):
    app, client, settings, source, body = room
    editable = body["manual_copies"][0]["scene"]
    chair = next(
        o
        for o in editable["objects"]
        if not o["fixed"] and not o["locked"] and o["type"] == "rug"
    )
    chair.update(color="#AABBCC", locked=True)
    reply = await saved(room)
    assert (
        reply["visibility"] == "private"
        and reply["revision"] == 1
        and reply["image_count"] == 1
        and reply["version_count"] == 2
    )
    detail = (await client.get(f"/api/projects/{reply['id']}")).json()
    assert detail["snapshot"]["source"]["analysis"] == source.analysis.model_dump(
        mode="json"
    )
    assert detail["snapshot"]["source"][
        "room_assessment"
    ] == source.room_assessment.model_dump(mode="json")
    assert detail["snapshot"]["messages"] == body["messages"]
    current = next(
        v
        for v in detail["versions"]
        if v["id"] == detail["snapshot"]["current_version_id"]
    )
    assert current["scene"] == editable
    assert next(v for v in detail["versions"] if v["kind"] == "base")[
        "scene"
    ] == source.working_scene.model_dump(mode="json")
    photo = (await client.get(detail["images"][0]["url"])).content
    assert (
        photo.startswith(b"\xff\xd8")
        and "storage_key" not in str(detail)
        and str(settings.project_storage_dir) not in str(detail)
    )
    assert (
        await client.delete(f"/api/rooms/inputs/{source.input_id}")
    ).status_code == 204
    restarted = create_app(settings)
    async with (
        restarted.router.lifespan_context(restarted),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted),
            base_url="http://testserver",
            cookies={AUTH_COOKIE: client.cookies[AUTH_COOKIE]},
        ) as again,
    ):
        reopened = await again.get(f"/api/projects/{reply['id']}")
        assert reopened.status_code == 200 and reopened.json() == detail
        response = await again.get(detail["images"][0]["url"])
        assert (
            response.content == photo
            and response.headers["cache-control"] == "private, no-store"
        )
        assert response.headers["x-content-type-options"] == "nosniff"
    assert not app.state.analysis_service.analyzer.usage_records


async def test_same_request_is_idempotent_even_after_guest_expiry(room):
    app, client, settings, source, body = room
    first = await saved(room)
    assert (await saved(room)) == first and counts(app) == (1, 1, 2)
    await client.delete(f"/api/rooms/inputs/{source.input_id}")
    assert (await saved(room)) == first
    changed = {**body, "title": "Different title"}
    assert (await client.post("/api/projects", json=changed)).status_code == 409
    assert len(list(settings.project_storage_dir.rglob("*.jpg"))) == 1


async def test_new_revision_preserves_immutable_versions_without_guest_input(room):
    app, client, _, source, body = room
    first = await saved(room)
    await client.delete(f"/api/rooms/inputs/{source.input_id}")
    body = copy.deepcopy(body)
    body.update(request_id=str(uuid4()), project_id=first["id"], expected_revision=1)
    obj = next(
        o for o in body["manual_copies"][0]["scene"]["objects"] if o["type"] == "rug"
    )
    obj.update(color="#AABBCC", locked=True)
    second = await saved(room, body)
    assert second["revision"] == 2 and counts(app) == (1, 1, 3)
    stale = {**body, "request_id": str(uuid4())}
    assert (await client.post("/api/projects", json=stale)).status_code == 409
    assert (await saved(room, body)) == second


async def test_anonymous_and_other_accounts_cannot_read_or_claim_rooms(room):
    app, client, _, _source, body = room
    result = await saved(room)
    url = f"/api/projects/{result['id']}"
    image_url = (await client.get(url)).json()["images"][0]["url"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as outsider:
        assert (await outsider.get(url)).status_code == 401
        assert (await outsider.get(image_url)).status_code == 401
        assert (await outsider.post("/api/projects", json=body)).status_code == 401
        assert (
            await outsider.post(
                "/api/auth/register",
                json={"username": "another_user", "password": PASS},
            )
        ).status_code == 201
        for path in (url, image_url, f"/api/projects/{uuid4()}"):
            assert (await outsider.get(path)).status_code == 404
        other = {**body, "request_id": str(uuid4())}
        assert (await outsider.post("/api/projects", json=other)).status_code == 404
        other.update(project_id=result["id"], expected_revision=1)
        assert (await outsider.post("/api/projects", json=other)).status_code == 404
    assert counts(app) == (1, 1, 2)


@pytest.mark.parametrize(
    "change",
    ["dimensions", "fixed", "outside", "candidate", "unknown", "extra", "system"],
)
async def test_invalid_snapshots_do_not_create_any_partial_project(room, change):
    app, client, settings, _, original = room
    body = copy.deepcopy(original)
    scene = body["manual_copies"][0]["scene"]
    if change == "dimensions":
        scene["room"]["width_m"] += 1
    if change == "fixed":
        next(o for o in scene["objects"] if o["fixed"])["position"]["x"] += 1
    if change == "outside":
        next(o for o in scene["objects"] if not o["fixed"])["position"]["x"] = 90.0
    if change == "candidate":
        body["manual_copies"][0]["parent_run_id"] = str(uuid4())
    if change == "unknown":
        body["source_run_id"] = str(uuid4())
    if change == "extra":
        body["user_id"] = str(uuid4())
    if change == "system":
        body["messages"][0]["role"] = "system"
    response = await client.post("/api/projects", json=body)
    assert response.status_code in (404, 422), response.text
    assert counts(app) == (0, 0, 0) and not list(
        settings.project_storage_dir.rglob("*.jpg")
    )


async def test_expired_unsaved_room_does_not_lose_current_draft_or_create_project(room):
    app, client, _, source, body = room
    await client.delete(f"/api/rooms/inputs/{source.input_id}")
    assert (await client.post("/api/projects", json=body)).status_code == 404
    assert counts(app) == (0, 0, 0)


async def test_image_and_database_failures_roll_back_without_touching_guest_photos(
    room, monkeypatch
):
    app, client, settings, source, body = room
    writer = app.state.project_service.photos.write

    def broken(_):
        raise OSError("private path must not leak")

    monkeypatch.setattr(app.state.project_service.photos, "write", broken)
    error = await client.post("/api/projects", json=body)
    assert error.status_code == 503 and "private path" not in error.text
    assert counts(app) == (0, 0, 0)
    monkeypatch.setattr(app.state.project_service.photos, "write", writer)

    def fail_insert(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO scene_versions"):
            raise OperationalError(
                "test failure", {}, Exception("private database details")
            )

    event.listen(app.state.database.engine, "before_cursor_execute", fail_insert)
    try:
        error = await client.post("/api/projects", json=body)
        assert error.status_code == 503 and "private database" not in error.text
    finally:
        event.remove(app.state.database.engine, "before_cursor_execute", fail_insert)
    assert counts(app) == (0, 0, 0) and not list(
        settings.project_storage_dir.rglob("*.jpg")
    )
    assert app.state.upload_service.storage.resolve(
        source.input_id, owner_scope(client.cookies[GUEST_COOKIE])
    )
    assert (await saved(room))["revision"] == 1


async def test_project_requests_are_bounded_and_reject_foreign_origins(room):
    _, client, _, _, body = room
    assert (
        await client.post(
            "/api/projects", json=body, headers={"origin": "https://other.example"}
        )
    ).status_code == 403
    assert (
        await client.post(
            "/api/projects",
            content=b" " * (8 * 1024 * 1024 + 1),
            headers={"content-type": "application/json"},
        )
    ).status_code == 413
    assert (
        await client.post(
            "/api/projects",
            content='{"title":"one","title":"two"}',
            headers={"content-type": "application/json"},
        )
    ).status_code == 422


async def test_retained_improvements_and_separate_manual_copies_are_saved(room):
    from types import SimpleNamespace

    app, client, _, source, body = room
    candidate_id = str(uuid4())
    candidate = source.working_scene.model_dump(mode="json")
    next(o for o in candidate["objects"] if o["id"] == "rug_1")["color"] = "#AABBCC"
    result = {
        "source_run_id": source.run_id,
        "parent_run_id": None,
        "source_scene": source.working_scene.model_dump(mode="json"),
        "status": "completed",
        "result_scene": candidate,
        "attempts": [{"outcome": "accepted"}],
        "instruction": "Use warmer colors",
    }
    read = AsyncMock(return_value=SimpleNamespace(model_dump=lambda **_: result))
    app.state.project_service.improvements = SimpleNamespace(read=read)
    body = copy.deepcopy(body)
    body.update(
        improvement_run_ids=[candidate_id],
        selected_improvement_id=candidate_id,
        view="improved",
    )
    manual = copy.deepcopy(candidate)
    next(o for o in manual["objects"] if o["id"] == "rug_1").update(
        color="#112233", locked=True
    )
    body["manual_copies"].append({"parent_run_id": candidate_id, "scene": manual})
    reply = await saved(room, body)
    detail = (await client.get(f"/api/projects/{reply['id']}")).json()
    assert counts(app) == (1, 1, 4)
    current = next(
        v
        for v in detail["versions"]
        if v["id"] == detail["snapshot"]["current_version_id"]
    )
    assert current["kind"] == "refined" and current["scene"] == candidate
    assert any(
        v["kind"] == "manual" and v["scene"] == manual for v in detail["versions"]
    )
    read.assert_awaited_once_with(
        candidate_id, owner_scope(client.cookies[GUEST_COOKIE])
    )
    # Re-saving an already retained candidate after guest cleanup uses its durable evidence.
    await client.delete(f"/api/rooms/inputs/{source.input_id}")
    body.update(project_id=reply["id"], expected_revision=1, request_id=str(uuid4()))
    assert (await saved(room, body))["revision"] == 2
    read.assert_awaited_once()

async def test_list_open_rename_and_delete_private_room_without_regeneration(room):
    app,client,settings,source,body=room
    first=await saved(room)
    listed=(await client.get('/api/projects')).json()
    assert len(listed)==1 and listed[0]['id']==first['id'] and listed[0]['visibility']=='private'
    assert (await client.delete(f'/api/rooms/inputs/{source.input_id}')).status_code==204
    opened=await client.post(f'/api/projects/{first["id"]}/open',json={})
    assert opened.status_code==200,opened.text
    assert opened.json()['run']['persistence']=='account'
    assert opened.json()['run']['working_scene']==source.working_scene.model_dump(mode='json')
    renamed=await client.patch(f'/api/projects/{first["id"]}',json={'title':'  My renamed room  ','expected_revision':1})
    assert renamed.status_code==200 and renamed.json()['revision']==2 and renamed.json()['title']=='My renamed room'
    assert (await client.patch(f'/api/projects/{first["id"]}',json={'title':'Stale name','expected_revision':1})).status_code==409
    assert (await client.request('DELETE',f'/api/projects/{first["id"]}',json={'expected_revision':1})).status_code==409
    assert (await client.request('DELETE',f'/api/projects/{first["id"]}',json={'expected_revision':2})).status_code==204
    assert (await client.get('/api/projects')).json()==[]
    assert (await client.get(f'/api/projects/{first["id"]}')).status_code==404
    assert not list(settings.project_storage_dir.rglob('*.jpg'))
    assert not app.state.analysis_service.analyzer.usage_records


async def test_catalog_addition_survives_save_reopen_and_manual_copy_without_loosening_original(room):
    from types import SimpleNamespace

    from roometry.optimization.inventory import apply_inventory
    from roometry.optimization.schemas import CandidateProposal

    app, client, _, source, body = room
    candidate_id = str(uuid4())
    proposal = CandidateProposal(changes=[], additions=[{
        'object_id': 'new_basket', 'asset_id': 'basket_standard_01',
        'position': {'x': .3, 'z': 3.1}, 'rotation_y_rad': 0., 'color': None,
    }], summary='Add a compact storage basket.', suggestions=[])
    candidate = apply_inventory(source.working_scene, proposal).model_dump(mode='json')
    result = {'source_run_id': source.run_id, 'parent_run_id': None,
              'source_scene': source.working_scene.model_dump(mode='json'), 'status': 'completed',
              'result_scene': candidate, 'attempts': [{'outcome': 'accepted'}], 'instruction': None}
    app.state.project_service.improvements = SimpleNamespace(read=AsyncMock(return_value=SimpleNamespace(model_dump=lambda **_: result)))
    body = copy.deepcopy(body)
    body.update(improvement_run_ids=[candidate_id], selected_improvement_id=candidate_id, view='improved')
    manual = copy.deepcopy(candidate)
    next(o for o in manual['objects'] if o['id'] == 'new_basket').update(color='#AABBCC', locked=True)
    body['manual_copies'].append({'parent_run_id': candidate_id, 'scene': manual})
    reply = await saved(room, body)
    opened = (await client.get(f"/api/projects/{reply['id']}")).json()
    assert next(v for v in opened['versions'] if v['kind'] == 'base')['scene'] == source.working_scene.model_dump(mode='json')
    assert any(v['kind'] == 'optimized' and v['scene'] == candidate for v in opened['versions'])
    assert any(v['kind'] == 'manual' and v['scene'] == manual for v in opened['versions'])
    body.update(project_id=reply['id'], expected_revision=1, request_id=str(uuid4()))
    await client.delete(f'/api/rooms/inputs/{source.input_id}')
    assert (await saved(room, body))['revision'] == 2


@pytest.mark.parametrize('created', [False, True])
async def test_editable_copy_ui_preference_survives_save_and_reopen(room, created):
    _, client, _, source, body = room
    body = copy.deepcopy(body)
    body.update(view='original', has_editable_copy=created)
    receipt = await saved(room, body)
    detail = (await client.get(f"/api/projects/{receipt['id']}")).json()
    assert detail['snapshot']['comparison']['has_editable_copy'] is created
    assert next(v for v in detail['versions'] if v['kind'] == 'base')['scene'] == source.working_scene.model_dump(mode='json')
