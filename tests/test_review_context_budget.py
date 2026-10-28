"""Compact review requests preserve evidence, coverage and correction authority."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import validate

from roometry.config import Settings
from roometry.scenes.schemas import AppearanceCorrection, MaterialColors
from roometry.vision.appearance import resolve_appearance
from roometry.vision.context_budget import photo_references
from roometry.vision.proposal_schemas import SceneProposalResponse
from roometry.vision.review_context import repair_review_context
from roometry.vision.review_render import VIEW_IDS, ReviewView
from roometry.vision.review_request import build_review_request
from roometry.vision.review_schemas import ReviewJudgment
from roometry.vision.review_validation import parse_judgment
from roometry.vision.schemas import ReconstructionConstraints
from tests.test_review_quality import finding
from tests.test_scene_proposals import source
from tests.test_visual_review import consistent, with_checks


def request_pair():
    analysis = source()
    proposal = SceneProposalResponse.model_validate_json(
        (
            Path(__file__).parents[1] / "data/fixtures/room_scene_proposal.json"
        ).read_text()
    )
    views = [ReviewView(key, b"mock-render") for key in VIEW_IDS]
    images = [
        f"photo-bytes-{i}".encode() for i in range(len(analysis.observations.image_ids))
    ]
    requests = [
        build_review_request(
            Settings(_env_file=None, review_compact_context=compact),
            analysis,
            proposal,
            ReconstructionConstraints(
                facing_targets={"desk_observed": "window_observed", "chair_a": None}
            ),
            images,
            views,
        )
        for compact in (False, True)
    ]
    return analysis, proposal, requests


def context(payload):
    return json.loads(payload["input"][0]["content"][0]["text"])


def test_compaction_preserves_all_photos_renders_targets_and_geometry_without_mutating_sources():
    analysis, proposal, (full, compact) = request_pair()
    original = analysis.model_dump_json(), proposal.model_dump_json()
    full_context, compact_context = context(full), context(compact)
    restored = photo_references(
        compact_context, analysis.observations.image_ids, decode=True
    )
    targets = {t["entity_id"]: t for t in restored["review_targets"]}
    for item in restored["observations"]["objects"]:
        if item["id"] in targets:
            item.update({key: targets[item["id"]][key] for key in ("label", "source_image_ids")})
    # Exact rendered material values are supplied once in the targets. Check
    # every pose/dimension/mount independently of their deduplicated appearance.
    scene_objects = proposal.scene.model_dump(mode="json", exclude_none=True)["objects"]
    for item in scene_objects:
        if item["id"] in targets and targets[item["id"]]["appearance_controls"]:
            item.pop("color", None)
            item.pop("material_colors", None)
    for field in (
        "review_targets",
        "room_dimensions",
        "unresolved_entities",
        "estimated_axes",
    ):
        assert restored[field] == full_context[field]
    assert ReconstructionConstraints.model_validate(
        restored["human_answers"]
    ) == ReconstructionConstraints.model_validate(full_context["human_answers"])
    assert (
        restored["scene"]["objects"]
        == scene_objects
    )
    assert restored["observations"] == analysis.observations.model_dump(
        mode="json", exclude_none=True
    )
    images = lambda payload: [
        item for item in payload["input"][0]["content"] if item["type"] == "input_image"
    ]
    assert images(full) == images(compact)
    assert len(images(compact)) == len(analysis.observations.image_ids) + 3
    assert compact["max_output_tokens"] == full["max_output_tokens"] == 6144
    assert len(json.dumps(compact)) < len(json.dumps(full)) * 0.9
    assert (analysis.model_dump_json(), proposal.model_dump_json()) == original


@pytest.mark.parametrize("compact", [False, True])
def test_provider_photo_citations_validate_and_are_stored_as_original_ids(compact):
    analysis, proposal, requests = request_pair()
    request = requests[int(compact)]
    data = with_checks(consistent(), context(request))
    # Real structured-output schema remains strict after titles/UUID removal.
    validate(data, request["text"]["format"]["schema"])
    result = parse_judgment(data, analysis, proposal.scene, require_comparisons=True)
    assert result.checks
    assert all(
        set(check.source_image_ids) <= set(analysis.observations.image_ids)
        for check in result.checks
    )
    assert "photo_1" not in result.model_dump_json()


def test_foreign_alias_is_quarantined_without_discarding_other_checks():
    analysis, proposal, (_, compact) = request_pair()
    data = with_checks(consistent(), context(compact))
    data["checks"][0]["source_image_ids"] = ["photo_999"]
    result = parse_judgment(data, analysis, proposal.scene, require_comparisons=True)
    assert result.verdict == "uncertain"
    assert all("photo_999" not in check.source_image_ids for check in result.checks)
    assert any(check.placement == "consistent" for check in result.checks[1:])
    assert result.limitations


@pytest.mark.parametrize(
    "field,old",
    [("color", None), ("color", "#445566"), ("metal", None), ("wall_color", None)],
)
def test_repair_baseline_uses_exact_original_value_even_when_rendered_color_differs(
    field, old
):
    analysis, proposal, _ = request_pair()
    room_field = field == "wall_color"
    obj = analysis.observations.objects[0]
    if room_field:
        analysis.observations.finishes = None
    elif field == "color":
        obj.color = old
    else:
        obj.material_colors = MaterialColors()
    target = finding(
        "color",
        None if room_field else obj.id,
        appearance={"field": field, "value": "#2458B0"},
    )
    judgment = ReviewJudgment.model_validate(
        {
            "verdict": "needs_correction",
            "summary": "Visible material mismatch.",
            "findings": [target],
            "limitations": [],
            "checks": [],
        }
    )
    original = deepcopy(analysis)
    result = repair_review_context(
        analysis, proposal.scene, judgment, [(0, judgment.findings[0])]
    )
    baseline = result["appearance_revision_baselines"][0]
    assert baseline["previous_value"] == old
    assert (
        "findings" not in result["visual_review"]
        and "checks" not in result["visual_review"]
    )
    corrected, errors = resolve_appearance(
        analysis,
        [AppearanceCorrection(**baseline, reason="Photo-supported material color.")],
    )
    assert not errors and corrected != original
    assert analysis == original
