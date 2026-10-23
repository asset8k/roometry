"""Review omissions and targeted placement/material repairs, without paid calls."""

import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from roometry.vision.proposal_schemas import SceneDraft
from roometry.vision.proposals import assemble
from roometry.vision.review import validate_judgment
from roometry.vision.review_policy import finding_progress, review_targets, select_findings
from roometry.vision.review_schemas import ReviewJudgment
from tests import test_vision_api as api
from tests.test_agent_tools import decision
from tests.test_scene_proposals import draft_data, measurements, scene_action, source
from tests.test_visual_review import consistent, construction, enabled, review_action, run, with_checks

anyio_backend = api.anyio_backend
isolated_environment = api.isolated_environment
harness = api.harness
pytestmark = pytest.mark.anyio


def finding(kind, entity, *, target=None, appearance=None, repairable=True):
    return {"kind":kind, "entity_ids":[entity] if entity is not None else [], "confidence":"high", "severity":"major",
            "source_image_ids":source().observations.image_ids[:1], "view_ids":["top","cutaway_se"],
            "description":"The workstation is farther from the window or loses its visible upholstery color.",
            "correction":"Restore the photo-supported local arrangement or rendered upholstery color.",
            "placement_target":target, "appearance_target":appearance, "repairable":repairable}


def quality_review(payload):
    context=json.loads(payload["input"][0]["content"][0]["text"])
    data={"verdict":"needs_correction", "summary":"Placement and upholstery need correction.", "limitations":[], "findings":[
        finding("position","desk_observed",target={"relation":"near","target_id":"window_observed"}),
        finding("color","chair_a",appearance={"field":"color","value":"#28558A"}),
    ]}
    for item in data["findings"]:
        item["source_image_ids"]=context["observations"]["image_ids"][:1]
    return data


def far_desk():
    data=draft_data()
    for item in data["objects"][1:3]:
        item["position"]["z"]=1.3
    return data


@pytest.mark.parametrize("defect", [None,"wrong_color","wrong_direction","insufficient_progress","resize_instead","reference_moved"])
async def test_targeted_review_moves_support_group_and_restores_color_or_keeps_prior_scene(harness,defect):
    app,_,calls,actions=harness
    runs,owner,uploaded=await enabled(harness)
    construction(actions,far_desk())
    async def repair(request,payload):
        context=json.loads(payload["input"][0]["content"][0]["text"])
        assert context["selected_findings"]==[0,1]
        assert context["repair_objectives"][0]["placement_target"]["target_id"]=="window_observed"
        assert "color" in context["appearance_controls"]["chair_a"]
        items=deepcopy(draft_data()["objects"][1:3])
        for obj in items:
            obj["asset_id"]=None
            obj["dimensions_m"]=None
            if defect=="wrong_direction": obj["position"]["z"]=1.4
            if defect=="insufficient_progress": obj["position"]["z"]=1.25
        old=next(obj for obj in context["observations"]["objects"] if obj["id"]=="chair_a")
        patch={"wall_assignments":[],"openings":[],"objects":items,"assumptions":[],"warnings":[],"appearance_corrections":[{
            "entity_id":"chair_a","field":"color","previous_value":old["color"],
            "revised_value":"#FFFF00" if defect=="wrong_color" else "#28558A",
            "reason":"The original photo shows blue upholstery, independently of the black frame.","source_image_ids":old["source_image_ids"],
        }]}
        if defect=="resize_instead":
            patch["objects"]=[]
            resized=deepcopy(far_desk()["objects"][1]);resized["dimensions_m"]["depth"]=1.0
            patch["objects"].append(resized)
        if defect=="reference_moved":
            patch["openings"]=[{**draft_data()["openings"][0],"offset_m":2.1}]
        return await scene_action(patch)(request,payload)
    actions.extend([review_action(quality_review),repair,decision("finish")])
    result=await run(runs,owner,uploaded)
    review=result.proposal.visual_review
    # Direction/size/reference regressions must never be accepted merely for
    # recoloring the chair. A small incomplete move can accompany a real color
    # improvement, but cannot be advertised as fixing the placement finding.
    accepted=defect in {None,"insufficient_progress"}
    assert review.repair in ({"accepted"} if accepted else {"rejected", "failed"})
    assert review.applied_findings==([0,1] if defect is None else [1] if accepted else [])
    objects={obj.id:obj for obj in result.base_scene.objects}
    if defect is None:
        assert objects["desk_observed"].position.z==objects["computer_observed"].position.z==.6
        assert objects["chair_a"].color=="#28558A"
    elif not accepted:
        assert objects["desk_observed"].position.z==1.3
        assert objects["chair_a"].color!="#28558A"
    assert result.status=="partial" and result.proposal.validation.valid
    assert result.analysis==app.state.analysis_service._states[uploaded["input_id"]].result
    assert result.base_scene.room.width_m==result.base_scene.room.length_m==4
    assert len(calls)==7 and sum(entry.operation=="analysis" for entry in app.state.analysis_service.analyzer.usage_records)==1


@pytest.mark.parametrize("defect", ["skipped","duplicate","hidden_difference","unrelated_photo","not_applicable","unused_material","no_target"])
async def test_incomplete_or_unsupported_review_never_authorizes_paid_repair(harness,defect):
    _,_,calls,actions=harness
    runs,owner,uploaded=await enabled(harness)
    construction(actions)
    def invalid(payload):
        context=json.loads(payload["input"][0]["content"][0]["text"])
        data=with_checks(quality_review(payload) if defect in {"unused_material","no_target"} else consistent(),context)
        if defect=="skipped": data["checks"].pop(0)
        elif defect=="duplicate": data["checks"].append(deepcopy(data["checks"][0]))
        elif defect=="hidden_difference": data["checks"][0]["placement"]="difference"
        elif defect=="unrelated_photo": data["checks"][0]["source_image_ids"]=["foreign_photo"]
        elif defect=="not_applicable": data["checks"][0]["placement"]="not_applicable"
        elif defect=="unused_material": data["findings"][1]["appearance_target"]["field"]="enamel"
        else: data["findings"][0]["placement_target"]=None
        return data
    actions.extend([review_action(invalid),decision("finish")])
    result=await run(runs,owner,uploaded)
    assert result.proposal.visual_review.status=="unavailable"
    assert result.proposal.validation.valid and len(result.base_scene.objects)==5
    assert len(calls)==6


async def test_uncertain_placement_keeps_a_provisional_scene_without_manufacturing_a_repair(harness):
    _,_,calls,actions=harness
    runs,owner,uploaded=await enabled(harness)
    construction(actions)
    def uncertain(payload):
        context=json.loads(payload["input"][0]["content"][0]["text"])
        data=with_checks(consistent(),context)
        data.update(verdict="uncertain",limitations=["The desk's distance from the window is occluded."])
        next(item for item in data["checks"] if item["entity_id"]=="desk_observed")["placement"]="uncertain"
        return data
    actions.extend([review_action(uncertain),decision("finish")])
    result=await run(runs,owner,uploaded)
    assert result.status=="partial" and result.proposal.visual_review.repair=="not_attempted"
    assert len(calls)==6


def test_minor_missing_accessory_does_not_spend_the_only_visual_repair():
    analysis=source()
    accessory=analysis.observations.objects[2]
    assert accessory.type=="computer"
    absent=finding("missing",accessory.id)
    absent["severity"]="minor"
    review=ReviewJudgment.model_validate({"verdict":"needs_correction","summary":"An accessory is absent.","limitations":[],"findings":[absent]})
    assert select_findings(analysis,review)==[]
    review.findings.insert(0,review.findings[0].model_copy(update={"kind":"position","entity_ids":["desk_observed"]}))
    assert [index for index,_ in select_findings(analysis,review)]==[0]


def test_review_targets_disclose_distance_and_only_actual_catalog_appearance_controls():
    analysis=source()
    scene,_,_=assemble(analysis,measurements(analysis),SceneDraft.model_validate(far_desk()))
    targets,truncated=review_targets(analysis,scene)
    desk=next(target for target in targets if target["entity_id"]=="desk_observed")
    assert desk["opening_gaps_m"]["window_observed"]>.8
    assert "color" in desk["appearance_controls"] and "enamel" not in desk["appearance_controls"]
    assert not truncated and targets[-1]["entity_id"] is None
    assert {"chair_a","chair_b","bed_observed","desk_observed","window_observed","door_observed"} <= {target["entity_id"] for target in targets}


def test_color_targets_need_valid_values_and_rendered_materials():
    with pytest.raises(ValidationError):
        ReviewJudgment.model_validate({"verdict":"needs_correction","summary":"Wrong color.","findings":[finding("color","chair_a",appearance={"field":"color","value":"blue"})],"limitations":[]})


def test_review_target_cannot_turn_a_color_finding_into_an_unrelated_material_edit():
    from roometry.vision.review import draft_from_scene, patch_matches_findings
    analysis=source()
    scene,_,_=assemble(analysis,measurements(analysis),SceneDraft.model_validate(draft_data()))
    before=draft_from_scene(scene)
    after=before.model_copy(deep=True)
    from roometry.scenes.schemas import AppearanceCorrection
    after.appearance_corrections.append(AppearanceCorrection(entity_id="chair_a",field="wood",previous_value=None,
        revised_value="#FF00FF",reason="An unrelated frame repaint.",source_image_ids=analysis.observations.image_ids[:1]))
    judgment=ReviewJudgment.model_validate({"verdict":"needs_correction","summary":"Upholstery differs.","limitations":[],
        "findings":[finding("color","chair_a",appearance={"field":"color","value":"#28558A"})]})
    assert not patch_matches_findings(before,after,judgment.findings)


def test_review_objectives_require_a_distinct_real_reference():
    analysis=source()
    scene,_,_=assemble(analysis,measurements(analysis),SceneDraft.model_validate(draft_data()))
    targets,_=review_targets(analysis,scene)
    context={"observations":analysis.observations.model_dump(mode="json"),"review_targets":targets}
    data=with_checks({"verdict":"needs_correction","summary":"Placement differs.","limitations":[],
        "findings":[finding("position","desk_observed",target={"relation":"near","target_id":"desk_observed"})]},context)
    with pytest.raises(ValueError,match="distinct reference"):
        validate_judgment(analysis,ReviewJudgment.model_validate(data),scene)


def test_facing_target_checks_actual_direction_not_just_any_rotation():
    analysis=source()
    scene,_,_=assemble(analysis,measurements(analysis),SceneDraft.model_validate(draft_data()))
    judgment=ReviewJudgment.model_validate({"verdict":"needs_correction","summary":"Facing differs.","limitations":[],
        "findings":[finding("orientation","bed_observed",target={"relation":"faces","target_id":"window_observed"})]})
    candidate=scene.model_copy(deep=True)
    candidate.objects[0].rotation_y_rad=.2
    assert finding_progress(scene,candidate,judgment.findings[0])[0] is False
    candidate.objects[0].rotation_y_rad=3.141592653589793
    assert finding_progress(scene,candidate,judgment.findings[0])==(True,False)


def test_large_rooms_keep_review_and_repair_work_bounded_and_disclose_unchecked_scope():
    analysis=source()
    scene,_,_=assemble(analysis,measurements(analysis),SceneDraft.model_validate(draft_data()))
    chair=analysis.observations.objects[-1]
    analysis.observations.objects.extend(chair.model_copy(update={"id":f"extra_chair_{index}"}) for index in range(30))
    targets,truncated=review_targets(analysis,scene)
    assert truncated and len(targets)==25
    assert len({target["entity_id"] for target in targets})==25
    data={"verdict":"needs_correction","summary":"Multiple positions differ.","limitations":[],"findings":[
        finding("position",f"extra_chair_{index}",target={"relation":"near","target_id":"window_observed"}) for index in range(10)]}
    assert len(select_findings(analysis,ReviewJudgment.model_validate(data)))==6


def test_new_review_schema_requires_the_complete_bounded_checklist():
    from roometry.config import Settings
    from roometry.vision.review_request import build_review_request
    from roometry.vision.review_render import render_views
    from roometry.vision.proposal_schemas import SceneProposalResponse
    from roometry.vision.schemas import ReconstructionConstraints
    from pathlib import Path
    proposal=SceneProposalResponse.model_validate_json((Path(__file__).parents[1]/"data/fixtures/room_scene_proposal.json").read_text())
    analysis=source()
    payload=build_review_request(Settings(_env_file=None),analysis,proposal,ReconstructionConstraints(),
        [b"mock-normalized-jpeg"]*len(analysis.observations.image_ids),render_views(proposal.scene))
    schema=payload["text"]["format"]["schema"]
    targets,_=review_targets(analysis,proposal.scene)
    assert schema["properties"]["checks"]["minItems"]==schema["properties"]["checks"]["maxItems"]==len(targets)
    entity_schema=schema["$defs"]["ReviewCheck"]["properties"]["entity_id"]
    assert entity_schema["anyOf"][1]=={"type":"null"}
    for item in [schema,*schema["$defs"].values()]:
        if item.get("type")=="object":
            assert set(item["required"])==set(item["properties"])
            assert not item["additionalProperties"]
    assert payload["model"]=="gpt-6-luna"


async def test_limited_review_keeps_scope_warning_even_when_model_fills_all_limitations(harness,monkeypatch):
    observations=deepcopy(api.SOURCE)
    chair=observations["objects"][-1]
    observations["objects"].extend({**deepcopy(chair),"id":f"extra_chair_{index}"} for index in range(30))
    monkeypatch.setattr(api,"SOURCE",observations)
    _,_,calls,actions=harness
    runs,owner,uploaded=await enabled(harness)
    construction(actions)
    actions.append(scene_action({"wall_assignments":[],"openings":[],"objects":[],"assumptions":[],"warnings":[]}))
    def limited(payload):
        context=json.loads(payload["input"][0]["content"][0]["text"])
        assert context["review_scope_limited"]
        data=with_checks(consistent(),context)
        data.update(verdict="uncertain",limitations=[f"Other uncertainty {index}." for index in range(10)])
        missing={target["entity_id"] for target in context["review_targets"] if not target.get("retained",True)}
        for check in data["checks"]:
            if check["entity_id"] in missing:
                check["placement"]="uncertain"
        return data
    actions.extend([review_action(limited),decision("finish")])
    result=await run(runs,owner,uploaded)
    review=result.proposal.visual_review
    assert review.status=="reviewed" and review.repair=="not_attempted" and result.status=="partial"
    assert len(review.judgment.checks)==25 and len(review.judgment.limitations)==10
    assert "did not check every major item" in review.judgment.limitations[0]
    assert len(calls)==7
