"""Publish/revoke real saved versions without exposing private project data."""

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from io import BytesIO
from uuid import UUID, uuid4

import httpx
import pytest
from PIL import Image
from sqlalchemy import select

from roometry.feed.model import Publication
from roometry.feed.schemas import PublishRequest
from roometry.feed.service import public_scene
from roometry.main import create_app
from roometry.projects.model import Project, SceneVersion
from roometry.scenes.schemas import RoomScene
from tests.test_auth import PASS
from tests.test_projects import room as _room_fixture
from tests.test_projects import saved

room = _room_fixture

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def options(room):
    _, client, _, source, body = room
    response = await client.post(
        "/api/projects/sharing-options",
        json={
            "source_run_id": source.run_id,
            "scene": body["manual_copies"][0]["scene"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def publish(room, **updates):
    _, client, _, _, _ = room
    data = await options(room)
    body = {
        "version_id": data["selected_version_id"],
        "expected_revision": data["revision"],
        "expected_publication_revision": data["publication"]["revision"]
        if data["publication"]
        else 0,
        "title": "Shared studio",
        "summary": "A calm corner.",
        "consent": True,
        **updates,
    }
    response = await client.post(
        f"/api/projects/{data['project_id']}/publication", json=body
    )
    return response, data, body


async def test_private_saves_do_not_appear_until_explicit_publishing_and_public_payload_is_allowlisted(
    room,
):
    app, client, _, source, _ = room
    assert await options(room) is None
    private = await saved(room)
    assert (await client.get("/api/feed")).json() == {"items": [], "next_cursor": None}
    options_data = await options(room)
    assert options_data["selected_version_id"] and options_data["publication"] is None
    for consent in [False, None, 1]:
        response, _, _ = await publish(room, consent=consent)
        assert response.status_code == 422
    response, _, body = await publish(room)
    assert response.status_code == 200, response.text
    public = response.json()
    assert public["active"] and public["revision"] == 1
    assert (
        await client.post(f"/api/projects/{private['id']}/publication", json=body)
    ).json() == public
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as guest:
        page = await guest.get("/api/feed")
        assert page.status_code == 200 and len(page.json()["items"]) == 1
        card = page.json()["items"][0]
        assert set(card) == {
            "id",
            "title",
            "summary",
            "room_type",
            "object_count",
            "published_at",
            "preview_url",
        }
        detail = await guest.get(f"/api/feed/{public['id']}")
        assert detail.status_code == 200 and set(detail.json()) == {*card, "scene"}
        text = detail.text
        for private_value in [
            private["id"],
            source.run_id,
            source.input_id,
            *source.image_ids,
            "Keep my desk near the window.",
            "My private room",
            "project_user",
        ]:
            assert private_value not in text
        assert detail.headers["cache-control"] == "no-store"
        scene = RoomScene.model_validate_json(json.dumps(detail.json()["scene"]))
        assert all(o.fixed and o.locked for o in scene.objects)
        assert all(o.id.startswith("object_") for o in scene.objects)
        assert not scene.assumptions and not scene.warnings and not scene.wall_mapping
        image = await guest.get(card["preview_url"])
        assert image.status_code == 200 and image.content.startswith(b"\x89PNG")
        assert image.headers["cache-control"] == "no-store"
        private_detail = (await client.get(f"/api/projects/{private['id']}")).json()
        assert (await guest.get(private_detail["images"][0]["url"])).status_code == 401
        assert (await guest.get(f"/api/projects/{private['id']}")).status_code == 401
    assert (await client.get(f"/api/projects/{private['id']}")).json()[
        "visibility"
    ] == "private"


def test_sanitizer_preserves_geometry_and_supports_while_stripping_notes_and_evidence(
    bedroom_scene,
):
    scene = bedroom_scene.model_copy(deep=True)
    scene.assumptions = ["PRIVATE_CLARIFICATION_MARKER"]
    scene.warnings = ["PRIVATE_DESCRIPTION_MARKER"]
    original = scene.model_dump()
    result = public_scene(scene)
    assert "PRIVATE_" not in result.model_dump_json()
    assert scene.model_dump() == original
    assert result.room == scene.room and result.finishes == scene.finishes
    for old, new in zip(scene.objects, result.objects):
        assert (old.type, old.position, old.dimensions_m, old.color) == (
            new.type,
            new.position,
            new.dimensions_m,
            new.color,
        )
    assert {
        o.support_object_id for o in result.objects if o.placement == "surface"
    } <= {o.id for o in result.objects}


async def test_anonymous_and_other_accounts_cannot_publish_preview_or_unpublish_private_rooms(
    room,
):
    app, client, _, _, _ = room
    private = await saved(room)
    response, data, body = await publish(room)
    publication = response.json()
    preview = data["versions"][0]["preview_url"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as stranger:
        assert (
            await stranger.post(f"/api/projects/{private['id']}/publication", json=body)
        ).status_code == 401
        assert (await stranger.get(preview)).status_code == 401
        assert (
            await stranger.post(
                "/api/auth/register",
                json={"username": "other_feed_user", "password": PASS},
            )
        ).status_code == 201
        assert (
            await stranger.post(f"/api/projects/{private['id']}/publication", json=body)
        ).status_code == 404
        assert (await stranger.get(preview)).status_code == 404
        assert (
            await stranger.request(
                "DELETE",
                f"/api/projects/{private['id']}/publication",
                json={
                    "public_id": publication["id"],
                    "expected_publication_revision": 1,
                },
            )
        ).status_code == 404
    assert (
        await client.post(
            f"/api/projects/{private['id']}/publication",
            json=body,
            headers={"origin": "https://untrusted.example"},
        )
    ).status_code == 403


async def test_unpublishing_revokes_json_and_preview_and_stale_retries_cannot_republish(
    room,
):
    _, client, _, _, _ = room
    private = await saved(room)
    published, _, body = await publish(room)
    first = published.json()
    delete = {
        "public_id": first["id"],
        "expected_publication_revision": first["revision"],
    }
    path = f"/api/projects/{private['id']}/publication"
    assert (await client.request("DELETE", path, json=delete)).status_code == 204
    assert (await client.request("DELETE", path, json=delete)).status_code == 204
    assert (await client.get("/api/feed")).json()["items"] == []
    assert (await client.get(f"/api/feed/{first['id']}")).status_code == 404
    assert (await client.get(f"/api/feed/{first['id']}/preview")).status_code == 404
    assert (await client.post(path, json=body)).status_code == 409
    republished, _, _ = await publish(room)
    assert republished.status_code == 200, republished.text
    assert republished.json()["id"] != first["id"]
    assert (await client.request("DELETE", path, json=delete)).status_code == 409
    assert (
        await client.get(f"/api/feed/{republished.json()['id']}")
    ).status_code == 200


async def test_project_deletion_revokes_the_publication_and_its_assets(room):
    app, client, _, _, _ = room
    private = await saved(room)
    response, _, _ = await publish(room)
    public_id = response.json()["id"]
    assert (
        await client.request(
            "DELETE", f"/api/projects/{private['id']}", json={"expected_revision": 1}
        )
    ).status_code == 204
    assert (await client.get(f"/api/feed/{public_id}")).status_code == 404
    assert (await client.get(f"/api/feed/{public_id}/preview")).status_code == 404
    with app.state.database.sessions() as db:
        assert db.scalar(select(Publication)) is None


async def test_saving_private_changes_does_not_update_the_published_snapshot(room):
    _, client, _, _, body = room
    private = await saved(room)
    response, _, publish_body = await publish(room)
    public_id = response.json()["id"]
    original = (await client.get(f"/api/feed/{public_id}")).json()
    revised = copy.deepcopy(body)
    revised.update(
        request_id=str(uuid4()), project_id=private["id"], expected_revision=1
    )
    rug = next(
        o for o in revised["manual_copies"][0]["scene"]["objects"] if o["type"] == "rug"
    )
    rug["color"] = "#AABBCC"
    await saved(room, revised)
    assert (await client.get(f"/api/feed/{public_id}")).json() == original
    assert (
        await client.post(
            f"/api/projects/{private['id']}/publication", json=publish_body
        )
    ).status_code == 409
    current_room = (*room[:4], revised)
    updated, _, _ = await publish(current_room)
    assert updated.status_code == 200, updated.text
    assert updated.json()["revision"] == 2
    assert (
        next(
            o
            for o in (await client.get(f"/api/feed/{public_id}")).json()["scene"][
                "objects"
            ]
            if o["type"] == "rug"
        )["color"]
        == "#AABBCC"
    )


async def test_foreign_versions_and_injected_scene_payloads_are_rejected(room):
    _, client, _, _, _ = room
    await saved(room)
    response, _, body = await publish(room, version_id=str(uuid4()))
    assert response.status_code == 404
    data = await options(room)
    response = await client.post(
        f"/api/projects/{data['project_id']}/publication",
        json={**body, "version_id": data["selected_version_id"], "scene": {}},
    )
    assert response.status_code == 422
    assert (await client.get("/api/feed")).json()["items"] == []


async def test_pagination_handles_ties_and_unpublished_cursor_items(room):
    app, client, _, _, _ = room
    private = await saved(room)
    with app.state.database.sessions.begin() as db:
        parent = db.get(Project, UUID(private["id"]))
        version = db.scalar(
            select(SceneVersion).where(SceneVersion.project_id == parent.id)
        )
        scene = public_scene(RoomScene.model_validate(version.scene_json)).model_dump(
            mode="json"
        )
        for index in range(14):
            project = Project(
                id=uuid4(),
                user_id=parent.user_id,
                source_run_id=str(uuid4()),
                title="Private",
                revision=1,
                snapshot={},
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            db.add(project)
            db.flush()
            own_version = SceneVersion(
                id=uuid4(),
                project_id=project.id,
                kind="base",
                fingerprint="f" * 64,
                scene_json=version.scene_json,
                validation_summary={},
                created_at=datetime.now(UTC),
            )
            db.add(own_version)
            db.flush()
            db.add(
                Publication(
                    project_id=project.id,
                    public_id=uuid4(),
                    version_id=own_version.id,
                    active=True,
                    revision=1,
                    title=f"Room {index}",
                    summary="",
                    scene=scene,
                    preview=b"test-preview",
                    published_at=datetime(2026, 10, 27, tzinfo=UTC),
                )
            )
    first = (await client.get("/api/feed")).json()
    assert len(first["items"]) == 12 and first["next_cursor"]
    last_id = UUID(first["items"][-1]["id"])
    with app.state.database.sessions.begin() as db:
        db.scalar(
            select(Publication).where(Publication.public_id == last_id)
        ).active = False
    second = (
        await client.get("/api/feed", params={"cursor": first["next_cursor"]})
    ).json()
    assert len(second["items"]) == 2 and second["next_cursor"] is None
    assert not {o["id"] for o in first["items"]} & {o["id"] for o in second["items"]}
    for cursor in ["not-json", "a" * 201]:
        assert (
            await client.get("/api/feed", params={"cursor": cursor})
        ).status_code == 422


async def test_duplicate_concurrent_publish_is_one_snapshot(room):
    app, client, _, _, _ = room
    private = await saved(room)
    data = await options(room)
    body = PublishRequest(
        version_id=UUID(data["selected_version_id"]),
        expected_revision=1,
        expected_publication_revision=0,
        title="Concurrent room",
        consent=True,
    )
    with app.state.database.sessions() as db:
        user = db.get(Project, UUID(private["id"])).user_id
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(app.state.feed_service.publish, user, UUID(private["id"]), body)
            for _ in range(2)
        ]
        results = [f.result() for f in futures]
    assert results[0] == results[1] and results[0]["revision"] == 1
    assert len((await client.get("/api/feed")).json()["items"]) == 1


async def test_published_snapshot_survives_restart_without_auth_or_an_active_generation(
    room,
):
    _, client, settings, _, _ = room
    await saved(room)
    response, _, _ = await publish(room)
    public_id = response.json()["id"]
    before = (await client.get(f"/api/feed/{public_id}")).json()
    restarted = create_app(settings)
    async with (
        restarted.router.lifespan_context(restarted),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted), base_url="http://testserver"
        ) as guest,
    ):
        assert (await guest.get(f"/api/feed/{public_id}")).json() == before
        assert (await guest.get(before["preview_url"])).content.startswith(b"\x89PNG")
        assert not restarted.state.analysis_service.analyzer.usage_records


async def test_preview_failure_leaves_private_room_intact_and_publishes_nothing(
    room, monkeypatch
):
    _, client, _, _, _ = room
    private = await saved(room)

    def failed(*args, **kwargs):
        raise ValueError("Diagnostic render face budget exceeded.")

    monkeypatch.setattr("roometry.feed.service.render_views", failed)
    response, _, _ = await publish(room)
    assert response.status_code == 503
    assert response.json()["detail"][0]["code"] == "preview_unavailable"
    assert (await client.get("/api/feed")).json()["items"] == []
    assert (await client.get(f"/api/projects/{private['id']}")).status_code == 200


def test_public_preview_omits_diagnostic_text_and_direction_arrows(bedroom_scene):
    from roometry.feed.service import preview
    from roometry.vision.review_render import render_views

    public = preview(public_scene(bedroom_scene))
    assert public != render_views(bedroom_scene, view_ids=("cutaway_nw",))[0].image
    with Image.open(BytesIO(public)) as image:
        assert image.size == (768, 768)
        assert image.crop((0, 0, 768, 40)).getextrema() == (
            (245, 245),
            (243, 243),
            (237, 237),
        )
