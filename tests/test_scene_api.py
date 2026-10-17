"""ASGI integration tests for the stateless scene validation/preview endpoints."""

from copy import deepcopy
import json
from math import pi

import pytest
import httpx

from roometry.config import Settings
from roometry.main import create_app
from roometry.scenes.schemas import RoomScene
from roometry.scenes.validation import validate_geometry


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    application = create_app(Settings(_env_file=None, app_environment="test", cors_origins=[]))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://testserver",
    ) as test_client:
        yield test_client


def move_body(scene, object_id="desk_1", x=0.85, z=2.0):
    return {
        "scene": scene.model_dump(mode="json"),
        "edit": {"operation": "move", "object_id": object_id, "position": {"x": x, "z": z}},
    }


async def test_health_endpoint_still_works(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "roometry"}


async def test_validate_endpoint_returns_serializable_baseline_report(client, bedroom_scene):
    response = await client.post("/api/scenes/validate", json={"scene": bedroom_scene.model_dump(mode="json")})
    assert response.status_code == 200
    assert response.json() == validate_geometry(bedroom_scene).model_dump(mode="json")
    assert response.json()["valid"] is True
    assert response.json()["warnings"][0]["code"] == "baseline_scope"


async def test_geometry_failure_is_a_report_not_a_request_schema_error(client, bedroom_scene):
    body = {"scene": bedroom_scene.model_dump(mode="json")}
    body["scene"]["objects"][0]["position"]["x"] = -1.0
    response = await client.post("/api/scenes/validate", json=body)
    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert "object_outside_room" in {issue["code"] for issue in response.json()["errors"]}


async def test_move_endpoint_returns_valid_transformed_scene_and_child(client, bedroom_scene):
    body = move_body(bedroom_scene)
    original = deepcopy(body)
    response = await client.post("/api/scenes/preview-edit", json=body)
    assert response.status_code == 200
    data = response.json()
    assert data["accepted"] is True
    assert data["changed_object_ids"] == ["computer_1", "desk_1"]
    assert data["edit_errors"] == []
    assert data["base_validation"]["valid"] is True
    assert data["candidate_validation"]["valid"] is True
    transformed = RoomScene.model_validate(data["scene"])
    assert validate_geometry(transformed).valid
    assert next(obj for obj in transformed.objects if obj.id == "computer_1").position.x == 0.85
    assert body == original


async def test_rotation_endpoint_uses_radians_and_normalizes_output(client, supported_scene):
    body = {
        "scene": supported_scene.model_dump(mode="json"),
        "edit": {"operation": "rotate", "object_id": "desk_1", "rotation_y_rad": -pi / 2},
    }
    response = await client.post("/api/scenes/preview-edit", json=body)
    assert response.status_code == 200
    data = response.json()
    assert data["accepted"] is True
    child = next(obj for obj in data["scene"]["objects"] if obj["id"] == "computer_1")
    assert child["position"]["x"] == pytest.approx(1.9)
    assert child["position"]["z"] == pytest.approx(2.5)
    assert child["rotation_y_rad"] == pytest.approx((0.2 - pi / 2) % (2 * pi))


@pytest.mark.parametrize("object_id,code", [
    ("bed_1", "object_locked"), ("tv_1", "object_fixed"),
    ("computer_1", "unsupported_placement"), ("missing_1", "object_not_found"),
])
async def test_policy_rejections_return_unchanged_base(client, bedroom_scene, object_id, code):
    body = move_body(bedroom_scene, object_id)
    response = await client.post("/api/scenes/preview-edit", json=body)
    assert response.status_code == 200
    data = response.json()
    assert data["accepted"] is False
    assert data["scene"] == body["scene"]
    assert data["changed_object_ids"] == []
    assert data["candidate_validation"] is None
    assert data["edit_errors"][0]["code"] == code
    assert data["edit_errors"][0]["entity_ids"] == [object_id]


async def test_protected_child_failure_crosses_http_boundary_without_partial_changes(client, supported_scene):
    supported_scene.objects[1].locked = True
    body = move_body(supported_scene, x=2.2)
    response = await client.post("/api/scenes/preview-edit", json=body)
    assert response.status_code == 200
    data = response.json()
    assert data["accepted"] is False
    assert data["edit_errors"][0]["code"] == "protected_children"
    assert data["edit_errors"][0]["entity_ids"] == ["desk_1", "computer_1"]
    assert data["scene"] == body["scene"]


@pytest.mark.parametrize("x,code", [(-0.5, "object_outside_room"), (2.7, "object_overlap")])
async def test_candidate_geometry_rejection_returns_report_and_original_scene(client, bedroom_scene, x, code):
    body = move_body(bedroom_scene, x=x)
    response = await client.post("/api/scenes/preview-edit", json=body)
    assert response.status_code == 200
    data = response.json()
    assert data["accepted"] is False
    assert data["scene"] == body["scene"]
    assert data["changed_object_ids"] == []
    assert data["edit_errors"] == []
    assert data["base_validation"]["valid"] is True
    assert data["candidate_validation"]["valid"] is False
    assert code in {issue["code"] for issue in data["candidate_validation"]["errors"]}


async def test_child_coordinate_overflow_returns_rejection_instead_of_500(client, supported_scene):
    body = move_body(supported_scene, x=100.0)
    response = await client.post("/api/scenes/preview-edit", json=body)
    assert response.status_code == 200
    assert response.json()["accepted"] is False
    assert response.json()["edit_errors"][0]["code"] == "candidate_schema"
    assert response.json()["scene"] == body["scene"]


async def test_invalid_base_scene_is_not_replaced_by_an_edit(client, bedroom_scene):
    body = move_body(bedroom_scene)
    body["scene"]["objects"][0]["position"]["x"] = -1.0
    response = await client.post("/api/scenes/preview-edit", json=body)
    assert response.status_code == 200
    data = response.json()
    assert data["accepted"] is False
    assert data["base_validation"]["valid"] is False
    assert data["candidate_validation"] is None
    assert data["edit_errors"][0]["code"] == "invalid_base_geometry"
    assert data["scene"] == body["scene"]


async def test_preview_endpoint_has_no_implicit_saved_state(client, supported_scene):
    body = move_body(supported_scene, x=2.2)
    first = (await client.post("/api/scenes/preview-edit", json=body)).json()
    repeated = (await client.post("/api/scenes/preview-edit", json=body)).json()
    assert first == repeated
    noop = move_body(supported_scene, x=2.0)
    original = (await client.post("/api/scenes/preview-edit", json=noop)).json()
    assert original["accepted"] is True
    assert original["scene"] == body["scene"]
    assert original["changed_object_ids"] == []


@pytest.mark.parametrize("edit", [
    {}, {"operation": "delete", "object_id": "desk_1"},
    {"operation": "move", "object_id": "desk_1"},
    {"operation": "rotate", "object_id": "desk_1"},
    {"operation": "rotate", "object_id": "desk_1", "rotation_y_rad": "1.0"},
    {"operation": "rotate", "object_id": "desk_1", "rotation_y_rad": True},
    {"operation": "rotate", "object_id": "desk_1", "rotation_y_rad": None},
    {"operation": "move", "object_id": "desk_1", "position": {"x": "1.0", "z": 2.0}},
    {"operation": "move", "object_id": "desk_1", "position": {"x": True, "z": 2.0}},
    {"operation": "move", "object_id": "desk_1", "position": {"x": 1.0}},
    {"operation": "move", "object_id": "desk_1", "position": {"x": 101.0, "z": 2.0}},
    {"operation": "move", "object_id": "desk_1", "position": {"x": 1.0, "y": 1.0, "z": 2.0}},
    {"operation": "rotate", "object_id": "desk_1", "rotation_y_rad": 1.0, "locked": False},
    {"operation": "rotate", "object_id": "desk_1", "rotation_y_rad": 1.0, "fixed": False},
    {"operation": "rotate", "object_id": "desk_1", "rotation_y_rad": 1.0, "color": "#000000"},
    {"operation": "rotate", "object_id": "desk_1", "rotation_y_rad": 1.0, "dimensions_m": {}},
    {"operation": "rotate", "object_id": "INVALID ID", "rotation_y_rad": 1.0},
])
async def test_invalid_edit_contract_is_http_422(client, bedroom_scene, edit):
    response = await client.post("/api/scenes/preview-edit", json={"scene": bedroom_scene.model_dump(mode="json"), "edit": edit})
    assert response.status_code == 422
    assert response.json()["detail"]


@pytest.mark.parametrize("path", ["/api/scenes/validate", "/api/scenes/preview-edit"])
@pytest.mark.parametrize("mutation", ["missing_scene", "invalid_reference", "extra_field", "bad_version"])
async def test_invalid_scene_contract_is_http_422(client, bedroom_scene, path, mutation):
    body = move_body(bedroom_scene)
    if path.endswith("validate"):
        body.pop("edit")
    if mutation == "missing_scene":
        body.pop("scene")
    elif mutation == "invalid_reference":
        body["scene"]["objects"][3]["support_object_id"] = "missing_1"
    elif mutation == "extra_field":
        body["scene"]["objects"][0]["collision_disabled"] = True
    else:
        body["scene"]["schema_version"] = True
    response = await client.post(path, json=body)
    assert response.status_code == 422
    assert response.json()["detail"]


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e309", "-1e309"])
async def test_nonfinite_json_tokens_return_validation_error_instead_of_server_error(client, bedroom_scene, token):
    body = move_body(bedroom_scene)
    body["edit"] = {"operation": "rotate", "object_id": "desk_1", "rotation_y_rad": "NUMBER_TOKEN"}
    content = json.dumps(body).replace('"NUMBER_TOKEN"', token)
    response = await client.post("/api/scenes/preview-edit", content=content, headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.json()["detail"]
    assert all(set(issue) == {"type", "loc", "msg"} for issue in response.json()["detail"])


async def test_scene_nonfinite_values_also_return_safe_http_422(client, bedroom_scene):
    body = {"scene": bedroom_scene.model_dump(mode="json")}
    body["scene"]["room"]["width_m"] = "NUMBER_TOKEN"
    content = json.dumps(body).replace('"NUMBER_TOKEN"', '1e309')
    response = await client.post(
        "/api/scenes/validate", content=content, headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "scene", "room", "width_m"]


async def test_rejection_preserves_normalized_base_not_raw_json_spelling(client, bedroom_scene):
    body = move_body(bedroom_scene, object_id="bed_1")
    body["scene"]["objects"][0]["color"] = "#abcdef"
    response = await client.post("/api/scenes/preview-edit", json=body)
    assert response.status_code == 200
    data = response.json()
    assert data["accepted"] is False
    assert data["scene"] == RoomScene.model_validate(body["scene"]).model_dump(mode="json")


async def test_malformed_json_returns_request_error(client):
    response = await client.post("/api/scenes/preview-edit", content="{bad json", headers={"Content-Type": "application/json"})
    assert response.status_code == 422


async def test_openapi_exposes_typed_routes_and_discriminated_edit_operations(client):
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert "/api/health" in schema["paths"]
    assert "/api/scenes/validate" in schema["paths"]
    assert "/api/scenes/preview-edit" in schema["paths"]
    edit = schema["components"]["schemas"]["PreviewEditRequest"]["properties"]["edit"]
    assert edit["discriminator"]["propertyName"] == "operation"
    assert set(edit["discriminator"]["mapping"]) == {"move", "rotate"}
