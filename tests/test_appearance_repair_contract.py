"""Constrained color repairs cannot revive omitted objects or mutate geometry."""

import json
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator, ValidationError

from roometry.config import Settings
from roometry.vision.review_request import build_appearance_repair_request
from roometry.vision.review_schemas import ReviewJudgment
from roometry.vision.schemas import ReconstructionConstraints
from tests.test_review_context_budget import request_pair
from tests.test_review_quality import finding


def prepared():
    analysis, proposal, _ = request_pair()
    data = {
        "verdict": "needs_correction",
        "summary": "Dark upholstery differs.",
        "limitations": [],
        "findings": [
            finding(
                "color",
                "bed_observed",
                appearance={"field": "color", "value": "#353535"},
            )
        ],
    }
    judgment = ReviewJudgment.model_validate(data)
    payload = build_appearance_repair_request(
        Settings(_env_file=None),
        analysis,
        proposal,
        ReconstructionConstraints(),
        judgment,
        [(0, judgment.findings[0])],
        [b"photo"] * len(analysis.observations.image_ids),
    )
    context = json.loads(payload["input"][0]["content"][0]["text"])
    record = {
        **context["appearance_revision_baselines"][0],
        "reason": "The photo shows dark upholstery.",
    }
    patch = {
        "wall_assignments": [],
        "openings": [],
        "objects": [],
        "estimate_corrections": [],
        "interpretation_corrections": [],
        "appearance_corrections": [record],
        "assumptions": [],
        "warnings": [],
    }
    return payload, context, patch


def test_exact_color_patch_schema_is_strict_and_does_not_contain_rebuild_context():
    payload, context, patch = prepared()
    schema = payload["text"]["format"]["schema"]
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(patch)
    assert "$ref" not in json.dumps(schema)
    assert "asset_models" not in context and "required_object_ids" not in context
    assert "previous_draft" not in context and "unresolved_entities" not in context
    assert [o["id"] for o in context["observations"]["objects"]] == ["bed_observed"]
    assert (
        payload["model"] == "gpt-6-luna"
        and not payload["store"]
        and not payload["tools"]
    )
    assert len(
        [p for p in payload["input"][0]["content"] if p["type"] == "input_image"]
    ) == len(context["observations"]["image_ids"])


@pytest.mark.parametrize(
    "field",
    [
        "objects",
        "openings",
        "wall_assignments",
        "estimate_corrections",
        "interpretation_corrections",
    ],
)
def test_color_patch_cannot_restore_door_or_make_any_geometric_change(field):
    payload, _, patch = prepared()
    patch[field] = [{"id": "unresolved_door_leaf"}]
    with pytest.raises(ValidationError):
        Draft202012Validator(payload["text"]["format"]["schema"]).validate(patch)


@pytest.mark.parametrize(
    "field,value",
    [
        ("entity_id", "desk_observed"),
        ("field", "metal"),
        ("previous_value", "#FAFAFA"),
        ("revised_value", "blue"),
        ("source_image_ids", ["photo_999"]),
    ],
)
def test_color_patch_cannot_escape_selected_baseline_or_photo_evidence(field, value):
    payload, _, patch = prepared()
    patch = deepcopy(patch)
    patch["appearance_corrections"][0][field] = value
    with pytest.raises(ValidationError):
        Draft202012Validator(payload["text"]["format"]["schema"]).validate(patch)
