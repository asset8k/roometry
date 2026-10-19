"""Real multipart integration: ownership, rollback, expiry, and failure recovery."""

from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from threading import Event
from uuid import uuid4

import anyio
import httpx
from PIL import Image
import pytest

from roometry.config import Settings
from roometry.main import create_app
from roometry.uploads.errors import UploadFailure
from roometry.uploads.session import COOKIE_NAME, owner_scope
from roometry.uploads.storage import LocalInputStorage

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def photo():
    buffer = BytesIO()
    with Image.new("RGB", (256, 256), "green") as image:
        image.save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture
def app(tmp_path):
    return create_app(Settings(_env_file=None, app_environment="test", upload_storage_dir=tmp_path / "private"))


@pytest.fixture
async def client(app):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            yield client


def batch(photo, count=1, options="{}"):
    return [("options", (None, options)), *[("images", (f"room-{index}.png", photo, "image/png")) for index in range(count)]]


def owner(client):
    return owner_scope(client.cookies[COOKIE_NAME])


async def test_limits_health_and_openapi(client):
    response = await client.get("/api/rooms/input-limits")
    assert response.status_code == 200 and response.json()["max_images"] == 10
    assert "image/heic" in response.json()["allowed_media_types"]
    assert "upload_storage_dir" not in response.json()
    assert (await client.get("/api/health")).status_code == 200
    schema = (await client.get("/openapi.json")).json()
    assert "multipart/form-data" in schema["paths"]["/api/rooms/inputs"]["post"]["requestBody"]["content"]


async def test_private_metadata_and_owned_delete(client, app, photo):
    response = await client.post("/api/rooms/inputs", files=batch(photo, options=json.dumps({"dimensions": {"width_m": 4}, "goals": ["better_aesthetics"], "goal_text": "Keep the bed; make the room cozier."})))
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "ready" and data["options"]["dimensions"]["height_m"] is None
    assert data["options"]["goals"] == ["better_aesthetics"]
    assert data["options"]["goal_text"] == "Keep the bed; make the room cozier."
    assert "HttpOnly" in response.headers["set-cookie"] and "SameSite=lax" in response.headers["set-cookie"]
    assert response.headers["cache-control"] == "no-store"
    assert set(data) == {"input_id", "status", "options", "images", "expires_at"}
    storage = app.state.upload_service.storage
    result = storage.read_image(data["input_id"], data["images"][0]["image_id"], owner(client))
    with Image.open(BytesIO(result)) as image:
        assert image.format == "JPEG" and image.mode == "RGB" and not image.getexif()
    assert all(path.suffix == ".jpg" for path in storage.root.rglob("*") if path.is_file())
    assert (await client.get(f'/uploads/{data["input_id"]}/{data["images"][0]["image_id"]}.jpg')).status_code == 404
    deleted = await client.delete(f'/api/rooms/inputs/{data["input_id"]}')
    assert deleted.status_code == 204 and deleted.content == b""
    assert not list(storage.root.iterdir())


@pytest.mark.parametrize("count,expected", [(0, 422), (10, 201), (11, 422)])
async def test_image_count(client, photo, count, expected):
    assert (await client.post("/api/rooms/inputs", files=batch(photo, count))).status_code == expected


@pytest.mark.parametrize("options", ['{"dimensions":{"width_m":"4"}}', '{"budget":{"amount":NaN,"currency":"USD"}}',
                                      '{"goals":[],"goals":["workspace"]}', '[1]', 'null', '{invalid'])
async def test_invalid_options_do_not_write_files(client, app, photo, options):
    response = await client.post("/api/rooms/inputs", files=batch(photo, options=options))
    assert response.status_code == 422
    assert response.json()["detail"][0]["code"] == "invalid_options"
    assert response.json()["detail"][0]["message"] == "Check the room options, dimensions, and budget."
    assert not list(app.state.upload_service.storage.root.iterdir())


async def test_duplicate_unexpected_and_missing_fields(client, photo):
    for fields in (batch(photo) + [("options", (None, "{}"))], batch(photo) + [("secret", (None, "private"))],
                   [("images", ("room.png", photo, "image/png"))], [("options", (None, "{}")), ("wrong", ("room.png", photo, "image/png"))]):
        response = await client.post("/api/rooms/inputs", files=fields)
        assert response.status_code in (400, 422)
        assert "private" not in response.text


async def test_malformed_and_oversized_metadata(client, photo):
    assert (await client.post("/api/rooms/inputs", json={})).status_code == 415
    assert (await client.post("/api/rooms/inputs", content=b"bad", headers={"content-type": "multipart/form-data"})).status_code == 400
    assert (await client.post("/api/rooms/inputs", files=batch(photo, options="x" * 16385))).status_code == 413
    request = client.build_request("POST", "/api/rooms/inputs", files=batch(photo))
    raw = request.read()
    response = await client.post("/api/rooms/inputs", content=raw[:-20], headers={"content-type": request.headers["content-type"]})
    assert response.status_code == 400


async def test_atomic_replacement_and_mixed_failure(client, app, photo):
    first = (await client.post("/api/rooms/inputs", files=batch(photo))).json()
    storage = app.state.upload_service.storage
    bad = batch(photo) + [("images", ("broken.png", b"corrupt private bytes", "image/png"))]
    response = await client.post("/api/rooms/inputs", files=bad)
    assert response.status_code == 422 and response.json()["detail"][0]["file_index"] == 1
    assert storage.resolve(first["input_id"], owner(client)).input_id == first["input_id"]
    assert [path.name for path in storage.root.iterdir()] == [first["input_id"]]
    second = (await client.post("/api/rooms/inputs", files=batch(photo))).json()
    assert not (storage.root / first["input_id"]).exists()
    with pytest.raises(UploadFailure):
        storage.resolve(first["input_id"], owner(client))
    assert storage.resolve(second["input_id"], owner(client)).input_id == second["input_id"]


async def test_cross_session_and_expiry(client, app, photo):
    storage = app.state.upload_service.storage
    now = datetime(2026, 10, 18, tzinfo=timezone.utc)
    storage.now = lambda: now
    data = (await client.post("/api/rooms/inputs", files=batch(photo))).json()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as stranger:
        assert (await stranger.delete(f'/api/rooms/inputs/{data["input_id"]}')).status_code == 404
    with pytest.raises(UploadFailure):
        storage.read_image(data["input_id"], data["images"][0]["image_id"], "stranger")
    storage.now = lambda: now + timedelta(hours=1)
    assert (await client.delete(f'/api/rooms/inputs/{data["input_id"]}')).status_code == 404
    with pytest.raises(UploadFailure):
        storage.resolve(data["input_id"], owner(client))
    await app.state.upload_service.cleanup()
    assert not list(storage.root.iterdir())


async def test_write_failure_rolls_back(client, app, photo, monkeypatch):
    first = (await client.post("/api/rooms/inputs", files=batch(photo))).json()
    storage = app.state.upload_service.storage
    def failed(*_args):
        raise OSError("private-storage-path")
    monkeypatch.setattr(storage, "write_image", failed)
    response = await client.post("/api/rooms/inputs", files=batch(photo))
    assert response.status_code == 503 and "private-storage-path" not in response.text
    assert [path.name for path in storage.root.iterdir()] == [first["input_id"]]
    assert storage.resolve(first["input_id"], owner(client))


async def test_origin_is_checked_before_mutations(client, app, photo):
    response = await client.post("/api/rooms/inputs", files=batch(photo), headers={"origin": "https://untrusted.example"})
    assert response.status_code == 403 and not list(app.state.upload_service.storage.root.iterdir())
    response = await client.post("/api/rooms/inputs", files=batch(photo), headers={"origin": "http://testserver"})
    assert response.status_code == 201
    assert (await client.delete(f'/api/rooms/inputs/{response.json()["input_id"]}', headers={"origin": "null"})).status_code == 403


async def test_restart_purges_inputs_and_staging(client, app, photo):
    storage = app.state.upload_service.storage
    await client.post("/api/rooms/inputs", files=batch(photo))
    assert storage.stage().exists()
    restarted = LocalInputStorage(storage.root)
    restarted.initialize()
    assert not list(storage.root.iterdir())


async def test_symlink_root_cannot_delete_target(tmp_path, photo):
    target = tmp_path / "target"
    target.mkdir()
    sentinel = target / "keep.txt"
    sentinel.write_text("keep")
    root = tmp_path / "symlink"
    root.symlink_to(target, target_is_directory=True)
    app = create_app(Settings(_env_file=None, app_environment="test", upload_storage_dir=root))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            assert (await client.post("/api/rooms/inputs", files=batch(photo))).status_code == 503
    assert sentinel.read_text() == "keep"


async def test_actual_byte_limits_including_missing_or_false_content_length(tmp_path, photo):
    settings = Settings(_env_file=None, app_environment="test", upload_storage_dir=tmp_path / "limited",
                        upload_max_file_bytes=len(photo), upload_max_total_file_bytes=len(photo), upload_max_body_bytes=20000)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            assert (await client.post("/api/rooms/inputs", files=batch(photo))).status_code == 201
            assert (await client.post("/api/rooms/inputs", files=batch(photo, 2))).json()["detail"][0]["code"] == "total_files_too_large"
            assert (await client.post("/api/rooms/inputs", files=batch(photo + b"x"))).json()["detail"][0]["code"] == "file_too_large"
            for length in (None, "1", "20001"):
                headers = {"content-type": "multipart/form-data; boundary=x"}
                if length is not None:
                    headers["content-length"] = length
                async def multipart():
                    yield b'--x\r\nContent-Disposition: form-data; name="images"; filename="room.png"\r\n\r\n'
                    for _ in range(21):
                        yield b"x" * 1000
                    yield b"\r\n--x--\r\n"
                response = await client.post("/api/rooms/inputs", content=multipart(), headers=headers)
                assert response.status_code == 413
                assert response.json()["detail"][0]["code"] == "request_too_large"


async def test_cancelled_decode_cleans_staging_and_preserves_previous(client, app, photo, monkeypatch):
    from roometry.uploads import service as service_module
    previous = (await client.post("/api/rooms/inputs", files=batch(photo))).json()
    entered, release = Event(), Event()
    original = service_module.normalize_photo
    def blocked(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(service_module, "normalize_photo", blocked)
    async def upload():
        await client.post("/api/rooms/inputs", files=batch(photo))
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(upload)
        assert await anyio.to_thread.run_sync(entered.wait, 3)
        tasks.cancel_scope.cancel()
        release.set()
    storage = app.state.upload_service.storage
    assert storage.resolve(previous["input_id"], owner(client))
    assert [path.name for path in storage.root.iterdir()] == [previous["input_id"]]


async def test_concurrent_replacements_leave_one_accepted_input(client, app, photo):
    await client.post("/api/rooms/inputs", files=batch(photo))
    results = []
    async def upload():
        results.append(await client.post("/api/rooms/inputs", files=batch(photo)))
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(upload)
        tasks.start_soon(upload)
    assert all(result.status_code == 201 for result in results)
    storage = app.state.upload_service.storage
    directories = list(storage.root.iterdir())
    assert len(directories) == 1
    assert directories[0].name in {result.json()["input_id"] for result in results}
    assert storage.resolve(directories[0].name, owner(client))


async def test_cleanup_retries_failed_staging_delete(client, app, photo, monkeypatch):
    storage = app.state.upload_service.storage
    def failed_remove(_path):
        raise OSError("temporary cleanup failure")
    def failed_write(*_args):
        raise OSError("temporary write failure")
    with monkeypatch.context() as patch:
        patch.setattr(storage, "_remove", failed_remove)
        patch.setattr(storage, "write_image", failed_write)
        assert (await client.post("/api/rooms/inputs", files=batch(photo))).status_code == 503
    assert list(storage.root.iterdir())
    await app.state.upload_service.cleanup()
    assert not list(storage.root.iterdir())


async def test_parser_closes_files_after_failure(client, photo, monkeypatch):
    from starlette import formparsers
    original = formparsers.SpooledTemporaryFile
    handles = []
    def tracked(*args, **kwargs):
        handle = original(*args, **kwargs)
        handles.append(handle)
        return handle
    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", tracked)
    assert (await client.post("/api/rooms/inputs", files=batch(photo, 11))).status_code == 422
    assert handles and all(handle.closed for handle in handles)
    assert (await client.post("/api/rooms/inputs", files=batch(b"corrupt"))).status_code == 422
    assert all(handle.closed for handle in handles)


async def test_secure_cookie_and_credentialed_cors(tmp_path, photo):
    app = create_app(Settings(_env_file=None, app_environment="production", cors_origins=["https://frontend.example"],
                              upload_storage_dir=tmp_path / "secure"))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://api.example") as client:
            response = await client.post("/api/rooms/inputs", files=batch(photo), headers={"origin": "https://frontend.example"})
            assert response.status_code == 201
            assert "Secure" in response.headers["set-cookie"]
            assert response.headers["access-control-allow-credentials"] == "true"
            assert (await client.delete(f'/api/rooms/inputs/{response.json()["input_id"]}', headers={"origin": "https://frontend.example"})).status_code == 204


async def test_oversized_part_headers_and_content_type_are_rejected(client):
    raw = b'--x\r\nContent-Disposition: form-data; name="images"; filename="' + b"x" * 9000 + b'.png"\r\n\r\nbad\r\n--x--\r\n'
    assert (await client.post("/api/rooms/inputs", content=raw, headers={"content-type": "multipart/form-data; boundary=x"})).status_code == 400
    assert (await client.post("/api/rooms/inputs", content=b"", headers={"content-type": "multipart/form-data; boundary=" + "x" * 600})).status_code == 400


async def test_analysis_handoff_checks_scope_membership_expiry_and_copies(client, app, photo):
    """Future analysis uses scoped storage reads, never a client-supplied path."""
    storage = app.state.upload_service.storage
    now = datetime(2026, 10, 18, tzinfo=timezone.utc)
    storage.now = lambda: now
    data = (await client.post("/api/rooms/inputs", files=batch(photo, options='{"description":"Original"}'))).json()
    input_id, image_id, scope = data["input_id"], data["images"][0]["image_id"], owner(client)
    snapshot = storage.resolve(input_id, scope)
    snapshot.options.description = "Changed by consumer"
    snapshot.images.clear()
    assert storage.resolve(input_id, scope).options.description == "Original"
    assert storage.read_image(input_id, image_id, scope).startswith(b"\xff\xd8")
    for wrong_input, wrong_image, wrong_scope in (
        (input_id, image_id, "another session"),
        (input_id, str(uuid4()), scope),
        (input_id, "../../outside", scope),
        (str(uuid4()), image_id, scope),
    ):
        with pytest.raises(UploadFailure) as failure:
            storage.read_image(wrong_input, wrong_image, wrong_scope)
        assert failure.value.status == 404
    storage.now = lambda: now + timedelta(hours=1)
    # Expiry is enforced on every read, before the cleanup task removes bytes.
    assert (storage.root / input_id).exists()
    with pytest.raises(UploadFailure) as failure:
        storage.read_image(input_id, image_id, scope)
    assert failure.value.status == 404


async def test_private_permissions_and_unmanaged_files_survive_cleanup(client, app, photo):
    storage = app.state.upload_service.storage
    data = (await client.post("/api/rooms/inputs", files=batch(photo))).json()
    directory = storage.root / data["input_id"]
    assert storage.root.stat().st_mode & 0o777 == 0o700
    assert directory.stat().st_mode & 0o777 == 0o700
    assert next(directory.iterdir()).stat().st_mode & 0o777 == 0o600
    sentinel = storage.root / "keep.txt"
    sentinel.write_text("unmanaged")
    active = storage.stage()
    await app.state.upload_service.cleanup()
    assert active.exists() and sentinel.read_text() == "unmanaged"
    storage.discard_stage(active)
    await client.delete(f'/api/rooms/inputs/{data["input_id"]}')
    assert list(storage.root.iterdir()) == [sentinel]


async def test_commit_failure_preserves_previous_batch(client, app, photo, monkeypatch):
    storage = app.state.upload_service.storage
    previous = (await client.post("/api/rooms/inputs", files=batch(photo))).json()
    def fail_commit(*_args):
        raise OSError("private storage details")
    monkeypatch.setattr(storage, "commit", fail_commit)
    response = await client.post("/api/rooms/inputs", files=batch(photo))
    assert response.status_code == 503 and "private storage details" not in response.text
    assert storage.resolve(previous["input_id"], owner(client))
    assert [path.name for path in storage.root.iterdir()] == [previous["input_id"]]


@pytest.mark.parametrize("operation", ["delete", "replace"])
async def test_failed_byte_deletion_revokes_access_and_cleanup_retries(client, app, photo, monkeypatch, operation):
    storage = app.state.upload_service.storage
    previous = (await client.post("/api/rooms/inputs", files=batch(photo))).json()
    old_directory = storage.root / previous["input_id"]
    original_remove = storage._remove
    def fail_old_delete(path):
        if path == old_directory:
            raise OSError("temporary disk failure")
        original_remove(path)
    with monkeypatch.context() as patch:
        patch.setattr(storage, "_remove", fail_old_delete)
        if operation == "delete":
            response = await client.delete(f'/api/rooms/inputs/{previous["input_id"]}')
            assert response.status_code == 503
        else:
            response = await client.post("/api/rooms/inputs", files=batch(photo))
            assert response.status_code == 201
            assert storage.resolve(response.json()["input_id"], owner(client))
        assert old_directory.exists()
        with pytest.raises(UploadFailure):
            storage.read_image(previous["input_id"], previous["images"][0]["image_id"], owner(client))
    await app.state.upload_service.cleanup()
    assert not old_directory.exists()


async def test_disconnect_during_multipart_closes_originals(client, app, photo, monkeypatch):
    from starlette import formparsers
    handles = []
    original = formparsers.SpooledTemporaryFile
    def tracked(*args, **kwargs):
        handle = original(*args, **kwargs)
        handles.append(handle)
        return handle
    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", tracked)
    request = client.build_request("POST", "/api/rooms/inputs", files=batch(photo))
    raw = request.read()
    messages = iter([
        {"type": "http.request", "body": raw[:-40], "more_body": True},
        {"type": "http.disconnect"},
    ])
    sent = []
    async def receive():
        return next(messages)
    async def send(message):
        sent.append(message)
    await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
               "method": "POST", "scheme": "http", "path": "/api/rooms/inputs", "query_string": b"",
               "headers": [(b"host", b"testserver"), (b"content-type", request.headers["content-type"].encode())],
               "server": ("testserver", 80), "client": ("127.0.0.1", 1234)}, receive, send)
    assert sent[0]["status"] == 400
    assert handles and all(handle.closed for handle in handles)
    assert not list(app.state.upload_service.storage.root.iterdir())


async def test_periodic_cleanup_removes_expired_input_without_another_request(tmp_path, photo, monkeypatch):
    app = create_app(Settings(_env_file=None, app_environment="test", upload_storage_dir=tmp_path / "periodic",
                              upload_cleanup_interval_seconds=1))
    storage = app.state.upload_service.storage
    now = datetime(2026, 10, 18, tzinfo=timezone.utc)
    storage.now = lambda: now
    removed = anyio.Event()
    original_remove = storage._remove
    def observe_remove(path):
        original_remove(path)
        anyio.from_thread.run_sync(removed.set)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            data = (await client.post("/api/rooms/inputs", files=batch(photo))).json()
            monkeypatch.setattr(storage, "_remove", observe_remove)
            storage.now = lambda: now + timedelta(hours=1)
            with anyio.fail_after(5):
                await removed.wait()
            assert not (storage.root / data["input_id"]).exists()
