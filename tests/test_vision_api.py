"""Real uploads + actual OpenAI SDK over a mocked HTTP transport; no live calls."""

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

import anyio
import httpx
import pytest
from openai import AsyncOpenAI
from PIL import Image
from pydantic import ValidationError

from roometry.config import Settings
from roometry.main import create_app
from roometry.vision.schemas import AnalysisErrorResponse, RoomAnalysisResponse

pytestmark = pytest.mark.anyio
SOURCE = json.loads(
    (
        Path(__file__).resolve().parents[1] / "data/fixtures/room_analysis.json"
    ).read_text()
)["observations"]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def observations_for(payload):
    image_ids = json.loads(payload["input"][0]["content"][0]["text"])["image_ids"]
    data = deepcopy(SOURCE)
    mapping = dict(zip(data["image_ids"], [image_ids[0], image_ids[-1]]))
    data["image_ids"] = image_ids
    for entity in [*data["walls"], *data["objects"], *data["openings"]]:
        entity["source_image_ids"] = list(
            dict.fromkeys(
                mapping[identifier] for identifier in entity["source_image_ids"]
            )
        )
    return data


def provider_response(payload):
    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 1792404000,
        "status": "completed",
        "model": payload["model"],
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "output": [
            {
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(observations_for(payload)),
                        "annotations": [],
                    }
                ],
            }
        ],
        "usage": {
            "input_tokens": 1200,
            "output_tokens": 800,
            "total_tokens": 2000,
            "input_tokens_details": {"cached_tokens": 100},
            "output_tokens_details": {"reasoning_tokens": 10},
        },
    }


@pytest.fixture
async def harness(tmp_path):
    calls, actions = [], []

    async def handler(request):
        assert request.url == "https://api.openai.com/v1/responses"
        payload = json.loads(request.content)
        calls.append(payload)
        if actions:
            return await actions.pop(0)(request, payload)
        return httpx.Response(200, json=provider_response(payload))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        async with AsyncOpenAI(
            api_key="test-provider-key",
            base_url="https://api.openai.com/v1",
            http_client=transport,
            max_retries=3,
        ) as sdk:
            settings = Settings(
                _env_file=None,
                app_environment="test",
                upload_storage_dir=tmp_path / "photos",
                analysis_enabled=True,
                # Existing transport tests isolate their stage. Dedicated review
                # tests explicitly enable the full pipeline with mock responses.
                visual_review_enabled=False,
                room_assessment_enabled=False,
                # Legacy tool-choice tests explicitly exercise the optional LLM
                # router. Cost-flow tests enable the default deterministic path.
                agent_deterministic_routing=False,
                openai_api_key="test-provider-key",
            )
            app = create_app(settings, analysis_client=sdk)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://testserver"
                ) as client:
                    yield app, client, calls, actions


async def upload(client, count=2):
    buffer = BytesIO()
    with Image.new("RGB", (256, 256), "green") as image:
        image.save(buffer, "PNG")
    options = {
        "room_type": "bedroom",
        "goals": ["better_aesthetics"],
        "goal_text": "Keep the bed.",
        "dimensions": {"width_m": 4, "measurements_source": "user_confirmed"},
    }
    files = [
        ("images", (f"photo-{index}.png", buffer.getvalue(), "image/png"))
        for index in range(count)
    ]
    files.append(("options", (None, json.dumps(options))))
    response = await client.post("/api/rooms/inputs", files=files)
    assert response.status_code == 201
    return response.json()


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    result = AnalysisErrorResponse.model_validate_json(response.content)
    assert result.detail[0].code == code
    assert response.headers["cache-control"] == "no-store"


async def test_ten_photos_analyzed_together_with_strict_output_and_owned_context(
    harness,
):
    app, client, calls, _ = harness
    uploaded = await upload(client, 10)
    response = await client.post(
        "/api/rooms/analyze", json={"input_id": uploaded["input_id"]}
    )
    assert response.status_code == 200, response.text
    result = RoomAnalysisResponse.model_validate_json(response.content)
    assert result.input_options.model_dump(mode="json") == uploaded["options"]
    assert (
        result.expires_at.isoformat()
        == datetime.fromisoformat(uploaded["expires_at"]).isoformat()
    )
    assert result.observations.image_ids == [
        photo["image_id"] for photo in uploaded["images"]
    ]
    assert len(calls) == 1
    request = calls[0]
    assert (
        request["store"] is False
        and request["tools"] == []
        and request["truncation"] == "disabled"
    )
    assert request["max_output_tokens"] == 16384 and request["model"] == "gpt-6-luna"
    assert request["text"]["format"]["strict"] is True
    assert request["text"]["format"]["schema"]["additionalProperties"] is False
    images = [
        part for part in request["input"][0]["content"] if part["type"] == "input_image"
    ]
    assert len(images) == 10
    assert all(
        part["image_url"].startswith("data:image/jpeg;base64,")
        and part["detail"] == "high"
        for part in images
    )
    assert (
        "untrusted" in request["instructions"]
        and "possible_duplicates" in request["instructions"]
    )
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    record = app.state.analysis_service.usage_records[-1]
    assert (record.input_tokens, record.output_tokens, record.total_tokens) == (
        1200,
        800,
        2000,
    )
    assert (
        record.cached_input_tokens == 100
        and record.reasoning_tokens == 10
        and record.outcome == "success"
    )


async def test_repeated_request_uses_cached_analysis_without_new_provider_spend(
    harness,
):
    app, client, calls, _ = harness
    uploaded = await upload(client)
    first = await client.post(
        "/api/rooms/analyze", json={"input_id": uploaded["input_id"]}
    )
    second = await client.post(
        "/api/rooms/analyze", json={"input_id": uploaded["input_id"]}
    )
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() and len(calls) == 1
    # A consumer mutating its returned copy cannot corrupt the private cache.
    state = next(iter(app.state.analysis_service._states.values()))
    copy = await app.state.analysis_service.analyze(uploaded["input_id"], state.owner)
    copy.observations.objects.clear()
    assert (
        await client.post("/api/rooms/analyze", json={"input_id": uploaded["input_id"]})
    ).json()["observations"]["objects"]
    assert len(calls) == 1


async def test_ownership_and_origin_gate_access_even_when_cached(harness):
    app, client, calls, _ = harness
    uploaded = await upload(client)
    assert (
        await client.post("/api/rooms/analyze", json={"input_id": uploaded["input_id"]})
    ).status_code == 200
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as stranger:
        assert_error(
            await stranger.post(
                "/api/rooms/analyze", json={"input_id": uploaded["input_id"]}
            ),
            404,
            "input_not_found",
        )
    assert_error(
        await client.post(
            "/api/rooms/analyze",
            json={"input_id": uploaded["input_id"]},
            headers={"origin": "null"},
        ),
        403,
        "forbidden_origin",
    )
    assert len(calls) == 1


@pytest.mark.parametrize("action", ["expire", "delete", "replace"])
async def test_unavailable_input_cannot_reopen_cached_analysis(harness, action):
    app, client, calls, _ = harness
    storage = app.state.upload_service.storage
    now = datetime(2026, 10, 19, tzinfo=UTC)
    storage.now = lambda: now
    uploaded = await upload(client)
    assert (
        await client.post("/api/rooms/analyze", json={"input_id": uploaded["input_id"]})
    ).status_code == 200
    if action == "expire":
        storage.now = lambda: now + timedelta(hours=1)
    elif action == "delete":
        await client.delete(f"/api/rooms/inputs/{uploaded['input_id']}")
    else:
        await upload(client)
    assert_error(
        await client.post(
            "/api/rooms/analyze", json={"input_id": uploaded["input_id"]}
        ),
        404,
        "input_not_found",
    )
    await app.state.analysis_service.cleanup()
    assert not app.state.analysis_service._states and len(calls) == 1


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_only_transient_statuses_retry_within_single_per_input_budget(
    harness, status
):
    app, client, calls, actions = harness

    async def failing(_request, _payload):
        return httpx.Response(
            status, json={"error": {"message": "PRIVATE_PROVIDER_DETAILS"}}
        )

    actions.extend([failing, failing])
    uploaded = await upload(client)
    first = await client.post(
        "/api/rooms/analyze", json={"input_id": uploaded["input_id"]}
    )
    assert_error(
        first,
        429 if status == 429 else 503,
        "provider_rate_limited" if status == 429 else "provider_unavailable",
    )
    assert "PRIVATE_PROVIDER_DETAILS" not in first.text and len(calls) == 2
    assert_error(
        await client.post(
            "/api/rooms/analyze", json={"input_id": uploaded["input_id"]}
        ),
        429,
        "analysis_limit_reached",
    )
    assert len(calls) == 2 and len(app.state.analysis_service.usage_records) == 2
    assert all(
        record.input_tokens is None
        for record in app.state.analysis_service.usage_records
    )


async def test_transient_failure_then_success_is_cached_and_both_attempts_tracked(
    harness,
):
    app, client, calls, actions = harness

    async def failing(_request, _payload):
        return httpx.Response(500, json={"error": {"message": "private"}})

    actions.append(failing)
    uploaded = await upload(client)
    assert (
        await client.post("/api/rooms/analyze", json={"input_id": uploaded["input_id"]})
    ).status_code == 200
    assert len(calls) == 2
    assert [record.outcome for record in app.state.analysis_service.usage_records] == [
        "provider_unavailable",
        "success",
    ]


@pytest.mark.parametrize(
    "kind,status,code",
    [
        ("timeout", 504, "analysis_timeout"),
        ("connection", 503, "provider_unavailable"),
        ("auth", 503, "provider_unavailable"),
    ],
)
async def test_ambiguous_network_and_configuration_failures_do_not_auto_retry(
    harness, kind, status, code
):
    app, client, calls, actions = harness

    async def failing(request, _payload):
        if kind == "timeout":
            raise httpx.ReadTimeout("PRIVATE_TOKEN", request=request)
        if kind == "connection":
            raise httpx.ConnectError("PRIVATE_TOKEN", request=request)
        return httpx.Response(401, json={"error": {"message": "PRIVATE_TOKEN"}})

    actions.append(failing)
    uploaded = await upload(client)
    response = await client.post(
        "/api/rooms/analyze", json={"input_id": uploaded["input_id"]}
    )
    assert_error(response, status, code)
    assert "PRIVATE_TOKEN" not in response.text and len(calls) == 1
    assert app.state.analysis_service.usage_records[-1].total_tokens is None


@pytest.mark.parametrize(
    "kind,status,code",
    [
        ("refusal", 422, "analysis_refused"),
        ("incomplete", 502, "analysis_incomplete"),
        ("invalid_json", 502, "invalid_analysis"),
        ("invalid_reference", 502, "invalid_analysis"),
        ("wrong_images", 502, "invalid_analysis"),
        ("multiple_outputs", 502, "invalid_analysis"),
        ("oversized", 502, "invalid_analysis"),
        ("failed", 503, "provider_unavailable"),
        ("missing_content", 502, "invalid_analysis"),
        ("missing_text", 502, "invalid_analysis"),
        ("missing_output", 502, "invalid_analysis"),
        ("duplicate_keys", 502, "invalid_analysis"),
        ("unexpected_tool", 502, "invalid_analysis"),
    ],
)
async def test_bad_model_results_are_safe_and_usage_is_still_recorded(
    harness, kind, status, code, caplog
):
    app, client, calls, actions = harness
    # Verify the public failure after its configured budget is exhausted.
    # Semantic correction with a second attempt is covered separately.
    app.state.settings.analysis_max_provider_attempts = 1

    async def model_result(_request, payload):
        data = provider_response(payload)
        part = data["output"][0]["content"][0]
        if kind == "refusal":
            data["output"][0]["content"] = [
                {"type": "refusal", "refusal": "PRIVATE_REFUSAL"}
            ]
        elif kind in {"incomplete", "failed"}:
            data["status"] = kind
        elif kind == "invalid_json":
            part["text"] = "PRIVATE_INVALID_OUTPUT"
        elif kind in {"invalid_reference", "wrong_images"}:
            observations = observations_for(payload)
            if kind == "invalid_reference":
                observations["objects"][0]["wall_id"] = "missing_wall"
            else:
                observations["image_ids"] = ["44444444-4444-4444-8444-444444444444"]
                observations.update(
                    objects=[], walls=[], openings=[], possible_duplicates=[]
                )
            part["text"] = json.dumps(observations)
        elif kind == "multiple_outputs":
            data["output"][0]["content"].append(deepcopy(part))
        elif kind == "missing_content":
            del data["output"][0]["content"]
        elif kind == "missing_text":
            del part["text"]
        elif kind == "missing_output":
            del data["output"]
        elif kind == "duplicate_keys":
            part["text"] = part["text"].replace(
                '"schema_version": 1', '"schema_version": 1, "schema_version": 1'
            )
        elif kind == "unexpected_tool":
            data["output"] = [
                {
                    "type": "function_call",
                    "name": "shell",
                    "arguments": "{}",
                    "call_id": "call_test",
                }
            ]
        else:
            part["text"] = "x" * 262145
        return httpx.Response(200, json=data)

    actions.append(model_result)
    uploaded = await upload(client)
    with caplog.at_level("INFO", logger="roometry.vision.service"):
        response = await client.post(
            "/api/rooms/analyze", json={"input_id": uploaded["input_id"]}
        )
    assert_error(response, status, code)
    assert len(calls) == 1
    record = app.state.analysis_service.usage_records[-1]
    assert record.total_tokens == 2000 and record.outcome == code
    assert "PRIVATE" not in response.text and "PRIVATE" not in caplog.text
    logged = next(
        record.analysis_usage
        for record in caplog.records
        if hasattr(record, "analysis_usage")
    )
    assert (
        logged["total_tokens"] == 2000
        and "photos" not in logged
        and "owner" not in logged
    )


async def test_total_deadline_cancels_provider_and_does_not_cache_partial_result(
    harness,
):
    app, client, calls, actions = harness
    app.state.settings.analysis_timeout_seconds = 0.05
    cancelled = anyio.Event()

    async def slow(_request, _payload):
        try:
            await anyio.sleep(5)
        finally:
            cancelled.set()

    actions.append(slow)
    uploaded = await upload(client)
    assert_error(
        await client.post(
            "/api/rooms/analyze", json={"input_id": uploaded["input_id"]}
        ),
        504,
        "analysis_timeout",
    )
    assert cancelled.is_set() and len(calls) == 1
    assert next(iter(app.state.analysis_service._states.values())).result is None


@pytest.mark.parametrize("kind", ["missing_usage", "missing_details"])
async def test_missing_usage_is_unknown_rather_than_fabricated_zero(harness, kind):
    app, client, calls, actions = harness

    async def missing(_request, payload):
        data = provider_response(payload)
        if kind == "missing_usage":
            del data["usage"]
        else:
            del data["usage"]["input_tokens_details"]
            del data["usage"]["output_tokens_details"]
        return httpx.Response(200, json=data)

    actions.append(missing)
    uploaded = await upload(client)
    response = await client.post(
        "/api/rooms/analyze", json={"input_id": uploaded["input_id"]}
    )
    assert response.status_code == 200
    record = app.state.analysis_service.usage_records[-1]
    assert record.cached_input_tokens is None and record.reasoning_tokens is None
    assert record.total_tokens == (None if kind == "missing_usage" else 2000)


async def test_external_cancellation_releases_capacity_and_preserves_attempt_budget(
    harness,
):
    app, client, calls, actions = harness
    entered = anyio.Event()

    async def blocked(_request, _payload):
        entered.set()
        await anyio.sleep_forever()

    actions.append(blocked)
    uploaded = await upload(client)

    async def first():
        await client.post("/api/rooms/analyze", json={"input_id": uploaded["input_id"]})

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(first)
        await entered.wait()
        tasks.cancel_scope.cancel()
    state = next(iter(app.state.analysis_service._states.values()))
    assert state.attempts == 1 and state.result is None
    assert app.state.analysis_service.usage_records[-1].outcome == "interrupted"
    assert (
        await client.post("/api/rooms/analyze", json={"input_id": uploaded["input_id"]})
    ).status_code == 200
    assert state.attempts == 2 and len(calls) == 2


async def test_revocation_before_retry_prevents_another_provider_call(harness):
    _, client, calls, actions = harness
    uploaded = await upload(client)

    async def revoked(_request, _payload):
        await client.delete(f"/api/rooms/inputs/{uploaded['input_id']}")
        return httpx.Response(500, json={"error": {"message": "private"}})

    actions.append(revoked)
    assert_error(
        await client.post(
            "/api/rooms/analyze", json={"input_id": uploaded["input_id"]}
        ),
        404,
        "input_not_found",
    )
    assert len(calls) == 1


async def test_concurrent_analysis_is_rejected_without_a_second_provider_call(harness):
    app, client, calls, actions = harness
    entered, release = anyio.Event(), anyio.Event()

    async def blocked(_request, payload):
        entered.set()
        await release.wait()
        return httpx.Response(200, json=provider_response(payload))

    actions.append(blocked)
    uploaded = await upload(client)
    responses = []

    async def first():
        responses.append(
            await client.post(
                "/api/rooms/analyze", json={"input_id": uploaded["input_id"]}
            )
        )

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(first)
        await entered.wait()
        assert_error(
            await client.post(
                "/api/rooms/analyze", json={"input_id": uploaded["input_id"]}
            ),
            429,
            "analysis_busy",
        )
        release.set()
    assert responses[0].status_code == 200 and len(calls) == 1


@pytest.mark.parametrize("action", ["expire", "delete", "replace"])
async def test_source_revoked_during_provider_call_discards_result(harness, action):
    app, client, calls, actions = harness
    storage = app.state.upload_service.storage
    now = datetime(2026, 10, 19, tzinfo=UTC)
    storage.now = lambda: now
    uploaded = await upload(client)

    async def revoked(_request, payload):
        if action == "expire":
            storage.now = lambda: now + timedelta(hours=1)
        elif action == "delete":
            await client.delete(f"/api/rooms/inputs/{uploaded['input_id']}")
        else:
            await upload(client)
        return httpx.Response(200, json=provider_response(payload))

    actions.append(revoked)
    assert_error(
        await client.post(
            "/api/rooms/analyze", json={"input_id": uploaded["input_id"]}
        ),
        404,
        "input_not_found",
    )
    assert (
        len(calls) == 1
        and app.state.analysis_service.usage_records[-1].total_tokens == 2000
    )
    assert next(iter(app.state.analysis_service._states.values())).result is None


async def test_storage_missing_and_payload_limits_do_not_call_provider(
    harness, monkeypatch
):
    app, client, calls, _ = harness
    uploaded = await upload(client)
    with monkeypatch.context() as patch:

        def unavailable(*_args):
            raise OSError("PRIVATE_PATH")

        patch.setattr(app.state.upload_service.storage, "read_image", unavailable)
        response = await client.post(
            "/api/rooms/analyze", json={"input_id": uploaded["input_id"]}
        )
        assert_error(response, 503, "storage_unavailable")
        assert "PRIVATE_PATH" not in response.text
    app.state.settings.analysis_max_request_bytes = 1024
    assert_error(
        await client.post(
            "/api/rooms/analyze", json={"input_id": uploaded["input_id"]}
        ),
        413,
        "request_too_large",
    )
    assert not calls


async def test_disabled_or_missing_key_keeps_uploads_and_health_available(harness):
    app, client, calls, _ = harness
    uploaded = await upload(client)
    for field, value in [("analysis_enabled", False), ("openai_api_key", None)]:
        setattr(app.state.settings, field, value)
        assert_error(
            await client.post(
                "/api/rooms/analyze", json={"input_id": uploaded["input_id"]}
            ),
            503,
            "analysis_unavailable",
        )
        app.state.settings.analysis_enabled = True
    assert (await client.get("/api/health")).status_code == 200 and not calls


async def test_request_contract_and_actual_body_limits_are_enforced_before_model(
    harness,
):
    _, client, calls, _ = harness
    for body in (
        {},
        {"input_id": "../private"},
        {"input_id": "11111111-1111-4111-8111-111111111111", "options": {}},
    ):
        assert_error(
            await client.post("/api/rooms/analyze", json=body), 422, "invalid_request"
        )
    assert_error(
        await client.post(
            "/api/rooms/analyze",
            content="{bad",
            headers={"content-type": "application/json"},
        ),
        422,
        "invalid_request",
    )
    for raw in (
        b'{"input_id":"one","input_id":"two"}',
        b"[" * 1500 + b"]" * 1500,
        b"\xff",
    ):
        assert_error(
            await client.post(
                "/api/rooms/analyze",
                content=raw,
                headers={"content-type": "application/json"},
            ),
            422,
            "invalid_request",
        )
    assert_error(
        await client.post("/api/rooms/analyze", content="plain"),
        415,
        "unsupported_media_type",
    )
    for length in (None, "1", "5000"):
        headers = {"content-type": "application/json"}
        if length is not None:
            headers["content-length"] = length

        async def chunks():
            yield b" " * 3000
            yield b" " * 2000

        assert_error(
            await client.post("/api/rooms/analyze", content=chunks(), headers=headers),
            413,
            "request_too_large",
        )
    assert not calls


async def test_cache_capacity_recovers_after_input_is_discarded(harness):
    app, client, calls, _ = harness
    app.state.settings.analysis_max_cached_inputs = 1
    uploaded = await upload(client)
    assert (
        await client.post("/api/rooms/analyze", json={"input_id": uploaded["input_id"]})
    ).status_code == 200
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as other:
        second = await upload(other)
        assert_error(
            await other.post(
                "/api/rooms/analyze", json={"input_id": second["input_id"]}
            ),
            429,
            "analysis_busy",
        )
        await client.delete(f"/api/rooms/inputs/{uploaded['input_id']}")
        assert (
            await other.post(
                "/api/rooms/analyze", json={"input_id": second["input_id"]}
            )
        ).status_code == 200
    assert len(calls) == 2


async def test_openapi_exposes_analysis_contract_and_safe_errors(harness):
    _, client, _, _ = harness
    schema = (await client.get("/openapi.json")).json()
    operation = schema["paths"]["/api/rooms/analyze"]["post"]
    assert "application/json" in operation["requestBody"]["content"]
    assert all(
        str(status) in operation["responses"]
        for status in (200, 403, 404, 413, 422, 429, 502, 503, 504)
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"analysis_timeout_seconds": 0},
        {"analysis_timeout_seconds": float("inf")},
        {"analysis_max_body_bytes": 0},
        {"analysis_max_request_bytes": 50_000_001},
        {"analysis_max_output_tokens": 32769},
        {"scene_max_output_tokens": 127},
        {"scene_max_output_tokens": 32769},
        {"scene_reasoning_effort": "unbounded"},
        {"analysis_max_output_bytes": 0},
        {"analysis_max_provider_attempts": 4},
        {"analysis_max_cached_inputs": 0},
    ],
)
async def test_analysis_configuration_is_bounded_and_secret_safe(patch):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **patch)
    settings = Settings(_env_file=None, openai_api_key="PRIVATE_SECRET", model_name=" ")
    assert "PRIVATE_SECRET" not in repr(settings)
    assert settings.model_name == "gpt-6-luna"


async def test_all_null_size_records_are_unknown_without_weakening_validation(harness):
    _app, client, calls, actions = harness

    async def unknown_dimensions(_request, payload):
        data = provider_response(payload)
        observations = observations_for(payload)
        observations["estimated_dimensions"] = {
            "width_m": None,
            "length_m": None,
            "height_m": None,
        }
        observations["objects"][0]["estimated_dimensions_m"] = {
            "width": None,
            "depth": None,
            "height": None,
        }
        data["output"][0]["content"][0]["text"] = json.dumps(observations)
        return httpx.Response(200, json=data)

    actions.append(unknown_dimensions)
    uploaded = await upload(client)
    response = await client.post(
        "/api/rooms/analyze", json={"input_id": uploaded["input_id"]}
    )
    assert response.status_code == 200, response.text
    assert response.json()["observations"]["estimated_dimensions"] is None
    assert (
        response.json()["observations"]["objects"][0]["estimated_dimensions_m"] is None
    )
    assert len(calls) == 1


@pytest.mark.parametrize(
    "invalid",
    [
        {"width": None, "depth": None, "height": None, "PRIVATE_EXTRA": None},
        {"width": None, "depth": None},
        {"width": 0, "depth": None, "height": None},
    ],
)
async def test_unknown_size_normalization_does_not_hide_invalid_fields(
    harness, invalid
):
    _, client, _, actions = harness

    async def bad_dimensions(_request, payload):
        data = provider_response(payload)
        observations = observations_for(payload)
        observations["objects"][0]["estimated_dimensions_m"] = invalid
        data["output"][0]["content"][0]["text"] = json.dumps(observations)
        return httpx.Response(200, json=data)

    actions.append(bad_dimensions)
    uploaded = await upload(client)
    assert_error(
        await client.post(
            "/api/rooms/analyze", json={"input_id": uploaded["input_id"]}
        ),
        502,
        "invalid_analysis",
    )


async def test_failure_logs_include_safe_schema_reason_and_provider_ids(
    harness, caplog
):
    _, client, _, actions = harness

    async def bad_reference(_request, payload):
        data = provider_response(payload)
        observations = observations_for(payload)
        observations["objects"][0]["wall_id"] = "private_missing_wall"
        observations["PRIVATE_EXTRA_KEY"] = "PRIVATE_MODEL_OUTPUT"
        data["output"][0]["content"][0]["text"] = json.dumps(observations)
        return httpx.Response(
            200, json=data, headers={"x-request-id": "req_diagnostic_test"}
        )

    actions.append(bad_reference)
    uploaded = await upload(client)
    with caplog.at_level("WARNING", logger="roometry.vision.service"):
        response = await client.post(
            "/api/rooms/analyze", json={"input_id": uploaded["input_id"]}
        )
    assert_error(response, 502, "invalid_analysis")
    assert "schema_validation" in caplog.text and "extra_forbidden" in caplog.text
    assert "req_diagnostic_test" in caplog.text and "resp_test" in caplog.text
    assert "PRIVATE" not in caplog.text and "private_missing_wall" not in caplog.text
    assert "diagnostics" not in response.text


@pytest.mark.parametrize("count", [1, 2, 10])
async def test_evidence_schema_is_bound_to_only_the_current_upload(harness, count):
    _, client, calls, _ = harness
    uploaded = await upload(client, count)
    response = await client.post('/api/rooms/analyze', json={'input_id': uploaded['input_id']})
    assert response.status_code == 200
    schema = calls[0]['text']['format']['schema']
    expected = [image['image_id'] for image in uploaded['images']]
    root = schema['properties']['image_ids']
    assert root['items']['enum'] == expected
    assert root['minItems'] == root['maxItems'] == count
    for model in schema['$defs'].values():
        prop = model.get('properties', {}).get('source_image_ids')
        if prop:
            assert prop['items']['enum'] == expected and prop['maxItems'] == count


@pytest.mark.parametrize('defect', ['duplicates', 'foreign', 'missing_photo'])
async def test_evidence_normalization_is_lossless_and_never_reassigns_unknown_photos(harness, defect):
    _, client, calls, actions = harness
    uploaded = await upload(client, 2)

    async def output(request, payload):
        data = provider_response(payload)
        obs = observations_for(payload)
        if defect == 'duplicates':
            obs['image_ids'].reverse()
            obs['objects'][0]['source_image_ids'] *= 2
        elif defect == 'foreign':
            obs['objects'][0]['source_image_ids'] = ['44444444-4444-4444-8444-444444444444']
        else:
            obs['image_ids'].pop()
        data['output'][0]['content'][0]['text'] = json.dumps(obs)
        return httpx.Response(200, json=data)

    actions.append(output)
    response = await client.post('/api/rooms/analyze', json={'input_id': uploaded['input_id']})
    assert len(calls) == 1  # Invalid evidence does not trigger blind paid retries.
    if defect == 'duplicates':
        assert response.status_code == 200
        observations = response.json()['observations']
        assert observations['image_ids'] == [image['image_id'] for image in uploaded['images']]
        assert len(observations['objects'][0]['source_image_ids']) == 2
    else:
        assert_error(response, 502, 'invalid_analysis')
