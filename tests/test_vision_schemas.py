"""Observation uncertainty, evidence integrity, and public analysis boundaries."""

import json
from copy import deepcopy
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from roometry.scenes.assets import ObjectType
from roometry.vision.schemas import (
    AnalyzeRoomRequest,
    EstimatedRoomDimensions,
    RoomAnalysisResponse,
    RoomObservations,
)

FIXTURE = Path(__file__).resolve().parents[1] / "data/fixtures/room_analysis.json"
FOREIGN_IMAGE = "44444444-4444-4444-8444-444444444444"


@pytest.fixture
def data():
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def observations(data):
    return data["observations"]


def test_synthetic_fixture_keeps_estimates_separate_from_confirmed_partial_input(data):
    response = RoomAnalysisResponse.model_validate_json(json.dumps(data))
    assert response.status == "analyzed"
    supplied = response.input_options.dimensions
    assert supplied is not None
    assert supplied.width_m == supplied.length_m == 4
    assert supplied.height_m is None and supplied.measurements_source == "user_confirmed"
    assert response.observations.estimated_dimensions is None
    bed_estimate = response.observations.objects[0].estimated_dimensions_m
    assert bed_estimate is not None and bed_estimate.height is None
    assert len(response.observations.objects[0].source_image_ids) == 2
    assert response.observations.objects[2].support_object_id == "desk_observed"
    chairs = [obj for obj in response.observations.objects if obj.type == "chair"]
    assert len(chairs) == 2 and chairs[0].id != chairs[1].id
    assert response.observations.possible_duplicates[0].entity_ids == [obj.id for obj in chairs]
    assert "Synthetic" in response.observations.assumptions[0]
    assert RoomAnalysisResponse.model_validate_json(response.model_dump_json()) == response


def test_unknown_room_and_empty_observations_do_not_manufacture_a_scene(observations):
    observations.update(room_type=None, room_type_confidence=None, room_shape="unknown",
                        floor_visible=None, ceiling_visible=None, walls=[], objects=[], openings=[],
                        possible_duplicates=[], unknowns=["No room features can be identified."])
    result = RoomObservations.model_validate(observations)
    assert result.room_type is None and not result.objects
    assert result.estimated_dimensions is None
    assert "scene" not in result.model_dump()


def test_unknown_object_and_occluded_support_can_be_described_without_a_fake_asset(observations):
    obj = observations["objects"][2]
    obj.update(type=None, label="Unrecognized item on a surface", support_object_id=None,
               relative_location=None, appears_fixed=None)
    assert RoomObservations.model_validate(observations).objects[2].type is None
    obj["placement"] = None
    assert RoomObservations.model_validate(observations).objects[2].placement is None


def test_all_approved_object_categories_are_observable(observations):
    for object_type in get_args(ObjectType):
        observations["objects"][0]["type"] = object_type
        assert RoomObservations.model_validate(observations).objects[0].type == object_type


@pytest.mark.parametrize("mutation", [
    lambda x: x.update(schema_version=True),
    lambda x: x.update(schema_version=1.0),
    lambda x: x.update(schema_version="1"),
    lambda x: x.update(schema_version=2),
    lambda x: x.update(room_type="garage"),
    lambda x: x.update(room_type=None),
    lambda x: x.update(room_type_confidence=None),
    lambda x: x.update(room_type_confidence=0.9),
    lambda x: x.update(room_shape="square"),  # Square is rectangular, not a separate geometry.
    lambda x: x.update(floor_visible="yes"),
    lambda x: x.update(image_ids=[]),
    lambda x: x["image_ids"].append(x["image_ids"][0]),
    lambda x: x.update(image_ids=[FOREIGN_IMAGE] * 11),
    lambda x: x["objects"][0].update(source_image_ids=[]),
    lambda x: x["objects"][0].update(source_image_ids=[FOREIGN_IMAGE]),
    lambda x: x["walls"][0]["source_image_ids"].append(x["image_ids"][0]),
    lambda x: x["openings"][0].update(source_image_ids=[FOREIGN_IMAGE]),
    lambda x: x["objects"][0].update(id=x["walls"][0]["id"]),
    lambda x: x["objects"][0].update(id="../bed"),
    lambda x: x["objects"][0].update(type="spaceship"),
    lambda x: x["objects"][0].update(label="   "),
    lambda x: x["objects"][0].update(label="x" * 201),
    lambda x: x["objects"][0].update(wall_id="missing_wall"),
    lambda x: x["openings"][0].update(wall_id="missing_wall"),
    lambda x: x["objects"][2].update(support_object_id="missing_object"),
    lambda x: x["objects"][2].update(support_object_id="computer_observed"),
    lambda x: x["objects"][2].update(placement="floor"),
    lambda x: x["objects"][1].update(placement="surface", support_object_id="computer_observed"),
    lambda x: x["possible_duplicates"][0].update(entity_ids=["chair_a", "chair_a"]),
    lambda x: x["possible_duplicates"][0].update(entity_ids=["chair_a", "missing_object"]),
    lambda x: x["possible_duplicates"][0].update(entity_ids=["chair_a", "window_observed"]),
    lambda x: x["possible_duplicates"].append({"entity_ids": ["chair_b", "chair_a"], "reason": "Repeated pair"}),
    lambda x: x["objects"][0].update(estimated_dimensions_m={"width": None, "depth": None, "height": None}),
    lambda x: x.update(estimated_dimensions={"width_m": None, "length_m": None, "height_m": None}),
    lambda x: x.update(estimated_dimensions={"width_m": 4, "length_m": None}),
    lambda x: x["objects"][0].pop("color"),
    lambda x: x.pop("ceiling_visible"),
    lambda x: x.update(unknowns=[" "]),
    lambda x: x.update(warnings=["x"] * 51),
    lambda x: x.update(assumptions=["x" * 1001]),
    lambda x: x.update(walls=x["walls"] * 3),
    lambda x: x.update(objects=x["objects"] * 41),
    lambda x: x.update(openings=x["openings"] * 17),
    lambda x: x.update(possible_duplicates=x["possible_duplicates"] * 101),
    lambda x: x["openings"][1].update(estimated_sill_height_m=0.2),
    lambda x: x["openings"][0].update(estimated_height_m=11),
    lambda x: x["objects"][0].update(color="green"),
])
def test_rejects_inconsistent_ambiguous_or_unbounded_observations(observations, mutation):
    mutation(observations)
    with pytest.raises(ValidationError):
        RoomObservations.model_validate(observations)


@pytest.mark.parametrize("value", [0, -1, True, "4", float("nan"), float("inf"), 30.1])
def test_estimates_are_bounded_finite_numbers_not_claims_of_user_confirmation(value):
    with pytest.raises(ValidationError):
        EstimatedRoomDimensions(width_m=value, length_m=None, height_m=None)


def test_partial_estimates_and_non_rectangular_observations_are_honest_valid_results(observations):
    observations.update(room_shape="non_rectangular",
                        estimated_dimensions={"width_m": 4, "length_m": None, "height_m": None})
    result = RoomObservations.model_validate(observations)
    assert result.room_shape == "non_rectangular"
    assert result.estimated_dimensions is not None
    assert result.estimated_dimensions.length_m is None


@pytest.mark.parametrize("field,value", [
    ("asset_id", "bed_standard_01"), ("position", {"x": 1, "y": 0, "z": 1}),
    ("locked", True), ("fixed", True), ("measurements_source", "user_confirmed"),
    ("image_url", "https://example.com/private.jpg"), ("storage_key", "private/photo.jpg"),
])
def test_observations_cannot_assign_scene_authority_or_private_image_handles(observations, field, value):
    observations["objects"][0][field] = value
    with pytest.raises(ValidationError):
        RoomObservations.model_validate(observations)


def test_nested_instances_are_revalidated(observations):
    estimate = EstimatedRoomDimensions(width_m=4, length_m=None, height_m=None)
    estimate.width_m = -1
    observations["estimated_dimensions"] = estimate
    with pytest.raises(ValidationError):
        RoomObservations.model_validate(observations)


def test_request_accepts_only_an_opaque_input_reference(data):
    assert AnalyzeRoomRequest(input_id=data["input_id"]).input_id == data["input_id"]
    for payload in ({}, {"input_id": "../private"}, {"input_id": data["input_id"], "options": {}},
                    {"input_id": data["input_id"], "image_urls": ["https://example.com/photo"]}):
        with pytest.raises(ValidationError):
            AnalyzeRoomRequest.model_validate(payload)


@pytest.mark.parametrize("patch", [
    {"analysis_id": "guessable"}, {"status": "ready"}, {"scene": {}}, {"owner_scope": "private"},
    {"expires_at": "2026-10-19T09:59:59Z"}, {"expires_at": "2026-10-19T10:00:00Z"},
    {"created_at": "2026-10-19T10:00:00"}, {"expires_at": "2026-10-19T11:00:00"},
])
def test_response_rejects_private_fields_invalid_states_and_invalid_times(data, patch):
    data.update(patch)
    with pytest.raises(ValidationError):
        RoomAnalysisResponse.model_validate_json(json.dumps(data))


def test_model_output_schema_requires_explicit_nulls_and_forbids_extra_fields():
    from roometry.vision.evidence import strict_schema
    schema = strict_schema(RoomObservations)
    for model in [schema, *schema["$defs"].values()]:
        if model.get("type") == "object":
            assert model["additionalProperties"] is False
            assert set(model["required"]) == set(model["properties"])
