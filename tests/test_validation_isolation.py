"""Recoverable items cannot discard unrelated evidence or authorize unsafe edits."""

import json
from copy import deepcopy

import httpx
import pytest
from pydantic import ValidationError

from roometry.vision.proposal_schemas import SceneDraft
from roometry.vision.draft_validation import parse_scene_draft
from roometry.vision.proposals import assemble
from roometry.vision.review_policy import review_targets, select_findings
from roometry.vision.review_validation import parse_judgment, validate_judgment
from tests import test_vision_api as api
from tests.test_agent_tools import decision
from tests.test_partial_proposals import select
from tests.test_review_quality import finding
from tests.test_scene_proposals import analyzed, body, confirmed, draft_data, measurements, scene_action, source
from tests.test_visual_review import construction, discrepancy, enabled, review_action, run, with_checks

anyio_backend = api.anyio_backend
isolated_environment = api.isolated_environment
harness = api.harness
pytestmark = pytest.mark.anyio


def review_case():
    analysis = source()
    scene, _, _ = assemble(analysis, measurements(analysis), SceneDraft.model_validate(draft_data()))
    targets, _ = review_targets(analysis, scene)
    return analysis, scene, {"observations": analysis.observations.model_dump(mode="json"), "review_targets": targets}


def test_unsupported_identity_can_describe_appearance_without_implying_wrong_placement():
    analysis, scene, context = review_case()
    item = finding("identity", "bed_observed", repairable=False)
    data = with_checks({"verdict": "needs_correction", "summary": "The upholstery geometry differs.", "findings": [item], "limitations": []}, context)
    check = next(check for check in data["checks"] if check["entity_id"] == "bed_observed")
    check.update(placement="consistent", appearance="difference")
    judgment = parse_judgment(data, analysis, scene)
    validate_judgment(analysis, judgment, scene)
    assert judgment.findings[0].kind == "identity"
    check = next(check for check in judgment.checks if check.entity_id == "bed_observed")
    assert check.placement == "consistent" and check.appearance == "difference"
    assert select_findings(analysis, judgment) == []
    assert not judgment.limitations  # This is coherent, not an invalid review.


@pytest.mark.parametrize("defect", ["finding_schema", "foreign_photo", "foreign_entity", "bad_target", "missing_check", "bad_check", "duplicate_check", "contradiction", "bad_limitation"])
async def test_valid_correction_survives_an_unrelated_bad_review_item(harness, defect):
    app, _, calls, actions = harness
    runs, owner, uploaded = await enabled(harness)
    construction(actions)

    def mixed(payload):
        context = json.loads(payload["input"][0]["content"][0]["text"])
        data = with_checks(discrepancy(payload), context)
        extra = finding("color", "chair_a", appearance={"field": "color", "value": "#28558A"})
        extra["source_image_ids"] = context["observations"]["image_ids"][:1]
        chair = next(check for check in data["checks"] if check["entity_id"] == "chair_a")
        if defect in {"finding_schema", "foreign_photo", "foreign_entity", "bad_target"}:
            chair["appearance"] = "difference"
            if defect == "finding_schema": extra["PRIVATE_BAD_FIELD"] = True
            elif defect == "foreign_photo": extra["source_image_ids"] = ["PRIVATE_FOREIGN_PHOTO"]
            elif defect == "foreign_entity": extra["entity_ids"] = ["PRIVATE_FOREIGN_ENTITY"]
            else: extra["appearance_target"]["field"] = "enamel"
            data["findings"].append(extra)
        elif defect == "missing_check": data["checks"].remove(chair)
        elif defect == "bad_check": chair["source_image_ids"] = ["PRIVATE_FOREIGN_PHOTO"]
        elif defect == "duplicate_check": data["checks"].append({**deepcopy(chair), "placement": "PRIVATE_BAD_STATUS"})
        elif defect == "contradiction": chair["placement"] = "difference"
        else: data["limitations"] = [123, "Exact measurements are unknown."]
        return data

    fixed = deepcopy(draft_data()["objects"][0])
    fixed["rotation_y_rad"] = 3.141592653589793
    patch = {"wall_assignments": [], "openings": [], "objects": [fixed], "assumptions": [], "warnings": []}
    actions.extend([review_action(mixed), scene_action(patch), decision("finish")])
    result = await run(runs, owner, uploaded)
    review = result.proposal.visual_review
    assert review.status == "reviewed" and review.repair == "accepted"
    assert review.applied_findings == [0]
    assert review.judgment.limitations and "PRIVATE" not in result.model_dump_json()
    assert result.base_scene.objects[0].rotation_y_rad == pytest.approx(3.141592653589793)
    assert result.proposal.validation.valid and len(result.base_scene.objects) == 5
    assert result.base_scene.room.width_m == result.base_scene.room.length_m == 4
    assert len(calls) == 7
    assert sum(record.operation == "visual_review" for record in app.state.analysis_service.analyzer.usage_records) == 1


def test_an_uncertain_subject_cannot_authorize_a_correction_but_other_checks_survive():
    analysis, scene, context = review_case()
    data = with_checks({"verdict": "needs_correction", "summary": "Facing needs review.", "findings": [finding("orientation", "bed_observed", target={"relation": "faces", "target_id": "window_observed"})], "limitations": []}, context)
    next(check for check in data["checks"] if check["entity_id"] == "bed_observed")["placement"] = "consistent"
    judgment = parse_judgment(data, analysis, scene)
    assert not judgment.findings[0].repairable and select_findings(analysis, judgment) == []
    assert next(check for check in judgment.checks if check.entity_id == "bed_observed").placement == "uncertain"
    assert next(check for check in judgment.checks if check.entity_id == "desk_observed").placement == "consistent"


@pytest.mark.parametrize("defect", ["envelope", "findings_limit", "checks_limit", "wrong_collection"])
def test_review_envelope_and_resource_limits_still_fail_closed(defect):
    analysis, scene, context = review_case()
    data = with_checks({"verdict": "consistent", "summary": "Matches.", "findings": [], "limitations": []}, context)
    if defect == "envelope": data["unknown"] = True
    elif defect == "findings_limit": data["findings"] = [{}] * 13
    elif defect == "checks_limit": data["checks"] = [{}] * 26
    else: data["checks"] = {}
    with pytest.raises(ValidationError): parse_judgment(data, analysis, scene)


async def test_optional_analysis_details_are_isolated_without_retry_or_inventory_loss(harness):
    app, client, calls, actions = harness

    async def mixed(_request, payload):
        response = api.provider_response(payload)
        obs = api.observations_for(payload)
        own = obs["image_ids"][0]
        obs["walls"][0]["span"] = {"role": "shorter", "confidence": "high", "source_image_ids": ["44444444-4444-4444-8444-444444444444"], "evidence": "PRIVATE_UNSUPPORTED_SPAN"}
        good = {"id": "desk_near_window", "subject_id": "desk_observed", "target_id": "window_observed", "kind": "near", "confidence": "medium", "source_image_ids": [own], "evidence": "The workstation is at the window end.", "alternative_target_id": None}
        obs["relationships"] = [{**good, "id": "bad_relationship", "target_id": "PRIVATE_MISSING_OBJECT"}, good]
        obs["possible_duplicates"].append({"entity_ids": ["chair_a", "PRIVATE_MISSING_OBJECT"], "reason": "PRIVATE_DUPLICATE_HINT"})
        obs["finishes"] = {"finishes": {}, "confidence": "high", "source_image_ids": ["44444444-4444-4444-8444-444444444444"], "evidence": "PRIVATE_FINISH"}
        response["output"][0]["content"][0]["text"] = json.dumps(obs)
        return httpx.Response(200, json=response)

    actions.append(mixed)
    uploaded = await api.upload(client)
    response = await client.post("/api/rooms/analyze", json={"input_id": uploaded["input_id"]})
    assert response.status_code == 200, response.text
    obs = response.json()["observations"]
    assert len(obs["objects"]) == len(api.SOURCE["objects"])
    assert len(obs["openings"]) == len(api.SOURCE["openings"])
    assert obs["walls"][0]["span"] is None and obs["finishes"] is None
    assert [relation["id"] for relation in obs["relationships"]] == ["desk_near_window"]
    assert len(obs["possible_duplicates"]) == len(api.SOURCE["possible_duplicates"])
    assert "invalid optional details" in obs["warnings"][0] and "PRIVATE" not in response.text
    repeated = await client.post("/api/rooms/analyze", json={"input_id": uploaded["input_id"]})
    assert repeated.json() == response.json() and len(calls) == 1
    assert app.state.analysis_service.usage_records[-1].outcome == "success"


@pytest.mark.parametrize("field", ["appearance_corrections", "interpretation_corrections"])
def test_invalid_hypothesis_correction_group_does_not_discard_other_scene_items(field):
    analysis, data = source(), draft_data()
    obj = analysis.observations.objects[0]
    if field == "appearance_corrections":
        data[field] = [{"entity_id": obj.id, "field": "color", "previous_value": "#ABCDEF", "revised_value": "#FF0000", "reason": "Incorrect original color.", "source_image_ids": obj.source_image_ids}]
        valid = analysis.observations.objects[-1]
        data[field].append({"entity_id": valid.id, "field": "color", "previous_value": valid.color, "revised_value": "#28558A", "reason": "A valid color revision.", "source_image_ids": valid.source_image_ids})
    else:
        data[field] = [{"entity_id": obj.id, "field": "placement", "previous_value": "wall", "revised_value": "floor", "reason": "Incorrect original attachment.", "source_image_ids": obj.source_image_ids}]
    scene, report, issues, unresolved = select(analysis, data)
    assert scene is not None and report.valid and len(scene.objects) == 5
    assert not unresolved and issues and any("invalid" in issue.message for issue in issues)
    original_scene, _, _ = assemble(analysis, measurements(analysis), SceneDraft.model_validate(draft_data()))
    assert scene.objects[0].color == original_scene.objects[0].color
    if field == "appearance_corrections":
        assert next(item for item in scene.objects if item.id == valid.id).color == "#28558A"
        assert len(scene.appearance_corrections) == 1
    assert any("corrections were invalid" in note for note in scene.warnings)


@pytest.mark.parametrize("kind", ["objects", "openings"])
def test_malformed_known_pose_is_deferred_without_discarding_valid_draft(kind):
    analysis, data = source(), draft_data()
    original = deepcopy(data)
    data[kind][0]["PRIVATE_INVALID_FIELD"] = True
    draft = parse_scene_draft(data, analysis)
    assert len(getattr(draft, kind)) == len(original[kind]) - 1
    assert "PRIVATE" not in draft.model_dump_json()
    assert any("ignored" in warning for warning in draft.warnings)
    scene, report, issues, unresolved = select(analysis, draft.model_dump())
    assert scene is not None and report.valid
    assert set(unresolved) == {original[kind][0]["id"]}
    assert {item.id for item in scene.objects} >= {"desk_observed", "chair_a", "chair_b"}
    assert issues


@pytest.mark.parametrize("defect", ["unknown_id", "duplicate_id", "no_id", "bad_walls", "limit", "envelope"])
def test_draft_recovery_preserves_identity_envelope_and_size_boundaries(defect):
    analysis, data = source(), draft_data()
    data["objects"][0]["PRIVATE_INVALID_FIELD"] = True
    if defect == "unknown_id": data["objects"][0]["id"] = "invented"
    elif defect == "duplicate_id": data["objects"].append(deepcopy(data["objects"][0]))
    elif defect == "no_id": data["objects"][0].pop("id")
    elif defect == "bad_walls": data["wall_assignments"][0]["wall"] = "invented"
    elif defect == "limit": data["objects"] *= 41
    else: data["PRIVATE_INVALID_ENVELOPE"] = True
    with pytest.raises(ValidationError): parse_scene_draft(data, analysis)


@pytest.mark.parametrize("defect", ["malformed_pose", "bad_hypothesis"])
async def test_public_proposal_keeps_partial_scene_when_repair_cannot_finish(harness, defect):
    _, client, calls, actions = harness
    analysis = await analyzed(harness)
    data = draft_data()
    if defect == "malformed_pose":
        data["objects"][0]["position"]["x"] = "PRIVATE_NOT_A_NUMBER"
    else:
        data["interpretation_corrections"] = [{"entity_id": "bed_observed", "field": "placement", "previous_value": "wall", "revised_value": "floor", "reason": "Incorrect original attachment.", "source_image_ids": analysis["observations"]["objects"][0]["source_image_ids"]}]

    async def failed_repair(request, payload):
        response = api.provider_response({**payload, "input": [{"content": [{"text": json.dumps({"image_ids": analysis["observations"]["image_ids"]})}]}]})
        response["output"][0]["content"] = [{"type": "refusal", "refusal": "PRIVATE_REPAIR_FAILURE"}]
        return httpx.Response(200, json=response)

    actions.extend([scene_action(data), failed_repair])
    response = await client.post("/api/rooms/propose-scene", json=body(analysis, dimensions=confirmed(length_m=4, height_m=2.6)))
    assert response.status_code == 200, response.text
    proposal = response.json()
    assert proposal["status"] == "partial" and proposal["validation"]["valid"]
    assert len(proposal["scene"]["objects"]) == (4 if defect == "malformed_pose" else 5)
    assert proposal["scene"]["room"]["measurements_source"] == "user_confirmed"
    assert len(calls) == 3 and "PRIVATE" not in response.text
