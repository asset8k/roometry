"""Evidence-backed reconstruction recovery, through the real mocked API/graph."""

import json
from copy import deepcopy

import pytest
import jsonschema

from roometry.config import Settings
from roometry.scenes.schemas import InterpretationCorrection, RoomScene
from roometry.scenes.validation import validate_geometry
from roometry.vision.errors import AnalysisFailure
from roometry.vision.evidence import apply_constraints
from roometry.vision.fidelity import evaluate_fidelity
from roometry.vision.interpretations import resolve_interpretations
from roometry.vision.partial import assemble_partial
from roometry.vision.proposal_request import build_proposal_request
from roometry.vision.proposal_schemas import SceneDraft
from roometry.vision.proposals import assemble, issue
from roometry.vision.recovery import merge_repair, repair_scope
from roometry.vision.schemas import ReconstructionConstraints
from tests import test_vision_api as api
from tests.test_agent_tools import decision, photo, prepared
from tests.test_catalog_assembly import single_object_case
from tests.test_scene_proposals import (
    analyzed, body, confirmed, curtained_door_case, draft_data, measurements,
    scene_action, source,
)

anyio_backend = api.anyio_backend
isolated_environment = api.isolated_environment
harness = api.harness


def interpretation(obj, field, revised):
    return {
        "entity_id": obj.id, "field": field,
        "previous_value": getattr(obj, field), "revised_value": revised,
        "source_image_ids": obj.source_image_ids,
        "reason": "Visible feet meet the floor; the cabinet back rests against the wall rather than hanging from it.",
    }


def wardrobe_case():
    analysis, data = single_object_case("wardrobe", "wall")
    obj = analysis.observations.objects[0]
    obj.confidence, obj.appears_fixed = "high", True
    data["objects"][0]["placement"] = "floor"
    data["interpretation_corrections"] = [interpretation(obj, "placement", "floor")]
    return analysis, data


def test_fixed_floor_wardrobe_is_recovered_without_changing_source_or_mobility():
    analysis, data = wardrobe_case()
    original = analysis.model_dump()
    scene, report, problems = assemble(analysis, measurements(analysis), SceneDraft.model_validate(data))
    assert scene and report.valid and not problems
    assert scene.objects[0].type == "wardrobe" and scene.objects[0].placement == "floor"
    assert scene.objects[0].fixed and not scene.objects[0].locked
    assert scene.interpretation_corrections[0].previous_value == "wall"
    assert RoomScene.model_validate_json(scene.model_dump_json()) == scene
    assert analysis.model_dump() == original
    assert evaluate_fidelity(analysis, scene).major_object_coverage == 1
    # The same record must survive fallback selection, not be lost at preflight.
    partial, report, problems, unresolved = assemble_partial(analysis, measurements(analysis), SceneDraft.model_validate(data))
    assert partial == scene and report.valid and not problems and not unresolved


@pytest.mark.parametrize("defect", ["attachment_confidence", "foreign_photo", "wrong_before", "unchanged", "invented", "duplicate", "incompatible", "unaudited"])
def test_interpretation_revisions_require_original_evidence_and_valid_configuration(defect):
    analysis, data = wardrobe_case()
    record = data["interpretation_corrections"][0]
    if defect == "attachment_confidence":
        analysis.observations.objects[0].placement_confidence = "high"
    elif defect == "foreign_photo":
        record["source_image_ids"] = ["foreign_view"]
    elif defect == "wrong_before":
        record["previous_value"] = "ceiling"
    elif defect == "unchanged":
        record["revised_value"] = "wall"
    elif defect == "invented":
        record["entity_id"] = "invented_wardrobe"
    elif defect == "duplicate":
        data["interpretation_corrections"].append(deepcopy(record))
    elif defect == "incompatible":
        record["revised_value"] = "ceiling"
    else:
        data["interpretation_corrections"] = []
    scene, _, problems = assemble(analysis, measurements(analysis), SceneDraft.model_validate(data))
    assert scene is None and problems


def test_only_recorded_uncertain_alternative_identity_can_change_and_user_answer_wins():
    analysis, data = single_object_case("bed")
    obj = analysis.observations.objects[0]
    obj.confidence, obj.alternative_type = "medium", "sofa"
    record = InterpretationCorrection.model_validate(interpretation(obj, "type", "sofa"))
    effective, errors = resolve_interpretations(analysis, [record])
    assert not errors and effective.observations.objects[0].type == "sofa"
    confirmed_analysis = apply_constraints(analysis, ReconstructionConstraints(object_types={obj.id: "bed"}))
    assert resolve_interpretations(confirmed_analysis, [record])[1]
    bad = record.model_copy(update={"revised_value": "wardrobe"})
    assert resolve_interpretations(analysis, [bad])[1]


def test_support_revision_reconciles_only_uncertain_support_and_keeps_source_immutable():
    analysis, data = source(), draft_data()
    child = analysis.observations.objects[2]
    child.placement_confidence = "medium"
    # A low-confidence mistaken support can be corrected to another observed
    # surface provider. A high-confidence relationship must not be rewritten.
    analysis.observations.relationships = []
    wrong = analysis.observations.objects[0].id
    child.support_object_id = wrong
    from roometry.vision.schemas import SpatialRelationship
    relation = SpatialRelationship(
        id="support_hypothesis", subject_id=child.id, target_id=wrong,
        kind="supported_by", confidence="medium", source_image_ids=child.source_image_ids,
        evidence="The support is partly occluded.",
    )
    analysis.observations.relationships = [relation]
    data["interpretation_corrections"] = [interpretation(child, "support_object_id", "desk_observed")]
    original = analysis.model_dump()
    scene, report, errors = assemble(analysis, measurements(analysis), SceneDraft.model_validate(data))
    assert scene and report.valid and not errors
    assert evaluate_fidelity(analysis, scene).checks
    assert analysis.model_dump() == original
    analysis.observations.relationships[0].confidence = "high"
    assert assemble(analysis, measurements(analysis), SceneDraft.model_validate(data))[0] is None


def patch_data(*, objects=(), openings=()):
    return SceneDraft.model_validate({"wall_assignments": [], "objects": list(objects), "openings": list(openings), "assumptions": [], "warnings": []})


def test_sparse_repair_restores_only_missing_observed_entries_without_deletions():
    analysis, data = source(), draft_data()
    missing_object = data["objects"].pop(0)
    missing_opening = data["openings"].pop(0)
    previous = SceneDraft.model_validate(data)
    problems = [issue("identity_mismatch", "Incomplete inventory.")]
    assert repair_scope(analysis, previous, problems) == {"bed_observed", "window_observed"}
    merged = merge_repair(analysis, previous, patch_data(objects=[missing_object], openings=[missing_opening]), problems)
    assert len(merged.objects) == 5 and len(merged.openings) == 2
    assert all(obj in merged.objects for obj in previous.objects)
    assert all(opening in merged.openings for opening in previous.openings)
    scene, report, errors = assemble(analysis, measurements(analysis), merged)
    assert scene and report.valid and not errors
    assert scene.room.width_m == 4 and scene.room.length_m == 4


@pytest.mark.parametrize("defect", ["unaffected_pose", "unaffected_size", "unaffected_asset", "invented", "duplicate", "wall_mapping"])
def test_patch_cannot_rewrite_unaffected_entities_or_invent_inventory(defect):
    analysis, data = source(), draft_data()
    data["objects"][0]["position"]["x"] = -10
    previous = SceneDraft.model_validate(data)
    objects = []
    walls = []
    if defect == "wall_mapping":
        walls = [{"observation_id": "window_wall", "wall": "south"}, {"observation_id": "door_wall", "wall": "east"}]
    else:
        changed = deepcopy(data["objects"][1])
        if defect == "unaffected_pose":
            changed["position"]["x"] += .2
        elif defect == "unaffected_size":
            changed["dimensions_m"]["width"] = 1.3
        elif defect == "unaffected_asset":
            changed["asset_id"] = "desk_compact_01"
        elif defect == "invented":
            changed["id"] = "unobserved_desk"
        objects = [changed, deepcopy(changed)] if defect == "duplicate" else [changed]
    patch = patch_data(objects=objects).model_copy(update={"wall_assignments": SceneDraft.model_validate({**data, "wall_assignments": walls}).wall_assignments})
    with pytest.raises(AnalysisFailure, match="invalid_analysis") as failure:
        merge_repair(analysis, previous, patch, [issue("geometry", "Outside room.", "bed_observed")])
    assert "protected or unobserved" in failure.value.response.detail[0].message


def test_shell_first_schema_and_context_use_measured_spans_and_distinguish_hypotheses():
    analysis, data = wardrobe_case()
    request = build_proposal_request(Settings(_env_file=None), analysis, measurements(analysis), None, [])
    context = json.loads(request["input"][0]["content"][0]["text"])
    assert context["asset_bindings"][0]["models"] == []
    assert any(item["type"] == "wardrobe" and item["placement"] == "floor" for item in context["hypothesis_alternatives"])
    assert list(request["text"]["format"]["schema"]["properties"])[:2] == ["wall_assignments", "openings"]
    assert context["room_shell"]["walls"]["north"]["span_m"] == 4
    analysis.observations.objects[0].placement_confidence = "high"
    protected = build_proposal_request(Settings(_env_file=None), analysis, measurements(analysis), None, [])
    assert not json.loads(protected["input"][0]["content"][0]["text"])["hypothesis_alternatives"]


def test_missing_window_reduces_coverage_even_when_all_furniture_survives():
    analysis, data = source(), draft_data()
    data["openings"].pop(0)
    draft = SceneDraft.model_validate(data)
    scene, _, _, unresolved = assemble_partial(analysis, measurements(analysis), draft)
    assert scene and {item.entity_id for item in unresolved} == {"window_observed"}
    report = evaluate_fidelity(analysis, scene)
    assert report.major_object_coverage < 1 and report.recognizable is False
    assert any(check.entity_ids == ["window_observed"] and not check.passed for check in report.checks)


def test_repair_output_schema_prevents_unaffected_rewrites_before_decoding():
    analysis, data = source(), draft_data()
    bed = data["objects"].pop(0)
    previous = SceneDraft.model_validate(data)
    payload = build_proposal_request(Settings(_env_file=None), analysis, measurements(analysis), previous,
                                     [issue("identity_mismatch", "The bed was omitted.", "bed_observed")], operation="scene_repair")
    schema = payload["text"]["format"]["schema"]
    patch = patch_data(objects=[bed]).model_dump(mode="json")
    jsonschema.validate(patch, schema)
    for changed in [patch_data(objects=[data["objects"][0]]), patch_data(openings=data["openings"])]:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(changed.model_dump(mode="json"), schema)
    assert schema["properties"]["wall_assignments"]["maxItems"] == 0


def test_wall_topology_repair_can_reassign_shell_but_still_must_pass_axis_and_evidence_checks():
    analysis, data = source(), draft_data()
    previous = SceneDraft.model_validate(data)
    problems = [issue("fidelity", "Preserve observed wall opposition.", "window_wall", "door_wall")]
    assert repair_scope(analysis, previous, problems) == {item.id for item in [*analysis.observations.objects, *analysis.observations.openings]}
    patch = patch_data().model_copy(update={"wall_assignments": [
        item.model_copy(update={"wall": "south" if item.observation_id == "window_wall" else "east"})
        for item in previous.wall_assignments
    ]})
    merged = merge_repair(analysis, previous, patch, problems)
    assert merged.wall_assignments[0].wall == "south"
    # Wall reassignment alone cannot disguise the now-wrong opening wall.
    assert assemble(analysis, measurements(analysis), merged)[0] is None


@pytest.mark.anyio
async def test_api_restores_opening_and_its_covering_using_one_sparse_luna_repair(harness, monkeypatch):
    app, client, calls, actions = harness
    analysis, good = curtained_door_case()
    monkeypatch.setattr(api, "SOURCE", analysis.observations.model_dump())
    uploaded = await analyzed(harness)
    bad = deepcopy(good)
    bad["openings"][0]["offset_m"] = 20
    # There is no usable partial until the opening and dependent curtain recover.
    patch = patch_data(openings=good["openings"]).model_dump()
    actions.extend([scene_action(bad), scene_action(patch)])
    response = await client.post("/api/rooms/propose-scene", json=body(uploaded, dimensions=confirmed(length_m=4, height_m=2.6)))
    assert response.status_code == 200 and response.json()["status"] == "ready", response.text
    scene = RoomScene.model_validate(response.json()["scene"])
    assert len(scene.objects) == len(scene.openings) == 1 and validate_geometry(scene).valid
    assert [call["model"] for call in calls] == ["gpt-6-luna", "gpt-6.1-sol", "gpt-6-luna"]
    context = json.loads(calls[-1]["input"][0]["content"][0]["text"])
    assert context["repair"]["mode"] == "upsert"
    assert set(context["repair"]["editable_entity_ids"]) == {"door_observed", "curtains_observed"}
    assert app.state.analysis_service._states[uploaded["input_id"]].scene_attempts == 2


@pytest.mark.anyio
async def test_graph_recovers_missing_anchors_without_reanalysis_or_changing_valid_furniture(harness):
    app, _, calls, actions = harness
    runs, owner, uploaded = await prepared(harness)
    good, bad = draft_data(), draft_data()
    bed = bad["objects"].pop(0)
    window = bad["openings"].pop(0)
    patch = patch_data(objects=[bed], openings=[window]).model_dump()
    actions.extend([decision("analyze_photos"), photo, decision("propose_scene"), scene_action(bad), scene_action(patch), decision("finish")])
    initial = await runs.create(uploaded["input_id"], owner)
    result = await runs.execute(initial.run_id, owner)
    assert result.status == "completed" and result.working_scene == result.base_scene
    assert len(result.base_scene.objects) == 5 and len(result.base_scene.openings) == 2
    assert result.proposal.status == "ready" and result.proposal.validation.valid
    unchanged = {obj.id: obj for obj in SceneDraft.model_validate(good).objects}
    for obj in result.base_scene.objects:
        assert obj.id in unchanged
        assert obj.dimensions_m == unchanged[obj.id].dimensions_m
    operations = [entry.operation for entry in app.state.analysis_service.analyzer.usage_records]
    assert operations.count("analysis") == operations.count("scene_proposal") == operations.count("scene_repair") == 1
    assert len(calls) == 6
