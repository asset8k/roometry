"""Qualitative photo evidence versus measured layout/materials; no live calls."""

import json
from copy import deepcopy
from math import pi

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from roometry.scenes.catalog import object_surfaces
from roometry.scenes.schemas import MaterialColors
from roometry.scenes.validation import validate_geometry
from roometry.vision.proposal_schemas import SceneDraft
from roometry.vision.proposals import assemble
from roometry.vision.review_policy import finding_progress, review_targets, select_findings, MAX_MATERIAL_COMPARISONS, MAX_RELATION_COMPARISONS
from roometry.vision.review_validation import parse_judgment
from roometry.vision.evidence import strict_schema
from roometry.vision.review_schemas import AppearanceTarget, MaterialComparison
from roometry.vision.schemas import RoomAnalysisResponse
from tests import test_vision_api as api
from tests.test_agent_tools import decision
from tests.test_review_quality import far_desk
from tests.test_scene_proposals import draft_data, measurements, scene_action, source
from tests.test_visual_review import consistent, construction, enabled, review_action, run, with_checks

anyio_backend = api.anyio_backend
isolated_environment = api.isolated_environment
harness = api.harness
pytestmark = pytest.mark.anyio


def candidate(data=None):
    analysis = source()
    scene, report, issues = assemble(analysis, measurements(analysis), SceneDraft.model_validate(data or draft_data()))
    assert scene is not None and report.valid and not issues
    return analysis, scene


def detailed(analysis, scene):
    context = {"observations": analysis.observations.model_dump(mode="json"), "review_targets": review_targets(analysis, scene)[0]}
    return with_checks(consistent(), context), context


def test_detailed_relationship_evidence_overrides_an_unreliable_overall_match_vote():
    analysis, scene = candidate(far_desk())
    data, _ = detailed(analysis, scene)
    check = next(item for item in data["checks"] if item["entity_id"] == "desk_observed")
    assert check["placement"] == "consistent"
    next(item for item in check["relations"] if item["target_id"] == "window_observed")["photo_support"] = "supported"
    judgment = parse_judgment(data, analysis, scene, require_comparisons=True)
    assert judgment.verdict == "needs_correction"
    findings = select_findings(analysis, judgment)
    assert len(findings) == 1
    assert findings[0][1].placement_target.target_id == "window_observed"
    assert next(item for item in judgment.checks if item.entity_id == "desk_observed").placement == "difference"


@pytest.mark.parametrize("distance, needs_correction", [(0., False), (.5, False), (2., True)])
def test_half_meter_variation_is_accepted_but_wrong_room_end_is_not(distance, needs_correction):
    analysis = source()
    raw = analysis.model_dump()
    obs = raw["observations"]
    obs["objects"] = [obs["objects"][1]]
    obs["walls"] = [obs["walls"][0]]
    obs["openings"] = [obs["openings"][0]]
    obs["possible_duplicates"] = []
    analysis = RoomAnalysisResponse.model_validate(raw)
    data = draft_data()
    data["objects"] = [data["objects"][1]]
    data["objects"][0]["position"].update(x=1.5, z=.8 + distance)
    data["wall_assignments"] = data["wall_assignments"][:1]
    data["openings"] = data["openings"][:1]
    scene, report, _ = assemble(analysis, measurements(analysis), SceneDraft.model_validate(data))
    assert scene and report.valid
    review, _ = detailed(analysis, scene)
    check = next(item for item in review["checks"] if item["entity_id"] == "desk_observed")
    check["relations"][0]["photo_support"] = "supported"
    judgment = parse_judgment(review, analysis, scene, require_comparisons=True)
    assert bool(select_findings(analysis, judgment)) is needs_correction


@pytest.mark.parametrize("angle, needs_correction", [(pi, False), (pi + pi / 6, False), (0., True)])
def test_facing_uses_a_broad_angle_band_but_detects_reversed_furniture(angle, needs_correction):
    analysis, scene = candidate()
    chair = next(item for item in scene.objects if item.id == "chair_a")
    desk = next(item for item in scene.objects if item.id == "desk_observed")
    desk.position.x = chair.position.x
    desk.position.z = chair.position.z - 1.4
    chair.rotation_y_rad = angle
    data, _ = detailed(analysis, scene)
    check = next(item for item in data["checks"] if item["entity_id"] == "chair_a")
    next(item for item in check["relations"] if item["relation"] == "faces")["photo_support"] = "supported"
    judgment = parse_judgment(data, analysis, scene, require_comparisons=True)
    assert any(f.kind == "orientation" for _, f in select_findings(analysis, judgment)) is needs_correction


def test_blue_in_the_wrong_region_is_two_material_differences_not_a_color_match():
    analysis, scene = candidate()
    chair = next(item for item in scene.objects if item.id == "chair_a")
    chair.asset_id = "chair_office_01"
    chair.color = "#111111"
    chair.material_colors = MaterialColors(inset="#28558A", metal="#17191B")
    data, _ = detailed(analysis, scene)
    check = next(item for item in data["checks"] if item["entity_id"] == "chair_a")
    for region in check["materials"]:
        if region["field"] == "color": region["photo_value"] = "#28558A"
        if region["field"] == "inset": region["photo_value"] = "#111111"
    judgment = parse_judgment(data, analysis, scene, require_comparisons=True)
    findings = [f for _, f in select_findings(analysis, judgment) if f.entity_ids == [chair.id]]
    assert {f.appearance_target.field for f in findings} == {"color", "inset"}
    wrong = scene.model_copy(deep=True)
    next(item for item in wrong.objects if item.id == chair.id).material_colors.metal = "#28558A"
    assert not any(finding_progress(scene, wrong, finding)[0] for finding in findings)
    corrected = scene.model_copy(deep=True)
    target = next(item for item in corrected.objects if item.id == chair.id)
    target.color, target.material_colors.inset = "#28558A", "#111111"
    assert all(finding_progress(scene, corrected, finding) == (True, False) for finding in findings)


@pytest.mark.parametrize("model, value_key", [(MaterialComparison, "photo_value"), (AppearanceTarget, "value")])
def test_provider_schema_rejects_the_color_prose_seen_in_live_reviews(model, value_key):
    validator = Draft202012Validator(strict_schema(model))
    for field, value in [("color", "#2458B0"), ("inset", "#202124"), ("floor_pattern", "wood")]:
        data = {"field": field, value_key: value}
        validator.validate(data)
        model.model_validate(data)
    for value in ("blue", "Brown wood", "Black center and", "wood planks", "#12345", "#1234567"):
        data = {"field": "color", value_key: value}
        assert not validator.is_valid(data), value
        with pytest.raises(ValidationError):
            model.model_validate(data)
    # The shared value union cannot by itself enforce field pairing; local
    # validation remains authoritative for malformed/legacy provider responses.
    for field, value in [("color", "tile"), ("floor_pattern", "#2458B0")]:
        with pytest.raises(ValidationError):
            model.model_validate({"field": field, value_key: value})
    if model is MaterialComparison:
        validator.validate({"field": "color", value_key: None})


def test_exact_pattern_limitation_does_not_block_an_actionable_approximate_color_fix():
    analysis, scene = candidate()
    chair = next(item for item in scene.objects if item.id == "chair_a")
    chair.asset_id = "chair_office_01"
    chair.color = "#111111"
    chair.material_colors = MaterialColors(inset="#28558A")
    data, _ = detailed(analysis, scene)
    check = next(item for item in data["checks"] if item["entity_id"] == chair.id)
    for answer in check["materials"]:
        if answer["field"] == "color": answer["photo_value"] = "#28558A"
        if answer["field"] == "inset": answer["photo_value"] = "#111111"
    data["findings"] = [{
        "kind": "identity", "entity_ids": [chair.id], "confidence": "high", "severity": "minor",
        "source_image_ids": check["source_image_ids"], "view_ids": ["cutaway_se"],
        "description": "The exact gaming-chair stitching is unavailable.",
        "correction": "Keep the approximate model and disclose the remaining pattern difference.",
        "placement_target": None, "appearance_target": None, "repairable": False,
    }]
    judgment = parse_judgment(data, analysis, scene, require_comparisons=True)
    colors = [finding for _, finding in select_findings(analysis, judgment, scene) if finding.kind == "color"]
    assert {finding.appearance_target.field for finding in colors} == {"color", "inset"}
    assert any(finding.kind == "identity" and not finding.repairable for finding in judgment.findings)



@pytest.mark.parametrize("defect", ["missing", "invalid", "duplicate", "unknown"])
def test_uncertain_material_region_does_not_block_another_valid_region(defect):
    analysis, scene = candidate()
    chair = next(item for item in scene.objects if item.id == "chair_a")
    chair.asset_id = "chair_office_01"
    chair.color = "#111111"
    chair.material_colors = MaterialColors(inset="#111111", metal="#17191B")
    data, _ = detailed(analysis, scene)
    check = next(item for item in data["checks"] if item["entity_id"] == chair.id)
    next(item for item in check["materials"] if item["field"] == "color")["photo_value"] = "#28558A"
    inset = next(item for item in check["materials"] if item["field"] == "inset")
    if defect == "missing": check["materials"].remove(inset)
    elif defect == "invalid": inset["photo_value"] = 42
    elif defect == "duplicate": check["materials"].append({**inset, "photo_value": "PRIVATE_INVALID"})
    else: inset["photo_value"] = None
    judgment = parse_judgment(data, analysis, scene, require_comparisons=True)
    findings = [f for _, f in select_findings(analysis, judgment) if f.kind == "color"]
    assert len(findings) == 1 and findings[0].appearance_target.field == "color"
    assert judgment.limitations and "PRIVATE" not in judgment.model_dump_json()


def test_comparison_work_is_bounded_and_uses_actual_part_names():
    analysis, scene = candidate()
    targets, _ = review_targets(analysis, scene)
    assert sum(len(target["relation_probes"]) for target in targets) <= MAX_RELATION_COMPARISONS
    assert sum(len(target["material_probes"]) for target in targets) <= MAX_MATERIAL_COMPARISONS
    assert all(probe["correction_trigger"] > probe["correction_target"] for target in targets for probe in target["relation_probes"])


async def test_correcting_a_local_group_moves_the_desk_and_its_devices_in_one_bounded_call(harness):
    app, _, calls, actions = harness
    runs, owner, uploaded = await enabled(harness)
    construction(actions, far_desk())

    def comparison(payload):
        context = json.loads(payload["input"][0]["content"][0]["text"])
        data = with_checks(consistent(), context)
        desk = next(item for item in data["checks"] if item["entity_id"] == "desk_observed")
        desk["relations"][0]["photo_support"] = "supported"
        return data

    patch = {"wall_assignments": [], "openings": [], "objects": draft_data()["objects"][1:3], "assumptions": [], "warnings": []}
    actions.extend([review_action(comparison), scene_action(patch), decision("finish")])
    result = await run(runs, owner, uploaded)
    assert result.proposal.visual_review.repair == "accepted"
    assert result.proposal.visual_review.applied_findings == [0]
    assert all(item.position.z == .6 for item in result.base_scene.objects if item.id in {"desk_observed", "computer_observed"})
    assert validate_geometry(result.base_scene).valid and len(result.base_scene.objects) == 5
    assert len(calls) == 7 and sum(item.operation == "visual_repair" for item in app.state.analysis_service.analyzer.usage_records) == 1


def test_integrated_desk_keeps_one_identity_and_uses_its_worktop_below_the_hutch():
    analysis, data = candidate()
    raw = analysis.model_dump()
    raw["observations"]["objects"] = raw["observations"]["objects"][1:3]
    raw["observations"]["possible_duplicates"] = []
    raw["observations"]["objects"][0].update(estimated_dimensions_m=None, label="Desk with integrated shelving and hutch")
    analysis = RoomAnalysisResponse.model_validate(raw)
    draft = draft_data()
    draft["objects"] = draft["objects"][1:3]
    desk, monitor = draft["objects"]
    desk.update(asset_id="desk_shelved_01", dimensions_m=None)
    monitor["position"].update(x=3.2, z=.6)
    scene, report, issues = assemble(analysis, measurements(analysis), SceneDraft.model_validate(draft))
    assert scene is not None and report.valid and not issues
    assert [item.id for item in scene.objects] == ["desk_observed", "computer_observed"]
    computer = next(item for item in scene.objects if item.id == "computer_observed")
    assert computer.position.y == pytest.approx(.75)
    surfaces = object_surfaces("desk_shelved_01", "floor")
    assert next(item for item in surfaces if item.name == computer.support_surface_id).part == "worktop"
    assert max(surface.y for surface in surfaces) * 1.4 > 1.3
