"""Real graph/SDK with mocked review images and outcomes; no paid requests."""

import json
from copy import deepcopy
from io import BytesIO

import httpx
import pytest
from PIL import Image

from roometry.scenes.schemas import RoomScene
from roometry.scenes.validation import validate_geometry
from roometry.vision.proposal_schemas import SceneDraft
from roometry.vision.review import VisualReviewer, candidate_allowed, draft_from_scene, patch_matches_findings, reconstruction_changed
from roometry.vision.review_render import VIEW_IDS, material_color, render_views
from roometry.vision.review_schemas import ReviewJudgment
from roometry.vision.schemas import ReconstructionConstraints
from tests import test_vision_api as api
from tests.test_agent_tools import decision, photo, prepared
from tests.test_scene_proposals import draft_data, scene_action

anyio_backend = api.anyio_backend
isolated_environment = api.isolated_environment
harness = api.harness
pytestmark = pytest.mark.anyio


def consistent():
    return {"verdict":"consistent", "summary":"The major arrangement agrees with the supplied evidence.", "findings":[], "limitations":["Fine texture and exact measurements remain uncertain."]}


def discrepancy(payload, *, kind="orientation", ids=None):
    context = json.loads(payload["input"][0]["content"][0]["text"])
    return {"verdict":"needs_correction", "summary":"A major arrangement difference needs correction.",
        "findings":[{"kind":kind, "entity_ids":ids if ids is not None else ["bed_observed"], "confidence":"high", "severity":"major",
            "source_image_ids":[context["observations"]["image_ids"][0]], "view_ids":["top","cutaway_se"],
            "description":"The object's functional front differs from the original photo.", "correction":"Preserve its location and turn its functional front to face north.",
            "placement_target":{"relation":"faces", "target_id":"window_observed"} if kind == "orientation" else None,
            "appearance_target":{"field":"wall_color" if ids == [] or ids == ["window_wall"] else "color", "value":"#DDDDDD" if ids == [] or ids == ["window_wall"] else "#353535"} if kind == "color" else None,
            "repairable": True}], "limitations":[]}


def with_checks(data, context):
    """Explicit synthetic provider checks; missing/invalid-check tests bypass it."""
    data = deepcopy(data)
    if "checks" in data:
        return data
    walls = {wall["id"] for wall in context["observations"]["walls"]}
    data["checks"] = []
    for target in context["review_targets"]:
        key = target["entity_id"]
        findings = [f for f in data["findings"] if key in f["entity_ids"] or
                    (key is None and f["kind"] == "color" and (not f["entity_ids"] or set(f["entity_ids"]) <= walls))]
        data["checks"].append({"entity_id":key,
            "placement":"not_applicable" if key is None else "difference" if any(f["kind"] != "color" for f in findings) else "consistent",
            "appearance":"not_applicable" if target["kind"] == "opening" else "difference" if any(f["kind"] == "color" for f in findings) else "uncertain" if not target.get("retained", True) else "consistent",
            "evidence":"Synthetic provider comparison of placement and visible materials.", "source_image_ids":target["source_image_ids"]})
    for check in data["checks"]:
        target = next(item for item in context["review_targets"] if item["entity_id"] == check["entity_id"])
        check.setdefault("relations", [{"target_id": p["target_id"], "relation": p["relation"],
            "photo_support": "supported" if p["candidate_error"] <= p["correction_trigger"] or any(
                f.get("placement_target") == {"target_id":p["target_id"],"relation":p["relation"]} and check["entity_id"] in f["entity_ids"] for f in data["findings"]
            ) else "contradicted"} for p in target.get("relation_probes", [])])
        controls = target["appearance_controls"]
        check.setdefault("materials", [{"field": field, "photo_value": next((f["appearance_target"]["value"] for f in data["findings"]
            if f.get("appearance_target") and f["appearance_target"]["field"] == field and
            (check["entity_id"] in f["entity_ids"] if check["entity_id"] else not f["entity_ids"])),
            controls[field]["value"] if isinstance(controls[field], dict) else controls[field])}
            for field in target.get("material_probes", [])])
    return data


def review_action(judgment=None, *, failure=None):
    async def action(request, payload):
        assert payload["text"]["format"]["name"] == "room_visual_review"
        if failure == "timeout":
            raise httpx.ReadTimeout("PRIVATE_REVIEW_ERROR", request=request)
        if failure == "provider":
            return httpx.Response(500, json={"error":{"message":"PRIVATE_REVIEW_ERROR"}})
        context = json.loads(payload["input"][0]["content"][0]["text"])
        fake = {"model":payload["model"], "input":[{"content":[{"text":json.dumps({"image_ids":context["observations"]["image_ids"]})}]}]}
        response = api.provider_response(fake)
        data = with_checks(judgment(payload) if callable(judgment) else judgment or consistent(), context)
        response["output"][0]["content"][0]["text"] = json.dumps(data)
        if failure == "refusal":
            response["output"][0]["content"] = [{"type":"refusal", "refusal":"PRIVATE_REVIEW_ERROR"}]
        elif failure == "incomplete":
            response["status"] = "incomplete"
            response["incomplete_details"] = {"reason":"max_output_tokens"}
        elif failure == "schema":
            response["output"][0]["content"][0]["text"] = '{"PRIVATE_REVIEW_ERROR": true}'
        elif failure == "foreign_id":
            data = discrepancy(payload, ids=["invented_sofa"])
            response["output"][0]["content"][0]["text"] = json.dumps(data)
        elif failure == "foreign_photo":
            data = discrepancy(payload)
            data["findings"][0]["source_image_ids"] = ["foreign_photo"]
            response["output"][0]["content"][0]["text"] = json.dumps(data)
        return httpx.Response(200, json=response)
    return action


async def enabled(harness):
    app, _, _, _ = harness
    app.state.settings.visual_review_enabled = True
    return await prepared(harness)


def construction(actions, data=None):
    actions.extend([decision("analyze_photos"), photo, decision("propose_scene"), scene_action(data)])


async def run(runs, owner, uploaded):
    state = await runs.create(uploaded["input_id"], owner)
    return await runs.execute(state.run_id, owner)


async def test_full_graph_compares_original_photos_three_views_and_scene_with_one_luna_review(harness):
    app, _, calls, actions = harness
    runs, owner, uploaded = await enabled(harness)
    construction(actions)
    actions.extend([review_action(), decision("finish")])
    result = await run(runs, owner, uploaded)
    assert result.status == "completed" and result.proposal.visual_review.repair == "not_needed"
    review = next(call for call in calls if call.get("text",{}).get("format",{}).get("name") == "room_visual_review")
    images = [part["image_url"] for part in review["input"][0]["content"] if part["type"] == "input_image"]
    assert sum(image.startswith("data:image/jpeg;") for image in images) == len(uploaded["images"])
    assert sum(image.startswith("data:image/png;") for image in images) == 3
    assert review["model"] == "gpt-6-luna" and review["reasoning"]["effort"] == "low"
    assert not review["store"] and review["tools"] == [] and review["truncation"] == "disabled"
    assert "data:image" not in result.model_dump_json()
    ledger = app.state.analysis_service.analyzer.usage_records
    assert [entry.operation for entry in ledger] == ["agent_decision","analysis","agent_decision","scene_proposal","visual_review","agent_decision"]
    assert result.usage.model_calls == 6 and all(entry.run_id == result.run_id for entry in ledger)
    assert (await runs.execute(result.run_id, owner)) == result and len(calls) == 6
    # A repeated service invocation reuses the owned result too.
    original = result.proposal.model_copy(deep=True)
    original.visual_review = None
    repeated = await VisualReviewer(app.state.analysis_service).review(original, owner, ReconstructionConstraints(), timeout_seconds=90)
    assert repeated == result.proposal and len(calls) == 6
    different = original.model_copy(deep=True)
    different.scene.objects[0].rotation_y_rad = 3.141592653589793
    stale = await VisualReviewer(app.state.analysis_service).review(different, owner, ReconstructionConstraints(), timeout_seconds=90)
    assert stale.visual_review.status == "unavailable" and len(calls) == 6
    from roometry.vision.errors import AnalysisFailure
    with pytest.raises(AnalysisFailure, match="analysis_not_found"):
        await VisualReviewer(app.state.analysis_service).review(original, "another-owner", ReconstructionConstraints(), timeout_seconds=90)
    assert len(calls) == 6


async def test_one_targeted_visual_correction_updates_base_and_keeps_geometry_and_photo_analysis(harness):
    app, _, calls, actions = harness
    runs, owner, uploaded = await enabled(harness)
    construction(actions)
    fixed = deepcopy(draft_data()["objects"][0])
    fixed["rotation_y_rad"] = 3.141592653589793
    patch = {"wall_assignments":[], "openings":[], "objects":[fixed], "assumptions":[], "warnings":[]}
    actions.extend([review_action(discrepancy), scene_action(patch), decision("finish")])
    result = await run(runs, owner, uploaded)
    assert result.status == "partial" and result.proposal.visual_review.repair == "accepted"
    assert result.base_scene == result.working_scene == result.proposal.scene
    assert result.base_scene.objects[0].rotation_y_rad == pytest.approx(3.141592653589793)
    assert result.proposal.validation.valid and validate_geometry(result.base_scene).valid
    assert not result.proposal.visual_review.revised_scene_visually_reviewed
    assert result.proposal.visual_review.reviewed_scene_hash != result.proposal.visual_review.result_scene_hash
    assert result.base_scene.room.width_m == 4 and result.base_scene.room.length_m == 4 and result.base_scene.room.height_m == 2.6
    ledger = app.state.analysis_service.analyzer.usage_records
    assert sum(entry.operation == "scene_proposal" for entry in ledger) == 1
    assert sum(entry.operation == "visual_review" for entry in ledger) == 1
    assert sum(entry.operation == "visual_repair" for entry in ledger) == 1
    assert sum(entry.operation == "analysis" for entry in ledger) == 1
    assert calls[-2]["model"] == "gpt-6-luna" and result.usage.model_calls == 7
    assert (await runs.execute(result.run_id, owner)) == result and len(calls) == 7


@pytest.mark.parametrize("failure", ["timeout","provider","refusal","incomplete","schema","foreign_id","foreign_photo"])
async def test_optional_review_failure_keeps_valid_room_without_retries_or_private_diagnostics(harness, failure):
    app, _, calls, actions = harness
    runs, owner, uploaded = await enabled(harness)
    construction(actions)
    actions.extend([review_action(failure=failure), decision("finish")])
    result = await run(runs, owner, uploaded)
    assert result.status == "completed" and result.proposal.visual_review.status == "unavailable"
    assert len(result.base_scene.objects) == 5 and result.proposal.validation.valid
    assert "PRIVATE" not in result.model_dump_json()
    assert len(calls) == 6 and not app.state.analysis_service._states[uploaded["input_id"]].visual_repair_attempted


@pytest.mark.parametrize("failure", ["collision","unaffected","invented","delete","timeout","refusal"])
async def test_failed_or_unsafe_visual_correction_keeps_previous_valid_base(harness, failure):
    _, _, calls, actions = harness
    runs, owner, uploaded = await enabled(harness)
    construction(actions)
    data = deepcopy(draft_data()["objects"][0])
    if failure == "collision":
        data["position"] = deepcopy(draft_data()["objects"][1]["position"])
    elif failure == "unaffected":
        data = deepcopy(draft_data()["objects"][1])
        data["position"]["x"] = 2.5
    elif failure == "invented":
        data["id"] = "invented_bed"
    patch = {"wall_assignments":[], "openings":[], "objects":[] if failure == "delete" else [data], "assumptions":[], "warnings":[]}
    async def failed(request,payload):
        if failure == "timeout":
            raise httpx.ReadTimeout("PRIVATE_REPAIR_ERROR",request=request)
        response = (await scene_action(patch)(request,payload)).json()
        response["output"][0]["content"] = [{"type":"refusal","refusal":"PRIVATE_REPAIR_ERROR"}]
        return httpx.Response(200,json=response)
    actions.extend([review_action(discrepancy), failed if failure in {"timeout","refusal"} else scene_action(patch), decision("finish")])
    result = await run(runs, owner, uploaded)
    assert result.status == "partial" and result.proposal.visual_review.repair in {"rejected","failed"}
    assert result.base_scene.objects[0].position.x == .8 and result.base_scene.objects[0].rotation_y_rad == 0
    assert len(result.base_scene.objects) == 5 and result.proposal.validation.valid
    assert "PRIVATE" not in result.model_dump_json() and len(calls) == 7


async def test_model_limit_stops_visual_repair_and_keeps_reviewed_partial(harness):
    from roometry.agents.state import RunLimits
    _, _, calls, actions = harness
    runs, owner, uploaded = await enabled(harness)
    construction(actions)
    actions.append(review_action(discrepancy))
    initial = await runs.create(uploaded["input_id"], owner, limits=RunLimits(max_model_calls=5,step_timeout_seconds=185))
    result = await runs.execute(initial.run_id, owner)
    assert result.status == "partial" and result.stop_reason == "budget_limit"
    assert result.proposal.visual_review.repair == "failed"
    assert len(calls) == result.usage.model_calls == 5 and result.proposal.validation.valid


async def test_empty_visual_patch_cannot_claim_correction_by_reordering_entities(harness):
    _, _, calls, actions = harness
    runs, owner, uploaded = await enabled(harness)
    data = draft_data()
    data["objects"].reverse()
    construction(actions, data)
    actions.extend([review_action(discrepancy), scene_action({
        "wall_assignments": [], "openings": [], "objects": [],
        "assumptions": [], "warnings": ["No compatible correction was available."],
    }), decision("finish")])
    result = await run(runs, owner, uploaded)
    review = result.proposal.visual_review
    assert review.repair == "rejected" and result.status == "partial"
    assert review.reviewed_scene_hash == review.result_scene_hash
    assert "One correction was applied" not in review.message
    assert len(calls) == 7


def test_reconstruction_change_ignores_order_and_roundoff_but_detects_real_edits():
    from tests.test_scene_proposals import measurements, source
    from roometry.vision.proposals import assemble
    scene, _, _ = assemble(source(), measurements(), SceneDraft.model_validate(draft_data()))
    candidate = scene.model_copy(deep=True)
    candidate.objects.reverse()
    candidate.openings.reverse()
    candidate.wall_mapping.reverse()
    candidate.objects[0].position.x += 3e-17
    candidate.warnings.append("Only audit wording changed.")
    assert not reconstruction_changed(scene, candidate)
    candidate.objects[0].position.x += .01
    assert reconstruction_changed(scene, candidate)
    candidate = scene.model_copy(deep=True)
    candidate.objects[0].color = "#28558A"
    assert reconstruction_changed(scene, candidate)


async def test_unobserved_missing_item_is_reported_without_inventing_an_object_or_spending_repair(harness):
    _, _, calls, actions = harness
    runs, owner, uploaded = await enabled(harness)
    construction(actions)
    actions.extend([review_action(lambda payload: discrepancy(payload, kind="missing", ids=[])), decision("finish")])
    result = await run(runs, owner, uploaded)
    assert result.status == "partial" and result.proposal.visual_review.repair == "not_attempted"
    assert len(result.base_scene.objects) == 5 and len(calls) == 6


async def test_appearance_revision_is_audited_and_source_observations_remain_immutable(harness):
    app, _, _, actions = harness
    runs, owner, uploaded = await enabled(harness)
    construction(actions)
    async def repair(request,payload):
        context = json.loads(payload["input"][0]["content"][0]["text"])
        obj = context["observations"]["objects"][0]
        patch = {"wall_assignments":[],"openings":[],"objects":[],"assumptions":[],"warnings":[],"appearance_corrections":[{
                "entity_id":obj["id"],"field":"color","previous_value":obj.get("color"),"revised_value":"#353535",
            "reason":"The visible upholstery is dark charcoal, rather than the initial light estimate.","source_image_ids":obj["source_image_ids"],
        }]}
        return await scene_action(patch)(request,payload)
    actions.extend([review_action(lambda payload: discrepancy(payload, kind="color")),repair,decision("finish")])
    result = await run(runs, owner, uploaded)
    assert result.proposal.visual_review.repair == "accepted"
    assert result.base_scene.objects[0].color == "#353535"
    assert result.base_scene.appearance_corrections[0].field == "color"
    assert result.analysis.observations.objects[0].color != "#353535"
    assert app.state.analysis_service._states[uploaded["input_id"]].result == result.analysis


async def test_diagnostic_views_are_valid_pngs_and_track_real_pose_and_palette():
    from tests.test_scene_proposals import source,measurements
    from roometry.vision.proposals import assemble
    scene,_,_ = assemble(source(),measurements(),SceneDraft.model_validate(draft_data()))
    before = render_views(scene)
    assert tuple(view.view_id for view in before) == VIEW_IDS
    for view in before:
        with Image.open(BytesIO(view.image)) as image:
            assert image.size == (768,768) and image.format == "PNG"
    scene.objects[0].rotation_y_rad = 3.141592653589793
    assert render_views(scene)[-1].image != before[-1].image
    scene.objects[0].material_colors = None
    assert material_color(scene.objects[0],"wood") == "#70543D"


async def test_candidate_guard_protects_locks_measurements_and_retained_inventory():
    from tests.test_scene_proposals import source,measurements
    from roometry.vision.proposals import assemble
    analysis = source()
    scene,_,_ = assemble(analysis,measurements(),SceneDraft.model_validate(draft_data()))
    for defect in ("locked","measured","deleted","room"):
        original,candidate = scene.model_copy(deep=True),scene.model_copy(deep=True)
        if defect == "locked":
            original.objects[0].locked = candidate.objects[0].locked = True
            candidate.objects[0].rotation_y_rad = 3.141592653589793
        elif defect == "measured":
            original.objects[0].measurements_source = "user_confirmed"
            candidate.objects[0].dimensions_m.width = 1.3
        elif defect == "deleted":
            candidate.objects.pop(0)
        else:
            candidate.room.width_m = 5
        assert not candidate_allowed(analysis,original,candidate)


async def test_color_review_does_not_authorize_pose_changes():
    from tests.test_scene_proposals import source,measurements
    from roometry.vision.proposals import assemble
    analysis = source()
    scene,_,_ = assemble(analysis,measurements(),SceneDraft.model_validate(draft_data()))
    previous = draft_from_scene(scene)
    changed = previous.model_copy(deep=True)
    changed.objects[0].position.x = 1
    fake = {"input":[{"content":[{"text":json.dumps({"observations":analysis.observations.model_dump(mode="json")})}]}]}
    findings = ReviewJudgment.model_validate(discrepancy(fake,kind="color")).findings
    assert not patch_matches_findings(previous,changed,findings)


@pytest.mark.parametrize("lock_target", ["bed", "desk"])
async def test_visual_correction_preserves_unaffected_locks_and_rejects_locked_target(harness,monkeypatch,lock_target):
    app,_,_,actions=harness
    runs,owner,uploaded=await enabled(harness)
    service=app.state.analysis_service
    propose=service.propose
    async def with_lock(*args,**kwargs):
        result=await propose(*args,**kwargs)
        result.scene.objects[0 if lock_target=="bed" else 1].locked=True
        return result
    monkeypatch.setattr(service,"propose",with_lock)
    construction(actions)
    fixed=deepcopy(draft_data()["objects"][0])
    fixed["rotation_y_rad"]=3.141592653589793
    patch={"wall_assignments":[],"openings":[],"objects":[fixed],"assumptions":[],"warnings":[]}
    actions.extend([review_action(discrepancy),scene_action(patch),decision("finish")])
    result=await run(runs,owner,uploaded)
    assert result.base_scene.objects[0 if lock_target=="bed" else 1].locked
    assert result.proposal.visual_review.repair==("accepted" if lock_target=="desk" else "rejected")


async def test_review_can_restore_catalog_misinterpreted_wardrobe_after_construction_repair_was_spent(harness,monkeypatch):
    _,_,calls,actions=harness
    observations=deepcopy(api.SOURCE)
    wardrobe=observations["objects"][0]
    wardrobe.update(type="wardrobe",placement="wall",appears_fixed=True,estimated_dimensions_m=None)
    monkeypatch.setattr(api,"SOURCE",observations)
    runs,owner,uploaded=await enabled(harness)
    data=draft_data()
    missing=data["objects"].pop(0)
    construction(actions,data)
    noop={"wall_assignments":[],"openings":[],"objects":[],"assumptions":[],"warnings":[]}
    actions.append(scene_action(noop)) # Construction's original Luna allowance.
    async def restore(request,payload):
        context=json.loads(payload["input"][0]["content"][0]["text"])
        source=context["observations"]["objects"][0]
        item={**missing,"placement":"floor","dimensions_m":None}
        patch={**noop,"objects":[item],"interpretation_corrections":[{
            "entity_id":source["id"],"field":"placement","previous_value":"wall","revised_value":"floor",
            "reason":"Its feet touch the floor; the cabinet is fixed in place rather than suspended from the wall.","source_image_ids":source["source_image_ids"],
        }]}
        return await scene_action(patch)(request,payload)
    actions.extend([review_action(lambda payload:discrepancy(payload,kind="missing")),restore,decision("finish")])
    result=await run(runs,owner,uploaded)
    assert result.proposal.visual_review.repair=="accepted"
    assert len(result.base_scene.objects)==5 and not result.proposal.unresolved_entities
    restored=next(obj for obj in result.base_scene.objects if obj.id=="bed_observed")
    assert restored.type=="wardrobe" and restored.placement=="floor" and restored.fixed
    assert sum(call["model"]=="gpt-6.1-sol" for call in calls)==1 and len(calls)==8


@pytest.mark.parametrize("wall_ids", [[],["window_wall"]])
async def test_room_finish_revision_cannot_move_unaffected_furniture(harness,wall_ids):
    _,_,_,actions=harness
    runs,owner,uploaded=await enabled(harness)
    construction(actions)
    async def recolor(request,payload):
        context=json.loads(payload["input"][0]["content"][0]["text"])
        old=context["observations"].get("finishes")
        patch={"wall_assignments":[],"openings":[],"objects":[],"assumptions":[],"warnings":[],"appearance_corrections":[{
            "entity_id":None,"field":"wall_color","previous_value":old["finishes"]["wall_color"] if old else None,
            "revised_value":"#DDDDDD","reason":"The walls have a neutral light gray paint in the original views.","source_image_ids":context["observations"]["image_ids"],
        }]}
        return await scene_action(patch)(request,payload)
    actions.extend([review_action(lambda payload:discrepancy(payload,kind="color",ids=wall_ids)),recolor,decision("finish")])
    result=await run(runs,owner,uploaded)
    assert result.proposal.visual_review.repair=="accepted"
    assert result.base_scene.finishes.wall_color=="#DDDDDD"
    assert result.base_scene.objects[0].position.x==.8


async def test_cancel_during_review_does_not_dispatch_visual_repair_or_repeat_review(harness):
    _,_,calls,actions=harness
    runs,owner,uploaded=await enabled(harness)
    initial=await runs.create(uploaded["input_id"],owner)
    construction(actions)
    async def cancel(request,payload):
        await runs.cancel(initial.run_id,owner)
        return await review_action(discrepancy)(request,payload)
    actions.append(cancel)
    result=await runs.execute(initial.run_id,owner)
    assert result.status=="cancelled" and len(calls)==5
    assert (await runs.execute(initial.run_id,owner)).status=="cancelled" and len(calls)==5


async def test_visual_request_limit_is_checked_before_paid_dispatch(harness):
    app,_,calls,actions=harness
    runs,owner,uploaded=await enabled(harness)
    async def small_limit(request,payload):
        response=await scene_action()(request,payload)
        app.state.settings.analysis_max_request_bytes=1024
        return response
    actions.extend([decision("analyze_photos"),photo,decision("propose_scene"),small_limit,decision("finish")])
    result=await run(runs,owner,uploaded)
    assert result.status=="completed" and result.proposal.visual_review.status=="unavailable"
    assert len(calls)==5 and all(entry.operation!="visual_review" for entry in app.state.analysis_service.analyzer.usage_records)


async def test_visual_result_cannot_be_attached_to_a_different_scene_revision():
    from pathlib import Path
    from pydantic import ValidationError
    from roometry.scenes.hashing import scene_hash
    from roometry.vision.proposal_schemas import SceneProposalResponse
    from roometry.vision.review_schemas import VisualReviewResult
    proposal=SceneProposalResponse.model_validate_json((Path(__file__).parents[1]/"data/fixtures/room_scene_proposal.json").read_text())
    digest=scene_hash(proposal.scene)
    proposal.visual_review=VisualReviewResult(status="reviewed",judgment=ReviewJudgment.model_validate(consistent()),repair="not_needed",
        message="Visual comparison completed.",reviewed_scene_hash=digest,result_scene_hash=digest)
    valid=SceneProposalResponse.model_validate(proposal.model_dump())
    changed=valid.model_dump()
    changed["scene"]["objects"][0]["position"]["x"]+=.1
    with pytest.raises(ValidationError,match="exact reconstructed scene"):
        SceneProposalResponse.model_validate(changed)
