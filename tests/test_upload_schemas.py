"""Room input boundaries without image decoding, storage, or upload endpoints."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from roometry.config import Settings
from roometry.scenes.schemas import RoomType
from roometry.uploads.schemas import (
    InputDimensions, PreparedRoomInput, RoomInputOptions, UploadErrorResponse,
)
from typing import get_args

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolate_settings_environment(monkeypatch):
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def response_data():
    return {
        "input_id": "11111111-1111-4111-8111-111111111111", "status": "ready", "options": {},
        "expires_at": datetime(2026, 10, 19, tzinfo=timezone.utc),
        "images": [{
            "image_id": "22222222-2222-4222-8222-222222222222",
            "original_media_type": "image/png", "original_bytes": 12000,
            "original_width_px": 3000, "original_height_px": 2000,
            "prepared_media_type": "image/jpeg", "prepared_bytes": 8000,
            "prepared_width_px": 2048, "prepared_height_px": 1365,
        }],
    }


def test_minimal_input_is_general_analysis_without_invented_dimensions():
    options = RoomInputOptions.model_validate({})
    assert options.goals == ["general_analysis"]
    assert options.dimensions is None
    assert options.room_type is None
    assert options.budget is None
    assert RoomInputOptions(goals=[]).goals == ["general_analysis"]


def test_fixture_retains_partial_estimates_zero_budget_and_user_constraints():
    options = RoomInputOptions.model_validate_json((ROOT / "data/fixtures/room_input_options.json").read_text())
    assert options.dimensions is not None
    assert options.dimensions.height_m is None
    assert options.dimensions.measurements_source == "estimated"
    assert options.budget is not None and options.budget.amount == 0
    assert options.budget.currency == "USD"
    assert options.constraints == "Keep the bed and leave the window clear."


@pytest.mark.parametrize("room_type", get_args(RoomType))
def test_all_scene_room_categories_are_supported(room_type):
    assert RoomInputOptions(room_type=room_type).room_type == room_type


def test_optional_text_is_trimmed_and_blank_input_is_absent():
    options = RoomInputOptions(description="  A bedroom  ", goal_text=" \n ", style=" ", constraints=" keep bed ")
    assert options.description == "A bedroom"
    assert options.goal_text is None and options.style is None
    assert options.constraints == "keep bed"


@pytest.mark.parametrize("field,limit", [("description", 2000), ("goal_text", 2000), ("constraints", 1000), ("style", 100)])
def test_text_limits_include_the_boundary(field, limit):
    assert getattr(RoomInputOptions.model_validate({field: "x" * limit}), field) == "x" * limit
    with pytest.raises(ValidationError):
        RoomInputOptions.model_validate({field: "x" * (limit + 1)})


@pytest.mark.parametrize("data", [
    {"unknown": True}, {"room_type": "garage"}, {"room_type": 1},
    {"goals": None}, {"goals": ["shopping"]}, {"goals": ["workspace", "workspace"]},
    {"description": 1}, {"style": False}, {"constraints": []},
    {"budget": {"amount": "10", "currency": "USD"}},
    {"budget": {"amount": True, "currency": "USD"}},
    {"budget": {"amount": -1, "currency": "USD"}},
    {"budget": {"amount": 1_000_001, "currency": "USD"}},
    {"budget": {"amount": float("nan"), "currency": "USD"}},
    {"budget": {"amount": float("inf"), "currency": "USD"}},
    {"budget": {"amount": 10}}, {"budget": {"amount": 10, "currency": "usd"}},
    {"budget": {"amount": 10, "currency": "USD", "exchange_rate": 1}},
    {"dimensions": {"width_m": "4"}}, {"dimensions": {"length_m": False}},
    {"dimensions": {"width_m": 0}}, {"dimensions": {"height_m": -1}},
    {"dimensions": {"width_m": 30.1}}, {"dimensions": {"height_m": 10.1}},
    {"dimensions": {"length_m": float("inf")}},
    {"dimensions": {"height_m": float("nan")}},
    {"dimensions": {"depth_m": 4}},
    {"dimensions": {"measurements_source": "user_confirmed"}},
])
def test_rejects_invalid_or_ambiguous_user_options(data):
    with pytest.raises(ValidationError):
        RoomInputOptions.model_validate(data)


def test_dimensions_allow_square_rooms_partial_confirmation_and_scene_boundaries():
    square = InputDimensions(width_m=4.0, length_m=4.0)
    assert square.width_m == square.length_m
    assert square.height_m is None
    assert square.measurements_source == "estimated"
    confirmed = InputDimensions(width_m=30.0, height_m=10.0, measurements_source="user_confirmed")
    assert confirmed.length_m is None
    assert confirmed.measurements_source == "user_confirmed"


def test_json_transport_keeps_strict_numbers_and_unknown_values():
    options = RoomInputOptions.model_validate_json('{"dimensions":{"width_m":4,"height_m":null}}')
    assert options.dimensions is not None and options.dimensions.width_m == 4
    for raw in ('{"dimensions":{"width_m":"4"}}', '{"budget":{"amount":NaN,"currency":"USD"}}'):
        with pytest.raises(ValidationError):
            RoomInputOptions.model_validate_json(raw)


def test_mutated_nested_models_are_revalidated():
    dimensions = InputDimensions(width_m=4.0)
    dimensions.width_m = -1.0
    with pytest.raises(ValidationError):
        RoomInputOptions(dimensions=dimensions)


def test_response_serializes_aware_expiry_and_contains_only_public_metadata():
    response = PreparedRoomInput.model_validate(response_data())
    serialized = response.model_dump(mode="json")
    assert serialized["expires_at"] == "2026-10-19T00:00:00Z"
    assert PreparedRoomInput.model_validate_json(json.dumps(serialized)) == response
    assert set(serialized) == {"input_id", "status", "options", "images", "expires_at"}
    assert set(serialized["images"][0]) == {
        "image_id", "original_media_type", "original_bytes", "original_width_px", "original_height_px",
        "prepared_media_type", "prepared_bytes", "prepared_width_px", "prepared_height_px",
    }


@pytest.mark.parametrize("mutation", [
    lambda data: data.update(storage_key="private/file.jpg"),
    lambda data: data.update(owner_scope="private owner"),
    lambda data: data.update(status="queued"),
    lambda data: data.update(input_id="../private"),
    lambda data: data.update(expires_at=datetime(2026, 10, 19)),
    lambda data: data.update(images=[]),
    lambda data: data.update(images=data["images"] * 11),
    lambda data: data.update(images=data["images"] * 2),
    lambda data: data["images"][0].update(original_bytes=True),
    lambda data: data["images"][0].update(prepared_width_px=0),
    lambda data: data["images"][0].update(original_media_type="image/svg+xml"),
    lambda data: data["images"][0].update(prepared_media_type="image/png"),
    lambda data: data["images"][0].update(filename="private-bedroom.jpg"),
])
def test_rejects_invalid_or_private_response_fields(mutation):
    data = response_data()
    mutation(data)
    with pytest.raises(ValidationError):
        PreparedRoomInput.model_validate(data)


@pytest.mark.parametrize("count", [1, 4, 10, 11])
def test_image_count_accepts_ten_and_rejects_eleven_distinct_images(count):
    data = response_data()
    image = data["images"][0]
    data["images"] = [
        {**image, "image_id": f"22222222-2222-4222-8222-{index:012x}"}
        for index in range(count)
    ]
    if count <= 10:
        assert len(PreparedRoomInput.model_validate(data).images) == count
    else:
        with pytest.raises(ValidationError) as error:
            PreparedRoomInput.model_validate(data)
        assert error.value.errors()[0]["type"] == "too_long"


@pytest.mark.parametrize("media_type,extension", [
    ("image/jpeg", ".jpg"), ("image/png", ".png"), ("image/webp", ".webp"),
    ("image/heic", ".heic"), ("image/heif", ".heif"), ("image/avif", ".avif"),
])
def test_photo_formats_are_exposed_and_preserved_in_public_metadata(media_type, extension):
    limits = Settings(_env_file=None).public_upload_limits()
    assert media_type in limits.allowed_media_types
    assert extension in limits.allowed_extensions
    data = response_data()
    data["images"][0]["original_media_type"] = media_type
    result = PreparedRoomInput.model_validate(data)
    assert result.images[0].original_media_type == media_type
    assert result.images[0].prepared_media_type == "image/jpeg"
    assert PreparedRoomInput.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("media_type", [
    "image/heic-sequence", "image/heif-sequence", "image/avif-sequence",
    "image/gif", "image/svg+xml", "video/quicktime",
])
def test_sequences_vector_images_and_video_are_not_source_photo_types(media_type):
    limits = Settings(_env_file=None).public_upload_limits()
    assert media_type not in limits.allowed_media_types
    data = response_data()
    data["images"][0]["original_media_type"] = media_type
    with pytest.raises(ValidationError):
        PreparedRoomInput.model_validate(data)


def test_error_contract_locates_a_file_or_options_field_without_raw_input():
    error = UploadErrorResponse.model_validate({"detail": [
        {"code": "invalid_image", "message": "This photo could not be decoded.", "file_index": 1},
        {"code": "invalid_options", "message": "Enter a positive width.", "field": "dimensions.width_m"},
    ]})
    assert error.detail[0].file_index == 1
    assert error.detail[1].field == "dimensions.width_m"
    for data in ({"detail": []}, {"detail": [{"code": "invented", "message": "Error"}]},
                 {"detail": [{"code": "invalid_image", "message": "Error", "raw_input": "private"}]}):
        with pytest.raises(ValidationError):
            UploadErrorResponse.model_validate(data)


def test_settings_expose_policy_without_private_configuration():
    settings = Settings(_env_file=None)
    limits = settings.public_upload_limits()
    assert limits.max_images == 10
    assert limits.max_file_bytes == 8 * 1024 * 1024
    assert limits.max_total_file_bytes == 80 * 1024 * 1024
    assert limits.max_body_bytes == 82 * 1024 * 1024
    assert limits.ttl_seconds == 3600
    assert limits.max_room_side_m == 30.0 and limits.max_room_height_m == 10.0
    assert "cors_origins" not in limits.model_dump()


def test_environment_overrides_reach_the_public_policy(monkeypatch):
    monkeypatch.setenv("MAX_IMAGES_PER_REQUEST", "2")
    monkeypatch.setenv("UPLOAD_MAX_FILE_BYTES", "4194304")
    monkeypatch.setenv("UPLOAD_TTL_SECONDS", "1800")
    limits = Settings(_env_file=None).public_upload_limits()
    assert limits.max_images == 2
    assert limits.max_file_bytes == 4194304
    assert limits.ttl_seconds == 1800


@pytest.mark.parametrize("patch", [
    {"max_images_per_request": 0}, {"max_images_per_request": 11},
    {"upload_max_file_bytes": 0}, {"upload_ttl_seconds": -1},
    {"upload_max_total_file_bytes": 1}, {"upload_max_body_bytes": 80 * 1024 * 1024},
    {"upload_min_edge_px": 10001}, {"upload_max_pixels": 255 * 255},
    {"upload_prepared_max_edge_px": 10001},
])
def test_rejects_inconsistent_upload_settings(patch):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **patch)
