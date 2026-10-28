"""Grouped fixtures keep their features; omitted inventory cannot be a no-op repair."""

import json
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator, ValidationError

from roometry.config import Settings
from roometry.scenes.catalog import ASSET_CATALOG, object_surfaces
from roometry.vision.analyzer import _normalize_observations
from roometry.vision.assembly_context import assembly_features
from roometry.vision.catalog import bind_catalog
from roometry.vision.features import required_features
from roometry.vision.partial import assemble_partial
from roometry.vision.proposal_request import build_proposal_request
from roometry.vision.proposal_schemas import SceneDraft
from roometry.vision.proposals import issue
from tests.test_assembly_quality import case
from tests.test_scene_proposals import measurements


def kitchen(asset=None):
    return case(
        [
            (
                "counter",
                "kitchen_counter",
                "L-shaped fitted counter with sink and tap",
                asset,
                "floor",
                (1.5, 0.0, 1.2),
                None,
                None,
            ),
            (
                "chair",
                "chair",
                "Separate chair",
                "chair_standard_01",
                "floor",
                (3.5, 0.0, 3.5),
                None,
                None,
            ),
        ]
    )


def test_explicit_plain_counter_cannot_silently_drop_an_observed_sink():
    analysis, draft = kitchen("kitchen_counter_l_right_01")
    bound, errors = bind_catalog(analysis, draft)
    assert bound is None and errors[0].entity_ids == ["counter"]
    assert "basin" in errors[0].message and "faucet" in errors[0].message
    scene, _, _, unresolved = assemble_partial(analysis, measurements(analysis), draft)
    assert scene and [o.id for o in scene.objects] == ["chair"]
    assert [e.entity_id for e in unresolved] == ["counter"]


def test_null_binding_chooses_a_real_fitted_assembly_without_losing_the_return():
    analysis, draft = kitchen()
    bound, errors = bind_catalog(analysis, draft)
    assert bound and not errors
    assert bound.objects[0].asset_id in {
        "kitchen_counter_l_sink_left_01",
        "kitchen_counter_l_sink_right_01",
    }
    for key in ("kitchen_counter_l_sink_left_01", "kitchen_counter_l_sink_right_01"):
        assert {"basin", "faucet", "worktop"} <= set(
            assembly_features(ASSET_CATALOG[key])
        )
        # No support surface spans the basin's open center or the L's empty bay.
        areas = object_surfaces(key, "floor")
        x = -0.18 if key.endswith("right_01") else 0.18
        assert not any(
            a.x_min < x < a.x_max and a.z_min < -0.315 < a.z_max for a in areas
        )
        assert not any(a.x_min < 0 < a.x_max and a.z_min < 0.3 < a.z_max for a in areas)


def test_separately_observed_fixture_is_not_required_twice():
    analysis, _ = kitchen()
    parent = analysis.observations.objects[0]
    child = parent.model_copy(
        update={"id": "sink", "type": "sink", "support_object_id": parent.id}
    )
    faucet = child.model_copy(update={"id": "tap", "type": "faucet"})
    assert not required_features(parent, [parent, child, faucet])
    for label in ("Counter beside separate sink", "Counter without sink"):
        assert not required_features(parent.model_copy(update={"label": label}))


def test_explicit_component_evidence_in_notes_is_retained_too():
    analysis, _ = kitchen()
    parent = analysis.observations.objects[0].model_copy(
        update={
            "label": "Fitted perimeter counter",
            "notes": ["Includes an inset sink and faucet."],
        }
    )
    assert required_features(parent) == {"basin", "faucet"}


def test_wall_kitchen_cabinet_is_canonicalized_without_changing_its_identity_or_evidence():
    analysis, _ = kitchen()
    raw = analysis.observations.model_dump(mode="json")
    raw["objects"][0].update(
        type="kitchen_cabinet", placement="wall", appears_fixed=True
    )
    original = deepcopy(raw)
    normalized = _normalize_observations(raw, raw["image_ids"])
    item = normalized["objects"][0]
    assert item["type"] == "wall_cabinet" and item["id"] == raw["objects"][0]["id"]
    assert item["source_image_ids"] == raw["objects"][0]["source_image_ids"]
    assert item["placement"] == "wall" and item["notes"]
    assert raw == original


def test_repair_requires_restoring_representable_missing_inventory_and_preserves_scope():
    analysis, draft = kitchen("kitchen_counter_l_sink_left_01")
    missing = draft.model_copy(update={"objects": [draft.objects[1]]})
    request = build_proposal_request(
        Settings(_env_file=None),
        analysis,
        measurements(analysis),
        missing,
        [issue("identity_mismatch", "This observed counter is absent.", "counter")],
        operation="scene_repair",
    )
    context = json.loads(request["input"][0]["content"][0]["text"])
    assert context["repair"]["missing_entity_ids"] == ["counter"]
    assert context["required_object_ids"] == ["counter"]
    assert set(context["repair"]["editable_entity_ids"]) == {"counter"}
    binding = next(b for b in context["asset_bindings"] if b["id"] == "counter")
    assert set(binding["required_components"]) == {"basin", "faucet"}
    assert all(
        {"basin", "faucet"}
        <= set(context["asset_models"][m["asset_id"]]["included_components"])
        for m in binding["models"]
    )
    schema = request["text"]["format"]["schema"]
    assert schema["properties"]["objects"]["minItems"] == 1
    empty = SceneDraft.model_validate(
        {**draft.model_dump(), "objects": [], "wall_assignments": []}
    ).model_dump(mode="json")
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(empty)
