"""Scene reconstruction invariants and the paid-call/ownership boundary; no live API."""

import json
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import anyio
import httpx
import pytest
from pydantic import ValidationError

from roometry.scenes.schemas import RoomScene
from roometry.scenes.validation import validate_geometry
from roometry.uploads.schemas import InputDimensions
from roometry.vision.proposal_schemas import (
    SceneDraft,
    SceneProposalRequest,
    SceneProposalResponse,
)
from roometry.vision.proposals import assemble, preflight, resolve_measurements
from roometry.vision.schemas import EstimatedRoomDimensions, RoomAnalysisResponse
from tests import test_vision_api
from tests.test_vision_api import assert_error, provider_response, upload

# Reuse the real-upload/SDK transport fixtures for the second analysis stage.
anyio_backend = test_vision_api.anyio_backend
isolated_environment = test_vision_api.isolated_environment
harness = test_vision_api.harness

pytestmark = pytest.mark.anyio
ROOT = Path(__file__).resolve().parents[1]


def source():
    return RoomAnalysisResponse.model_validate_json(
        (ROOT / "data/fixtures/room_analysis.json").read_text()
    )


def draft_data():
    def obj(id, x, y, z, w, d, h, placement="floor", support=None):
        return {
            "id": id,
            "position": {"x": x, "y": y, "z": z},
            "rotation_y_rad": 0,
            "dimensions_m": {"width": w, "depth": d, "height": h},
            "placement": placement,
            "support_object_id": support,
            "wall": None,
            "opening_id": None,
        }

    return {
        "wall_assignments": [
            {"observation_id": "window_wall", "wall": "north"},
            {"observation_id": "door_wall", "wall": "east"},
        ],
        "objects": [
            obj("bed_observed", 0.8, 0, 2.8, 1.4, 2, 0.5),
            obj("desk_observed", 3, 0, 0.6, 1.2, 0.6, 0.75),
            obj(
                "computer_observed",
                3,
                0.75,
                0.6,
                0.4,
                0.3,
                0.45,
                "surface",
                "desk_observed",
            ),
            obj("chair_a", 2.4, 0, 2, 0.4, 0.4, 0.8),
            obj("chair_b", 3.2, 0, 2.8, 0.4, 0.4, 0.8),
        ],
        "openings": [
            {
                "id": "window_observed",
                "wall": "north",
                "offset_m": 0.5,
                "width_m": 1.2,
                "height_m": 1.2,
                "sill_height_m": 1.1,
            },
            {
                "id": "door_observed",
                "wall": "east",
                "offset_m": 2.5,
                "width_m": 0.8,
                "height_m": 2,
                "sill_height_m": 0,
            },
        ],
        "assumptions": [],
        "warnings": [],
    }


def measurements(analysis=None):
    return resolve_measurements(
        analysis or source(), {"length_m": 4, "height_m": 2.6}, False
    )


def scene_action(data=None, *, text=None):
    async def action(request, payload):
        assert payload["text"]["format"]["name"] == "room_scene_draft"
        # Reuse a genuine SDK response envelope without feeding scene context to
        # the photo-response helper, which expects image evidence.
        fake = {
            **payload,
            "input": [
                {
                    "content": [
                        {
                            "text": json.dumps(
                                {"image_ids": source().observations.image_ids}
                            )
                        }
                    ]
                }
            ],
        }
        body = provider_response(fake)
        body["output"][0]["content"][0]["text"] = (
            text
            if text is not None
            else json.dumps(data if data is not None else draft_data())
        )
        return httpx.Response(200, json=body)

    return action


async def analyzed(harness):
    _app, client, _calls, _actions = harness
    batch = await upload(client)
    response = await client.post(
        "/api/rooms/analyze", json={"input_id": batch["input_id"]}
    )
    assert response.status_code == 200, response.text
    return response.json()


def body(analysis, **kwargs):
    return {"analysis_id": analysis["analysis_id"], **kwargs}


def confirmed(**values):
    return {"measurements_source": "user_confirmed", **values}


async def test_measurement_confirmation_cached_analysis_and_ready_scene(harness):
    app, client, calls, actions = harness
    analysis = await analyzed(harness)
    response = await client.post("/api/rooms/propose-scene", json=body(analysis))
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "needs_dimensions"
    assert response.json()["dimensions_to_confirm"] == ["length_m", "height_m"]
    assert response.json()["dimensions"]["width_m"] == 4
    assert len(calls) == 1
    response = await client.post(
        "/api/rooms/propose-scene",
        json=body(analysis, dimensions=confirmed(length_m=4)),
    )
    assert response.json()["dimensions_to_confirm"] == ["height_m"]
    assert len(calls) == 1
    actions.append(scene_action())
    response = await client.post(
        "/api/rooms/propose-scene",
        json=body(analysis, dimensions=confirmed(height_m=2.6)),
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "ready", data
    assert data["validation"]["valid"] is True
    scene = RoomScene.model_validate(data["scene"])
    assert validate_geometry(scene).valid
    assert scene.room.width_m == scene.room.length_m == 4
    assert scene.room.measurements_source == "user_confirmed"
    assert {obj.id for obj in scene.objects} == {
        obj["id"] for obj in analysis["observations"]["objects"]
    }
    assert all(
        obj.measurements_source == "estimated" and obj.provenance == "photo_observed"
        for obj in scene.objects
    )
    assert any("duplicate" in warning for warning in scene.warnings)
    assert scene.objects[2].support_object_id == "desk_observed"
    assert scene.objects[2].color == "#000000"
    assert len(calls) == 2
    request = calls[-1]
    assert request["store"] is False and request["tools"] == []
    assert request["model"] == "gpt-6-luna"
    assert request["text"]["format"]["strict"] is True
    assert all(part["type"] == "input_text" for part in request["input"][0]["content"])
    assert "data:image" not in json.dumps(request)
    response = await client.post("/api/rooms/propose-scene", json=body(analysis))
    assert response.json() == data
    assert response.headers["cache-control"] == "no-store"
    assert len(calls) == 2
    assert [record.stage for record in app.state.analysis_service.usage_records] == [
        "analysis",
        "scene_proposal",
    ]
    assert [record.attempt for record in app.state.analysis_service.usage_records] == [
        1,
        1,
    ]


async def test_confirmed_dimensions_win_over_estimates_and_partial_values_do_not_erase():
    analysis = source()
    analysis.observations.estimated_dimensions = EstimatedRoomDimensions(
        width_m=9, length_m=8, height_m=2.7
    )
    # Revalidate strict nested models before exercising the resolver.
    analysis = RoomAnalysisResponse.model_validate(analysis.model_dump())
    result = resolve_measurements(analysis, {"height_m": 2.6}, False)
    assert result.dimensions.width_m == result.dimensions.length_m == 4
    assert result.dimensions.height_m == 2.6
    assert result.estimated == result.to_confirm == []
    result = resolve_measurements(analysis, {}, True)
    assert result.dimensions.width_m == result.dimensions.length_m == 4
    assert result.dimensions.height_m == 2.7
    assert result.estimated == ["height_m"]
    assert result.dimensions.measurements_source == "estimated"
    assert resolve_measurements(analysis, {}, False).to_confirm == ["height_m"]


async def test_explicit_acceptance_of_estimated_dimensions(harness):
    app, client, _calls, actions = harness
    analysis = await analyzed(harness)
    state = app.state.analysis_service._states[analysis["input_id"]]
    state.result.observations.estimated_dimensions = (
        source().observations.estimated_dimensions
    )
    # No estimate exists in the fixture: consent must not invent a ceiling.
    result = await client.post(
        "/api/rooms/propose-scene", json=body(analysis, allow_estimated_dimensions=True)
    )
    assert result.json()["dimensions_to_confirm"] == ["length_m", "height_m"]
    state.result.observations.estimated_dimensions = EstimatedRoomDimensions(
        width_m=9, length_m=4, height_m=2.6
    )
    actions.append(scene_action())
    result = await client.post(
        "/api/rooms/propose-scene", json=body(analysis, allow_estimated_dimensions=True)
    )
    data = result.json()
    assert data["status"] == "ready", data
    assert data["estimated_axes"] == ["length_m", "height_m"]
    assert data["scene"]["room"]["width_m"] == 4
    assert data["scene"]["room"]["measurements_source"] == "estimated"
    assert any("Unconfirmed" in note for note in data["scene"]["assumptions"])


@pytest.mark.parametrize(
    "defect",
    [
        "collision",
        "missing_object",
        "unknown_id",
        "wall",
        "support",
        "shrink",
        "opening",
        "schema",
        "json",
    ],
)
async def test_one_bounded_repair_preserves_authority(harness, defect):
    app, client, calls, actions = harness
    analysis = await analyzed(harness)
    bad = draft_data()
    if defect == "collision":
        bad["objects"][4]["position"] = deepcopy(bad["objects"][3]["position"])
    if defect == "missing_object":
        bad["objects"].pop()
    if defect == "unknown_id":
        bad["objects"][0]["id"] = "invented_bed"
    if defect == "wall":
        bad["openings"][0]["wall"] = "south"
    if defect == "support":
        bad["objects"][2]["support_object_id"] = "bed_observed"
    if defect == "shrink":
        bad["objects"][0]["dimensions_m"]["width"] = 0.3
    if defect == "opening":
        bad["openings"][0]["width_m"] = 0.2
    if defect == "schema":
        bad["objects"][0]["fixed"] = False
    actions.extend(
        [
            scene_action(bad, text="not-json" if defect == "json" else None),
            scene_action(),
        ]
    )
    response = await client.post(
        "/api/rooms/propose-scene",
        json=body(analysis, dimensions=confirmed(length_m=4, height_m=2.6)),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ready", response.text
    assert len(calls) == 3
    context = json.loads(calls[-1]["input"][0]["content"][0]["text"])
    assert context["validation_feedback"]
    assert context["room_dimensions"]["width_m"] == 4
    assert [
        r.attempt
        for r in app.state.analysis_service.usage_records
        if r.stage == "scene_proposal"
    ] == [1, 2]


async def test_failed_repair_returns_incomplete_and_never_exposes_invalid_scene(
    harness,
    caplog,
):
    _app, client, calls, actions = harness
    analysis = await analyzed(harness)
    bad = draft_data()
    bad["objects"][0]["position"]["x"] = -3
    actions.extend([scene_action(bad), scene_action(bad)])
    req = body(analysis, dimensions=confirmed(length_m=4, height_m=2.6))
    response = await client.post("/api/rooms/propose-scene", json=req)
    data = response.json()
    assert data["status"] == "incomplete" and data["scene"] is None
    assert data["validation"]["valid"] is False
    assert data["issues"][0]["code"] == "geometry"
    assert response.status_code == 200
    assert '"stage": "scene_proposal"' in caplog.text
    assert "object_outside_room" in caplog.text
    assert len(calls) == 3
    assert (await client.post("/api/rooms/propose-scene", json=req)).json() == data
    # Revising a dimension cannot reset the repair/paid-call budget.
    response = await client.post(
        "/api/rooms/propose-scene", json=body(analysis, dimensions=confirmed(width_m=5))
    )
    assert response.json()["issues"][0]["code"] == "proposal_limit_reached"
    assert len(calls) == 3


@pytest.mark.parametrize(
    "kind",
    ["non_rectangular", "unknown_object", "unsupported_placement", "no_evidence"],
)
async def test_unsupported_evidence_returns_incomplete_without_model(harness, kind):
    app, client, calls, _actions = harness
    analysis = await analyzed(harness)
    obs = app.state.analysis_service._states[analysis["input_id"]].result.observations
    if kind == "non_rectangular":
        obs.room_shape = "non_rectangular"
    if kind == "unknown_object":
        obs.objects[0].type = None
    if kind == "unsupported_placement":
        obs.objects[0].placement = "ceiling"
    if kind == "no_evidence":
        obs.objects, obs.openings, obs.walls, obs.possible_duplicates = [], [], [], []
        obs.floor_visible = None
    response = await client.post(
        "/api/rooms/propose-scene",
        json=body(analysis, dimensions=confirmed(length_m=4, height_m=2.6)),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "incomplete"
    assert response.json()["issues"]
    assert len(calls) == 1


@pytest.mark.parametrize(
    "invalid",
    [
        {"dimensions": {"height_m": 2.6}},
        {"dimensions": confirmed(height_m=-1)},
        {"dimensions": confirmed(width_m=True)},
        {"dimensions": confirmed(height_m=11)},
        {"allow_estimated_dimensions": "true"},
        {"scene": {}},
    ],
)
async def test_strict_confirmation_request(harness, invalid):
    _app, client, calls, _actions = harness
    analysis = await analyzed(harness)
    assert_error(
        await client.post("/api/rooms/propose-scene", json=body(analysis, **invalid)),
        422,
        "invalid_request",
    )
    assert len(calls) == 1


async def test_other_session_cannot_read_or_propose_cached_analysis(harness):
    _app, client, calls, _actions = harness
    analysis = await analyzed(harness)
    client.cookies.clear()
    assert_error(
        await client.post("/api/rooms/propose-scene", json=body(analysis)),
        404,
        "analysis_not_found",
    )
    assert len(calls) == 1


@pytest.mark.parametrize("revoke", ["delete", "replace", "expire"])
async def test_revoked_upload_revokes_analysis_and_prevents_scene_call(
    harness, revoke, monkeypatch
):
    app, client, calls, _actions = harness
    analysis = await analyzed(harness)
    storage = app.state.upload_service.storage
    if revoke == "delete":
        state = app.state.analysis_service._states[analysis["input_id"]]
        storage.delete(analysis["input_id"], state.owner)
    elif revoke == "replace":
        await upload(client)
    else:
        now = storage.now() + timedelta(hours=2)
        monkeypatch.setattr(storage, "now", lambda: now)
    response = await client.post(
        "/api/rooms/propose-scene",
        json=body(analysis, dimensions=confirmed(length_m=4, height_m=2.6)),
    )
    assert response.status_code == 404
    assert len(calls) == 1


async def test_revoke_during_proposal_discards_result_and_prevents_repair(harness):
    app, client, calls, actions = harness
    analysis = await analyzed(harness)

    async def revoke(request, payload):
        state = app.state.analysis_service._states[analysis["input_id"]]
        await anyio.to_thread.run_sync(
            app.state.upload_service.storage.delete, analysis["input_id"], state.owner
        )
        return await scene_action()(request, payload)

    actions.append(revoke)
    response = await client.post(
        "/api/rooms/propose-scene",
        json=body(analysis, dimensions=confirmed(length_m=4, height_m=2.6)),
    )
    assert response.status_code == 404
    assert len(calls) == 2


@pytest.mark.parametrize(
    "failure",
    ["timeout", "connection", "refusal", "incomplete", "rate_limit", "server"],
)
async def test_provider_failures_safe_and_do_not_automatically_repeat(harness, failure):
    app, client, calls, actions = harness
    analysis = await analyzed(harness)

    async def fail(request, payload):
        if failure == "timeout":
            raise httpx.ReadTimeout("private details", request=request)
        if failure == "connection":
            raise httpx.ConnectError("private details", request=request)
        if failure in {"rate_limit", "server"}:
            return httpx.Response(
                429 if failure == "rate_limit" else 503,
                json={"error": {"message": "private details"}},
            )
        response = await scene_action()(request, payload)
        data = response.json()
        if failure == "refusal":
            data["output"][0]["content"] = [
                {"type": "refusal", "refusal": "private details"}
            ]
        if failure == "incomplete":
            data["status"] = "incomplete"
        return httpx.Response(200, json=data)

    actions.append(fail)
    response = await client.post(
        "/api/rooms/propose-scene",
        json=body(analysis, dimensions=confirmed(length_m=4, height_m=2.6)),
    )
    assert response.status_code in {422, 429, 502, 503, 504}
    assert "private details" not in response.text
    assert len(calls) == 2
    assert app.state.analysis_service.usage_records[-1].stage == "scene_proposal"


async def test_same_provider_capacity_for_analysis_and_scene(harness):
    _app, client, _calls, actions = harness
    analysis = await analyzed(harness)
    entered, release = anyio.Event(), anyio.Event()

    async def slow(request, payload):
        entered.set()
        await release.wait()
        return await scene_action()(request, payload)

    actions.append(slow)

    async def run():
        response = await client.post(
            "/api/rooms/propose-scene",
            json=body(analysis, dimensions=confirmed(length_m=4, height_m=2.6)),
        )
        assert response.json()["status"] == "ready"

    async with anyio.create_task_group() as group:
        group.start_soon(run)
        await entered.wait()
        assert_error(
            await client.post(
                "/api/rooms/analyze", json={"input_id": analysis["input_id"]}
            ),
            429,
            "analysis_busy",
        )
        release.set()


async def test_scene_assembly_fixed_flags_and_models_remain_backend_authority():
    analysis = source()
    analysis.observations.objects[0].appears_fixed = True
    scene, report, issues = assemble(
        analysis, measurements(analysis), SceneDraft.model_validate(draft_data())
    )
    assert scene and report and report.valid and not issues
    assert scene.objects[0].fixed is True
    assert scene.objects[0].locked is False
    assert all(obj.asset_id == f"{obj.type}_standard_01" for obj in scene.objects)
    assert preflight(analysis) == []


async def test_openapi_documents_confirmation_and_validation_result(harness):
    app, *_ = harness
    operation = app.openapi()["paths"]["/api/rooms/propose-scene"]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/SceneProposalRequest")
    assert "200" in operation["responses"] and "404" in operation["responses"]


async def test_response_serialization_preserves_validation_and_rejects_contradictions(
    harness,
):
    _, client, _, actions = harness
    analysis = await analyzed(harness)
    actions.append(scene_action())
    response = await client.post(
        "/api/rooms/propose-scene",
        json=body(analysis, dimensions=confirmed(length_m=4, height_m=2.6)),
    )
    parsed = SceneProposalResponse.model_validate_json(response.content)
    assert parsed.validation and parsed.validation.valid
    for patch in (
        {"scene": None},
        {"dimensions_to_confirm": ["height_m"]},
        {"estimated_axes": ["width_m"]},
    ):
        with pytest.raises(ValidationError):
            SceneProposalResponse.model_validate_json(
                json.dumps({**response.json(), **patch})
            )
    data = response.json()
    data["validation"]["valid"] = False
    with pytest.raises(ValidationError):
        SceneProposalResponse.model_validate_json(json.dumps(data))


async def test_explicit_measurement_correction_preserves_other_confirmations(harness):
    _, client, calls, actions = harness
    analysis = await analyzed(harness)
    await client.post(
        "/api/rooms/propose-scene",
        json=body(analysis, dimensions=confirmed(length_m=4)),
    )
    actions.append(scene_action())
    response = await client.post(
        "/api/rooms/propose-scene",
        json=body(analysis, dimensions=confirmed(width_m=5, height_m=2.6)),
    )
    assert response.json()["status"] == "ready"
    assert response.json()["scene"]["room"]["width_m"] == 5
    assert response.json()["scene"]["room"]["length_m"] == 4
    assert len(calls) == 2


async def test_cached_proposal_is_not_mutable_through_service_return(harness):
    app, _, calls, actions = harness
    analysis = await analyzed(harness)
    state = app.state.analysis_service._states[analysis["input_id"]]
    actions.append(scene_action())
    request = SceneProposalRequest(
        analysis_id=analysis["analysis_id"],
        dimensions=InputDimensions(
            length_m=4, height_m=2.6, measurements_source="user_confirmed"
        ),
    )
    result = await app.state.analysis_service.propose(request, state.owner)
    assert result.scene
    result.scene.objects.clear()
    cached = await app.state.analysis_service.propose(request, state.owner)
    assert cached.scene and len(cached.scene.objects) == 5
    assert len(calls) == 2


async def test_proposal_body_caps_and_origin_enforced_before_paid_calls(harness):
    _, client, calls, _ = harness
    analysis = await analyzed(harness)
    assert_error(
        await client.post(
            "/api/rooms/propose-scene",
            json=body(analysis),
            headers={"Origin": "https://untrusted.example"},
        ),
        403,
        "forbidden_origin",
    )
    assert_error(
        await client.post(
            "/api/rooms/propose-scene",
            content=b"x" * 5000,
            headers={"Content-Type": "application/json"},
        ),
        413,
        "request_too_large",
    )
    assert_error(
        await client.post(
            "/api/rooms/propose-scene",
            content='{"analysis_id":"x","analysis_id":"y"}',
            headers={"Content-Type": "application/json"},
        ),
        422,
        "invalid_request",
    )
    assert_error(
        await client.post("/api/rooms/propose-scene", content="{}"),
        415,
        "unsupported_media_type",
    )
    assert len(calls) == 1


async def test_proposal_timeout_consumes_attempt_and_releases_capacity(harness):
    app, client, calls, actions = harness
    analysis = await analyzed(harness)
    app.state.settings.analysis_timeout_seconds = 0.02

    async def slow(request, payload):
        await anyio.sleep(1)
        return await scene_action()(request, payload)

    actions.append(slow)
    req = body(analysis, dimensions=confirmed(length_m=4, height_m=2.6))
    assert_error(
        await client.post("/api/rooms/propose-scene", json=req), 504, "analysis_timeout"
    )
    state = app.state.analysis_service._states[analysis["input_id"]]
    assert state.scene_attempts == 1
    assert app.state.analysis_service.usage_records[-1].outcome == "analysis_timeout"
    app.state.settings.analysis_timeout_seconds = 90
    actions.append(scene_action())
    response = await client.post("/api/rooms/propose-scene", json=req)
    assert response.json()["status"] == "ready"
    assert len(calls) == 3


async def test_slow_first_scene_attempt_does_not_starve_repair(harness):
    app, client, calls, actions = harness
    analysis = await analyzed(harness)
    app.state.settings.analysis_timeout_seconds = 0.4
    bad = draft_data()
    bad["openings"][0]["wall"] = "south"

    def delayed(data):
        async def action(request, payload):
            await anyio.sleep(0.25)
            return await scene_action(data)(request, payload)

        return action

    actions.extend([delayed(bad), delayed(draft_data())])
    response = await client.post(
        "/api/rooms/propose-scene",
        json=body(analysis, dimensions=confirmed(length_m=4, height_m=2.6)),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ready"
    assert len(calls) == 3
    feedback = json.loads(calls[-1]["input"][0]["content"][0]["text"])[
        "validation_feedback"
    ]
    assert feedback[0]["code"] == "wall_mapping"
    assert feedback[0]["entity_ids"] == ["window_observed"]
    assert "north" in feedback[0]["message"]


async def test_expired_analysis_cleanup_removes_proposal_and_confirmations(
    harness, monkeypatch
):
    app, client, _, actions = harness
    analysis = await analyzed(harness)
    actions.append(scene_action())
    response = await client.post(
        "/api/rooms/propose-scene",
        json=body(analysis, dimensions=confirmed(length_m=4, height_m=2.6)),
    )
    assert response.json()["status"] == "ready"
    storage = app.state.upload_service.storage
    expired = storage.now() + timedelta(hours=2)
    monkeypatch.setattr(storage, "now", lambda: expired)
    await app.state.analysis_service.cleanup()
    assert not app.state.analysis_service._states


@pytest.mark.parametrize("placement", ["floor", "surface", "wall", "ceiling"])
async def test_attachment_coordinates_are_derived_without_repairing_free_layout(
    placement,
):
    analysis, data = source(), draft_data()
    if placement == "surface":
        data["objects"][2]["position"]["y"] = 0.76
        object_id = "computer_observed"
    else:
        observed = analysis.observations.objects[0]
        obj = data["objects"][0]
        object_id = obj["id"]
        if placement == "floor":
            obj["position"]["y"] = 0.1
        else:
            observed.type = "clock" if placement == "wall" else "chandelier"
            observed.placement = placement
            observed.estimated_dimensions_m = None
            obj["placement"] = placement
            obj["dimensions_m"] = {"width": 0.3, "depth": 0.1, "height": 0.3}
            if placement == "wall":
                observed.wall_id = "window_wall"
                obj["wall"] = "north"
                obj["position"] = {"x": 3, "y": 2, "z": 0.12}
                obj["rotation_y_rad"] = 0.1
            else:
                obj["position"] = {"x": 2, "y": 2, "z": 3.5}
    draft = SceneDraft.model_validate(data)
    original = draft.model_dump()
    scene, report, problems = assemble(analysis, measurements(analysis), draft)
    assert scene is not None and report is not None and report.valid and not problems
    obj = next(obj for obj in scene.objects if obj.id == object_id)
    assert (
        obj.dimensions_m.model_dump()
        == original["objects"][2 if placement == "surface" else 0]["dimensions_m"]
    )
    if placement == "surface":
        assert obj.position.y == 0.75
    if placement == "floor":
        assert obj.position.y == 0
    if placement == "wall":
        assert obj.position.z == 0.05 and obj.rotation_y_rad == 0
    if placement == "ceiling":
        assert obj.position.y == pytest.approx(2.3)
    assert draft.model_dump() == original


async def test_attachment_normalization_keeps_outside_objects_rejected():
    analysis, data = source(), draft_data()
    data["objects"][2]["position"] = {"x": 20, "y": 0.76, "z": 0.6}
    scene, report, _problems = assemble(
        analysis, measurements(analysis), SceneDraft.model_validate(data)
    )
    assert scene is None and report is not None and not report.valid
    assert {"object_outside_room", "surface_support"} <= {
        error.code for error in report.errors
    }


async def test_unknown_surface_support_returns_specific_repair_feedback():
    analysis, data = source(), draft_data()
    # An occluded or architectural support has not yet been identified.
    analysis.observations.objects[2].support_object_id = None
    data["objects"][2]["support_object_id"] = None
    scene, report, issues = assemble(
        analysis, measurements(analysis), SceneDraft.model_validate(data)
    )
    assert scene is None and report is None
    assert issues[0].entity_ids == ["computer_observed"]
    assert "support" in issues[0].message


async def test_visible_window_sill_can_use_existing_shelf_asset_as_support():
    analysis = source()
    source_data = analysis.observations.model_dump()
    example = source_data["objects"][0]
    source_data["objects"] = [
        {
            **example,
            "id": "window_sill",
            "type": "wall_shelf",
            "label": "Visible window sill",
            "placement": "wall",
            "wall_id": "window_wall",
            "support_object_id": None,
            "estimated_dimensions_m": None,
        },
        {
            **example,
            "id": "plant_on_sill",
            "type": "plant",
            "label": "Plant on window sill",
            "placement": "surface",
            "wall_id": "window_wall",
            "support_object_id": "window_sill",
            "estimated_dimensions_m": None,
        },
    ]
    source_data["possible_duplicates"] = []
    source_data["openings"] = [source_data["openings"][0]]
    from roometry.vision.schemas import RoomObservations

    analysis.observations = RoomObservations.model_validate(source_data)
    data = draft_data()
    data["objects"] = [
        {
            "id": "window_sill",
            "position": {"x": 1.1, "y": 1.06, "z": 0.12},
            "rotation_y_rad": 0.01,
            "dimensions_m": {"width": 1.2, "depth": 0.24, "height": 0.04},
            "placement": "wall",
            "wall": "north",
            "support_object_id": None,
            "opening_id": None,
        },
        {
            "id": "plant_on_sill",
            "position": {"x": 1.1, "y": 1.11, "z": 0.12},
            "rotation_y_rad": 0,
            "dimensions_m": {"width": 0.15, "depth": 0.15, "height": 0.3},
            "placement": "surface",
            "wall": None,
            "support_object_id": "window_sill",
            "opening_id": None,
        },
    ]
    data["openings"] = [data["openings"][0]]
    scene, report, problems = assemble(
        analysis, measurements(analysis), SceneDraft.model_validate(data)
    )
    assert scene is not None and report is not None and report.valid and not problems
    assert scene.objects[0].asset_id == "wall_shelf_standard_01"
    assert scene.objects[1].position.y == pytest.approx(1.1)


def curtained_door_case():
    analysis = source().model_dump()
    observations = analysis["observations"]
    observations["objects"] = [
        {
            **observations["objects"][0],
            "id": "curtains_observed",
            "type": "window_covering",
            "label": "Curtains at glazed patio door",
            "placement": "wall",
            "wall_id": "door_wall",
            "support_object_id": None,
            "estimated_dimensions_m": None,
        }
    ]
    observations["openings"] = observations["openings"][1:]
    observations["walls"] = observations["walls"][1:]
    observations["possible_duplicates"] = []
    data = draft_data()
    data["wall_assignments"] = data["wall_assignments"][1:]
    data["openings"] = data["openings"][1:]
    data["objects"] = [
        {
            **data["objects"][0],
            "id": "curtains_observed",
            "placement": "wall",
            "wall": "east",
            "opening_id": "door_observed",
            "position": {"x": 3.975, "y": 0, "z": 2.9},
            "dimensions_m": {"width": 1, "depth": 0.05, "height": 2.15},
        }
    ]
    return RoomAnalysisResponse.model_validate(analysis), data


async def test_scene_assembly_preserves_glazed_door_with_curtains():
    analysis, data = curtained_door_case()
    scene, report, issues = assemble(
        analysis, measurements(analysis), SceneDraft.model_validate(data)
    )
    assert scene is not None and report is not None and report.valid and not issues
    assert scene.openings[0].id == "door_observed" and scene.openings[0].type == "door"
    assert scene.objects[0].opening_id == "door_observed"


@pytest.mark.parametrize(
    "defect", ["missing_link", "unknown_link", "wrong_wall", "door_sill"]
)
async def test_opening_errors_identify_the_entity_and_repair_needed(defect):
    analysis, data = curtained_door_case()
    if defect == "missing_link":
        data["objects"][0]["opening_id"] = None
    elif defect == "unknown_link":
        data["objects"][0]["opening_id"] = "missing_opening"
    elif defect == "wrong_wall":
        analysis.observations.objects[0].wall_id = None
        data["objects"][0]["wall"] = "north"
    else:
        analysis.observations.openings[0].estimated_sill_height_m = None
        data["openings"][0]["sill_height_m"] = 0.1
    scene, report, issues = assemble(
        analysis, measurements(analysis), SceneDraft.model_validate(data)
    )
    assert scene is None and report is None
    assert issues[0].entity_ids[0] == (
        "door_observed" if defect == "door_sill" else "curtains_observed"
    )
    assert (
        "sill_height_m=0"
        if defect == "door_sill"
        else "east"
        if defect == "wrong_wall"
        else "opening_id"
    ) in issues[0].message
    assert "assembled scene violates" not in issues[0].message


async def test_api_repairs_curtain_opening_link_without_reanalyzing(
    harness, monkeypatch
):
    _, client, calls, actions = harness
    original, data = curtained_door_case()
    monkeypatch.setattr(test_vision_api, "SOURCE", original.observations.model_dump())
    analysis = await analyzed(harness)
    bad = deepcopy(data)
    bad["objects"][0]["opening_id"] = "missing_opening"
    actions.extend([scene_action(bad), scene_action(data)])
    response = await client.post(
        "/api/rooms/propose-scene",
        json=body(analysis, dimensions=confirmed(length_m=4, height_m=2.6)),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ready"
    assert response.json()["scene"]["openings"][0]["type"] == "door"
    assert len(calls) == 3
    context = json.loads(calls[-1]["input"][0]["content"][0]["text"])
    assert context["asset_bindings"][0]["opening_ids"] == ["door_observed"]
    assert context["validation_feedback"][0]["entity_ids"] == ["curtains_observed"]
    assert "opening_id" in context["validation_feedback"][0]["message"]
