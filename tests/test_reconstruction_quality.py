"""Photo-grounded construction, relationship checks and real interrupt regression."""

import json
from copy import deepcopy
from math import pi
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from roometry.agents.clarification import answer_updates, question_for
from roometry.agents.state import AgentState, ClarificationAnswer
from roometry.scenes.schemas import RoomScene
from roometry.vision.evidence import apply_constraints, strict_schema
from roometry.vision.fidelity import evaluate_fidelity
from roometry.vision.proposal_request import build_proposal_request
from roometry.vision.proposal_schemas import SceneDraft
from roometry.vision.proposals import assemble
from roometry.vision.schemas import EstimatedObjectDimensions, ReconstructionConstraints, RoomAnalysisResponse, RoomObservations
from tests import test_vision_api
from tests.test_agent_graph import OWNER, Room
from tests.test_agent_tools import decision, prepared
from tests.test_catalog_assembly import single_object_case
from tests.test_scene_proposals import draft_data, measurements, scene_action

anyio_backend = test_vision_api.anyio_backend
isolated_environment = test_vision_api.isolated_environment
harness = test_vision_api.harness


def facing_case(wall="north"):
    analysis, data = single_object_case()
    obs = analysis.observations
    photo_ids = obs.image_ids
    obs.walls = [Room().analysis.observations.walls[0]]
    obs.objects[0].id = "sofa"
    obs.objects[0].source_image_ids = photo_ids
    raw = analysis.model_dump()
    raw["observations"]["openings"] = [{
        "id": "window", "type": "window", "wall_id": "window_wall",
        "source_image_ids": photo_ids, "confidence": "high", "relative_location": "End wall",
        "estimated_width_m": 1.2, "estimated_height_m": 1.2, "estimated_sill_height_m": 1.1, "notes": [],
    }]
    raw["observations"]["relationships"] = [{
        "id": "sofa_facing", "subject_id": "sofa", "target_id": "window", "kind": "faces",
        "confidence": "high", "source_image_ids": photo_ids, "evidence": "The open seat side faces the end opening.", "alternative_target_id": None,
    }]
    analysis = RoomAnalysisResponse.model_validate(raw)
    data.update(wall_assignments=[{"observation_id": "window_wall", "wall": wall}], openings=[{
        "id": "window", "wall": wall, "offset_m": 1.4, "width_m": 1.2, "height_m": 1.2, "sill_height_m": 1.1,
    }])
    data["objects"][0].update(id="sofa", rotation_y_rad={"north": pi, "south": 0., "east": pi / 2, "west": 3 * pi / 2}[wall])
    return analysis, data


def block_local_turn(data):
    # The north-facing diagonal would extend outside the west boundary. Unlike
    # a simple 180-degree flip, this needs a model-led translation and must keep
    # exercising bounded repair / provisional-scene fallback.
    data['objects'][0].update(rotation_y_rad=0.,
        position={'x': .5, 'y': 0., 'z': 2.},
        dimensions_m={'width': .8, 'height': .85, 'depth': 2.2})


@pytest.mark.parametrize("wall", ["north", "south", "east", "west"])
def test_wrong_facing_is_rejected_even_without_any_collision(wall):
    analysis, data = facing_case(wall)
    scene, report, issues = assemble(analysis, measurements(analysis), SceneDraft.model_validate(data))
    assert scene is not None and report.valid and not issues
    assert evaluate_fidelity(analysis, scene).recognizable is True
    data["objects"][0]["rotation_y_rad"] += pi
    rejected, geometry, issues = assemble(analysis, measurements(analysis), SceneDraft.model_validate(data))
    assert rejected is None and geometry.valid
    assert issues[0].code == "fidelity" and issues[0].entity_ids == ["sofa", "window"]


@pytest.mark.parametrize("mutation", ["foreign_evidence", "dangling", "self", "wall_kind"])
def test_relationships_must_have_real_endpoints_and_current_photo_evidence(mutation):
    analysis, _ = facing_case()
    raw = analysis.model_dump()
    relation = raw["observations"]["relationships"][0]
    if mutation == "foreign_evidence":
        relation["source_image_ids"] = [str(uuid4())]
    elif mutation == "dangling":
        relation["target_id"] = "invented_tv"
    elif mutation == "self":
        relation["target_id"] = "sofa"
    else:
        relation["kind"] = "opposite_wall"
    with pytest.raises(ValidationError):
        RoomAnalysisResponse.model_validate(raw)


def test_estimate_revisions_are_audited_and_cannot_change_room_measurements():
    analysis, data = single_object_case()
    source = analysis.observations.objects[0]
    source.estimated_dimensions_m = EstimatedObjectDimensions(width=2.5, depth=None, height=None)
    data["objects"][0]["dimensions_m"] = {"width": 2.2, "height": .88, "depth": .95}
    assert assemble(analysis, measurements(analysis), SceneDraft.model_validate(data))[0] is None
    correction = {"entity_id": source.id, "field": "width", "previous_m": 2.5, "revised_m": 2.2, "reason": "The second view shows the arm-to-arm span relative to the doorway.", "source_image_ids": source.source_image_ids}
    data["estimate_corrections"] = [correction]
    original = analysis.model_dump()
    scene, report, errors = assemble(analysis, measurements(analysis), SceneDraft.model_validate(data))
    assert scene and report.valid and not errors
    assert scene.estimate_corrections[0].previous_m == 2.5
    assert scene.room.width_m == 4 and scene.room.measurements_source == "user_confirmed"
    assert scene.objects[0].measurements_source == "estimated"
    assert analysis.model_dump() == original
    for patch in [{"source_image_ids": [str(uuid4())]}, {"previous_m": 3.}, {"revised_m": 1.}, {"entity_id": "room"}]:
        changed = deepcopy(data)
        changed["estimate_corrections"][0].update(patch)
        assert assemble(analysis, measurements(analysis), SceneDraft.model_validate(changed))[0] is None


def test_structured_output_schema_is_strict_even_for_backward_compatible_fields():
    for model in (RoomObservations, SceneDraft):
        schema = strict_schema(model)
        for item in [schema, *schema["$defs"].values()]:
            if item.get("type") == "object":
                assert set(item["required"]) == set(item["properties"])
                assert item["additionalProperties"] is False
                assert all("default" not in prop for prop in item["properties"].values())


@pytest.mark.anyio
@pytest.mark.parametrize("kind,choice", [("object_identity", "use_alternative"), ("layout_orientation", "face_alternative")])
async def test_new_questions_use_real_interrupt_resume_without_repeating_analysis(harness, kind, choice):
    _app, client, calls, actions = harness
    runs, owner, upload = await prepared(harness)

    async def photo(request, payload):
        body = test_vision_api.provider_response(payload)
        observations = json.loads(body["output"][0]["content"][0]["text"])
        bed = observations["objects"][0]
        if kind == "object_identity":
            bed["alternative_type"] = "sofa"
        else:
            observations["relationships"] = [{"id": "bed_facing", "subject_id": bed["id"], "target_id": "desk_observed", "alternative_target_id": "window_observed", "kind": "faces", "confidence": "medium", "source_image_ids": bed["source_image_ids"], "evidence": "The front is partly occluded; the desk and window are plausible targets."}]
        body["output"][0]["content"][0]["text"] = json.dumps(observations)
        return httpx.Response(200, json=body)

    actions.extend([decision("analyze_photos"), photo, decision("request_clarification", {"kind": kind, "object_id": "bed_observed"})])
    initial = await runs.create(upload["input_id"], owner)
    paused = await runs.execute(initial.run_id, owner)
    assert paused.status == "awaiting_answer" and len(calls) == 3
    assert len(paused.pending_question.choices) == 3
    original = paused.analysis.model_dump()
    construction = draft_data()
    if kind == "layout_orientation":
        construction["objects"][0]["rotation_y_rad"] = pi
    actions.extend([decision("propose_scene"), scene_action(construction), decision("finish")])
    body = {"question_id": "q1", "choice_id": choice}
    response = await client.post(f"/api/agent-runs/{initial.run_id}/answer", json=body)
    assert response.status_code == 200, response.text
    resumed = AgentState.model_validate_json(response.text)
    assert resumed.analysis.model_dump() == original and len(calls) == 6
    assert resumed.status == "completed"
    effective = apply_constraints(resumed.analysis, resumed.reconstruction_constraints)
    if kind == "object_identity":
        assert effective.observations.objects[0].type == "sofa"
        assert resumed.reconstruction_constraints.object_types == {"bed_observed": "sofa"}
    else:
        assert effective.observations.relationships[0].target_id == "window_observed"
        assert effective.observations.relationships[0].confidence == "high"
    assert (await client.post(f"/api/agent-runs/{initial.run_id}/answer", json=body)).json() == resumed.model_dump(mode="json")
    assert len(calls) == 6


@pytest.mark.anyio
async def test_construction_and_repair_receive_every_owned_photo_with_matching_ids(harness):
    _, client, calls, actions = harness
    upload = await test_vision_api.upload(client, count=2)
    analysis = (await client.post("/api/rooms/analyze", json={"input_id": upload["input_id"]})).json()
    bad = draft_data()
    bad["objects"][0]["position"]["x"] = -10
    actions.extend([scene_action(bad), scene_action()])
    result = await client.post("/api/rooms/propose-scene", json={"analysis_id": analysis["analysis_id"], "dimensions": {"length_m": 4., "height_m": 2.6, "measurements_source": "user_confirmed"}})
    assert result.status_code == 200 and result.json()["status"] == "ready", result.text
    for payload in calls[-2:]:
        content = payload["input"][0]["content"]
        images = [part for part in content if part["type"] == "input_image"]
        assert len(images) == 2 and all(image["detail"] == "high" for image in images)
        for index, metadata in enumerate(upload["images"]):
            assert {"type": "input_text", "text": f"Photo evidence ID: photo_{index+1}"} in content
    assert result.json()["fidelity"]["major_object_coverage"] == 1.
    assert "data:image" not in json.dumps(result.json())


@pytest.mark.anyio
@pytest.mark.parametrize("repair_succeeds", [True, False])
async def test_fidelity_feedback_reaches_one_bounded_repair_without_deleting_the_anchor(harness, repair_succeeds):
    _app, client, calls, actions = harness
    _runs, _owner, uploaded = await prepared(harness)
    example, good = facing_case()
    bad = deepcopy(good)
    block_local_turn(bad)
    good['objects'][0]['dimensions_m'] = deepcopy(bad['objects'][0]['dimensions_m'])

    async def photo(request, payload):
        response = test_vision_api.provider_response(payload)
        ids = json.loads(payload["input"][0]["content"][0]["text"])["image_ids"]
        observations = example.observations.model_dump(mode="json")
        observations["image_ids"] = ids
        for kind in ("walls", "objects", "openings", "relationships"):
            for entity in observations[kind]:
                entity["source_image_ids"] = ids
        response["output"][0]["content"][0]["text"] = json.dumps(observations)
        return httpx.Response(200, json=response)

    actions.extend([photo, scene_action(bad), scene_action(good if repair_succeeds else bad)])
    analysis = (await client.post("/api/rooms/analyze", json={"input_id": uploaded["input_id"]})).json()
    response = await client.post("/api/rooms/propose-scene", json={"analysis_id": analysis["analysis_id"]})
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(calls) == 3
    repair_context = json.loads(calls[-1]["input"][0]["content"][0]["text"])
    assert repair_context["validation_feedback"][0]["code"] == "fidelity"
    assert "sofa" in repair_context["validation_feedback"][0]["entity_ids"]
    if repair_succeeds:
        assert result["status"] == "ready" and result["fidelity"]["recognizable"] is True
        assert [obj["id"] for obj in result["scene"]["objects"]] == ["sofa"]
        assert not result["unresolved_entities"]
    else:
        assert result["status"] == "partial" and result["scene"] is not None
        assert result["validation"]["valid"] is True
        assert result["fidelity"]["recognizable"] is False
        assert [obj["id"] for obj in result["scene"]["objects"]] == ["sofa"]
        assert not result["unresolved_entities"]
        assert any(issue["code"] == "fidelity" for issue in result["issues"])
    again = await client.post("/api/rooms/propose-scene", json={"analysis_id": analysis["analysis_id"]})
    assert again.json() == result and len(calls) == 3


def test_high_object_confidence_does_not_turn_a_wall_reference_into_a_hard_distance_constraint():
    analysis, data = facing_case()
    analysis.observations.objects[0].wall_id = "window_wall"
    scene, geometry, issues = assemble(analysis, measurements(analysis), SceneDraft.model_validate(data))
    assert scene is not None and geometry.valid and not issues
    checks = evaluate_fidelity(analysis, scene).checks
    advisory = next(check for check in checks if check.kind == "wall" and check.entity_ids == ["sofa", "window_wall"])
    assert advisory.passed is False and advisory.required is False
    facing = next(check for check in checks if check.kind == "facing")
    assert facing.required and facing.passed


def test_partial_fallback_keeps_valid_geometry_with_both_omissions_and_a_facing_mismatch():
    from roometry.vision.partial import assemble_partial

    analysis, data = facing_case()
    # A bad accessory must not erase a retained sofa merely because its facing
    # also needs correction. This is the failed-partial path in the screenshot.
    accessory = analysis.observations.objects[0].model_copy(update={"id": "plant", "type": "plant", "wall_id": None, "placement": "floor"})
    analysis.observations.objects.append(accessory)
    block_local_turn(data)
    data["objects"].append({**deepcopy(data["objects"][0]), "id": "plant", "asset_id": "plant_standard_01", "position": {"x": -10., "y": 0., "z": 2.}})
    original = analysis.model_dump()
    scene, geometry, issues, unresolved = assemble_partial(analysis, measurements(analysis), SceneDraft.model_validate(data))
    assert scene is not None and geometry.valid
    assert [o.id for o in scene.objects] == ["sofa"]
    assert [item.entity_id for item in unresolved] == ["plant"]
    assert {problem.code for problem in issues} == {"geometry", "fidelity"}
    assert evaluate_fidelity(analysis, scene).recognizable is False
    assert analysis.model_dump() == original


def test_the_same_repair_gets_geometry_and_fidelity_feedback():
    analysis, data = facing_case()
    # All objects fit, but this opening exceeds the wall, while the sofa also
    # faces away. Both errors must be visible before spending the one repair.
    data["objects"][0]["rotation_y_rad"] = 0.
    data["openings"][0]["offset_m"] = 3.5
    scene, geometry, issues = assemble(analysis, measurements(analysis), SceneDraft.model_validate(data))
    assert scene is None and not geometry.valid
    assert {problem.code for problem in issues} == {"geometry", "fidelity"}


@pytest.mark.anyio
async def test_graph_exposes_a_provisional_scene_instead_of_stopping_empty(harness):
    _app, client, calls, actions = harness
    runs, owner, uploaded = await prepared(harness)
    example, data = facing_case()
    block_local_turn(data)

    async def photo(request, payload):
        response = test_vision_api.provider_response(payload)
        ids = json.loads(payload["input"][0]["content"][0]["text"])["image_ids"]
        observations = example.observations.model_dump(mode="json")
        observations["image_ids"] = ids
        for kind in ("walls", "objects", "openings", "relationships"):
            for entity in observations[kind]:
                entity["source_image_ids"] = ids
        response["output"][0]["content"][0]["text"] = json.dumps(observations)
        return httpx.Response(200, json=response)

    actions.extend([decision("analyze_photos"), photo, decision("propose_scene"), scene_action(data), scene_action(data), decision("finish")])
    initial = await runs.create(uploaded["input_id"], owner)
    result = await runs.execute(initial.run_id, owner)
    assert result.status == "partial" and result.last_error is None
    assert result.working_scene is not None and result.base_scene is not None
    assert [obj.id for obj in result.working_scene.objects] == ["sofa"]
    assert result.proposal.validation.valid and result.proposal.fidelity.recognizable is False
    assert not result.proposal.unresolved_entities
    assert result.usage.model_calls == len(calls) == 6
    received = await client.get(f"/api/agent-runs/{result.run_id}")
    assert received.status_code == 200 and received.json()["working_scene"] is not None
    assert received.json()["status"] == "partial" and len(calls) == 6


def test_an_empty_fidelity_failure_is_not_mislabeled_as_a_renderable_partial():
    from roometry.vision.partial import assemble_partial
    from roometry.vision.schemas import WallObservation, SpatialRelationship

    analysis, data = facing_case()
    analysis.observations.objects = []
    analysis.observations.openings = []
    data["objects"] = data["openings"] = []
    wall = analysis.observations.walls[0]
    analysis.observations.walls.append(wall.model_copy(update={"id": "opposite_wall"}))
    analysis.observations.relationships = [SpatialRelationship(
        id="wall_pair", kind="opposite_wall", subject_id="window_wall", target_id="opposite_wall",
        confidence="high", source_image_ids=wall.source_image_ids, evidence="The two walls occupy opposite room ends.",
    )]
    data["wall_assignments"].append({"observation_id": "opposite_wall", "wall": "east"})
    scene, geometry, issues, unresolved = assemble_partial(analysis, measurements(analysis), SceneDraft.model_validate(data))
    assert scene is None and geometry.valid and not unresolved
    assert issues and issues[0].code == "fidelity"
