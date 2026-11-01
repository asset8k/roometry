from hashlib import sha256
from types import SimpleNamespace

import httpx
import pytest

from roometry.projects.storage import SupabaseProjectPhotos
from roometry.config import Settings


def test_private_photos_write_read_and_delete():
    objects = {}
    def handler(request):
        assert request.headers["authorization"] == "Bearer secret"
        path = request.url.path
        if request.method == "POST" and "/object/list/" in path:
            import json
            batch = json.loads(request.content)["prefix"]
            return httpx.Response(200, json=[{"name": key.split("/")[1]} for key in objects if key.startswith(batch + "/")])
        if request.method == "POST":
            key = path.split("/object/room-photos/")[1]
            objects[key] = request.content
            return httpx.Response(200, json={"Key": key})
        if request.method == "GET":
            key = path.split("/object/authenticated/room-photos/")[1]
            return httpx.Response(200, content=objects[key])
        if request.method == "DELETE":
            import json
            for key in json.loads(request.content)["prefixes"]:
                objects.pop(key, None)
            return httpx.Response(200, json=[])
        raise AssertionError(path)

    store = SupabaseProjectPhotos("https://example.supabase.co", "room-photos", "secret", transport=httpx.MockTransport(handler))
    metadata = SimpleNamespace(image_id="source", prepared_width_px=512, prepared_height_px=512)
    batch, records = store.write([(metadata, b"photo")])
    assert store.read(records[0]["storage_key"], sha256(b"photo").hexdigest()) == b"photo"
    with pytest.raises(OSError, match="integrity"):
        store.read(records[0]["storage_key"], sha256(b"other").hexdigest())
    store.discard(batch)
    assert objects == {}
    with pytest.raises(OSError, match="Invalid"):
        store.read("../other/photo.jpg", "x")


def test_storage_errors_do_not_expose_provider_response():
    store = SupabaseProjectPhotos("https://example.supabase.co", "room-photos", "secret", transport=httpx.MockTransport(lambda request: httpx.Response(403, text="private details")))
    with pytest.raises(OSError, match="unavailable") as error:
        store.discard("e7e452c9-d8cc-4771-9cb6-6f04a35d070d")
    assert "private details" not in str(error.value)


def test_partial_upload_is_cleaned_up():
    objects = {}
    uploads = 0
    def handler(request):
        nonlocal uploads
        import json
        path = request.url.path
        if "/object/list/" in path:
            return httpx.Response(200, json=[{"name": key.split("/")[1]} for key in objects])
        if request.method == "POST":
            uploads += 1
            if uploads == 2:
                return httpx.Response(503)
            objects[path.split("/object/room-photos/")[1]] = request.content
            return httpx.Response(200)
        if request.method == "DELETE":
            for key in json.loads(request.content)["prefixes"]:
                objects.pop(key)
            return httpx.Response(200, json=[])
        raise AssertionError(path)
    store = SupabaseProjectPhotos("https://example.supabase.co", "room-photos", "secret", transport=httpx.MockTransport(handler))
    metadata = SimpleNamespace(image_id="source", prepared_width_px=512, prepared_height_px=512)
    with pytest.raises(OSError):
        store.write([(metadata, b"one"), (metadata, b"two")])
    assert objects == {}


def test_supabase_storage_requires_valid_private_configuration():
    with pytest.raises(ValueError, match="HTTPS"):
        Settings(_env_file=None, project_storage_backend="supabase", supabase_url="http://example.com", supabase_storage_secret_key="secret")
    with pytest.raises(ValueError, match="secret key"):
        Settings(_env_file=None, project_storage_backend="supabase", supabase_url="https://example.supabase.co")
    with pytest.raises(ValueError, match="bucket"):
        Settings(_env_file=None, project_storage_backend="supabase", supabase_url="https://example.supabase.co", supabase_storage_secret_key="secret", supabase_storage_bucket="../other")
