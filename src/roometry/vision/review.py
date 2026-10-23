"""One private visual review and one validated correction per owned analysis.

Reservations precede dispatch, including ambiguous failures. Results are cached
against the exact measured scene and human answers; checkpoints contain no images.
An optional review failing never discards a usable reconstruction.
"""

import hashlib
import json
import logging
from base64 import b64encode
from functools import partial
from typing import TYPE_CHECKING

import anyio
from openai import APIError
from pydantic import ValidationError

from roometry.scenes.schemas import RoomScene
from roometry.scenes.hashing import scene_hash
from roometry.scenes.validation import validate_geometry
from roometry.vision.analyzer import response_json
from roometry.vision.errors import AnalysisFailure
from roometry.vision.evidence import apply_constraints
from roometry.vision.fidelity import evaluate_fidelity
from roometry.vision.partial import assemble_partial
from roometry.vision.proposal_request import build_proposal_request
from roometry.vision.proposal_schemas import DraftObject, DraftOpening, SceneDraft, SceneProposalResponse
from roometry.vision.proposals import Measurements, assemble, issue, notes
from roometry.vision.recovery import merge_repair
from roometry.vision.review_render import VIEW_IDS, render_views
from roometry.vision.review_request import build_review_request, enforce_review_limit
from roometry.vision.review_schemas import ReviewJudgment, VisualReviewResult
from roometry.vision.review_policy import appearance_controls, finding_progress, review_targets, same_values, select_findings
from roometry.vision.schemas import ReconstructionConstraints, RoomAnalysisResponse

if TYPE_CHECKING:
    from roometry.vision.service import AnalysisService


def draft_from_scene(scene: RoomScene) -> SceneDraft:
    return SceneDraft.model_validate({
        "wall_assignments": [item.model_dump() for item in scene.wall_mapping],
        "objects": [item.model_dump(include=set(DraftObject.model_fields)) for item in scene.objects],
        "openings": [item.model_dump(include=set(DraftOpening.model_fields)) for item in scene.openings],
        "estimate_corrections": scene.estimate_corrections,
        "interpretation_corrections": scene.interpretation_corrections,
        "appearance_corrections": scene.appearance_corrections,
        "assumptions": [], "warnings": [],
    })


def candidate_allowed(analysis: RoomAnalysisResponse, previous: RoomScene, candidate: RoomScene) -> bool:
    if previous.room != candidate.room or not validate_geometry(candidate).valid:
        return False
    old_ids = {item.id for item in [*previous.objects, *previous.openings]}
    if not old_ids <= {item.id for item in [*candidate.objects, *candidate.openings]}:
        return False  # Never improve a reconstruction by deleting valid inventory.
    new = {obj.id: obj for obj in candidate.objects}
    for obj in previous.objects:
        replacement = new.get(obj.id)
        if replacement is None or ((obj.locked or obj.provenance == "user_added") and obj != replacement):
            return False
        if obj.measurements_source == "user_confirmed" and obj.dimensions_m != replacement.dimensions_m:
            return False
    new_openings = {item.id:item for item in candidate.openings}
    if any(item.measurements_source == "user_confirmed" and item != new_openings.get(item.id) for item in previous.openings):
        return False
    before, after = evaluate_fidelity(analysis, previous), evaluate_fidelity(analysis, candidate)
    failed = {(c.kind,tuple(c.entity_ids),c.message) for c in before.checks if c.required and not c.passed}
    return after.major_object_coverage >= before.major_object_coverage and not any(
        c.required and not c.passed and (c.kind,tuple(c.entity_ids),c.message) not in failed for c in after.checks
    )


def reconstruction_changed(previous: RoomScene, candidate: RoomScene) -> bool:
    """Reordering entities or assembly roundoff is not a visual correction.

    Compare the rendered/placed content independently of audit text and list
    order. Sub-micrometer arithmetic noise cannot establish an improvement.
    """
    object_fields = {"id", "type", "asset_id", "position", "rotation_y_rad", "dimensions_m",
                     "placement", "wall", "support_object_id", "support_surface_id", "opening_id",
                     "color", "material_colors"}

    def content(scene):
        return {
            "objects": {obj.id: obj.model_dump(include=object_fields) for obj in scene.objects},
            "openings": {item.id: item.model_dump() for item in scene.openings},
            "walls": {item.observation_id: item.wall for item in scene.wall_mapping},
            "finishes": scene.finishes.model_dump() if scene.finishes else None,
        }

    return not same_values(content(previous), content(candidate))


def validate_judgment(analysis: RoomAnalysisResponse, judgment: ReviewJudgment, scene: RoomScene) -> None:
    ids = {item.id for item in [*analysis.observations.walls, *analysis.observations.objects, *analysis.observations.openings]}
    walls = {item.id for item in analysis.observations.walls}
    objects = {item.id:item for item in scene.objects}
    targets, _ = review_targets(analysis, scene)
    expected = {target["entity_id"]:target for target in targets}
    if {check.entity_id for check in judgment.checks} != set(expected):
        raise ValueError("Complete the server-selected placement and appearance checks.")
    for finding in judgment.findings:
        for values, allowed in ((finding.entity_ids, ids), (finding.source_image_ids,set(analysis.observations.image_ids)), (finding.view_ids,set(VIEW_IDS))):
            if not set(values) <= allowed or len(values) != len(set(values)):
                raise ValueError("Review findings must cite unique original evidence and known identities.")
        if finding.repairable and finding.kind in {"position", "orientation"}:
            target = finding.placement_target
            if target is None or len(finding.entity_ids) != 1 or finding.entity_ids[0] not in objects or target.target_id not in ids or target.target_id == finding.entity_ids[0]:
                raise ValueError("Placement findings require one observed subject and a distinct reference target.")
            if (finding.kind == "orientation") != (target.relation == "faces") or (target.relation == "against_wall" and target.target_id not in walls):
                raise ValueError("The placement target must match the kind of correction.")
        elif finding.placement_target is not None:
            raise ValueError("Placement targets belong only to repairable position/orientation findings.")
        if finding.repairable and finding.kind == "color":
            target = finding.appearance_target
            if target is None:
                raise ValueError("Color findings require a rendered appearance target.")
            room_field = target.field in {"floor_color", "wall_color", "floor_pattern"}
            if room_field:
                if finding.entity_ids and not set(finding.entity_ids) <= walls:
                    raise ValueError("Room appearance targets may reference only observed walls or room finishes.")
            elif not finding.entity_ids or any(key not in objects or target.field not in appearance_controls(objects[key]) for key in finding.entity_ids):
                raise ValueError("Choose a material control actually rendered by this object's catalog model.")
        elif finding.appearance_target is not None:
            raise ValueError("Appearance targets belong only to repairable color findings.")
    for check in judgment.checks:
        target = expected[check.entity_id]
        if len(set(check.source_image_ids)) != len(check.source_image_ids) or not set(check.source_image_ids) <= set(target["source_image_ids"]):
            raise ValueError("Each object check requires its own recorded photo evidence.")
        if (check.placement == "not_applicable") != (check.entity_id is None) or (check.appearance == "not_applicable" and target["kind"] != "opening"):
            raise ValueError("Major furniture needs placement and appearance checks.")
        if check.entity_id is not None and not target["retained"] and (
            check.placement == "consistent" or check.appearance == "consistent"
        ):
            raise ValueError("An omitted major item cannot be reported as a consistent reconstruction.")
        for aspect, status in (("placement", check.placement), ("appearance", check.appearance)):
            matching = [finding for finding in judgment.findings if
                ((check.entity_id in finding.entity_ids) if check.entity_id is not None else
                 (finding.kind == "color" and (not finding.entity_ids or set(finding.entity_ids) <= walls)))
                and ((finding.kind == "color") == (aspect == "appearance"))]
            if (status == "difference") != bool(matching):
                raise ValueError("Each reported difference must agree with its object check and finding.")


def patch_matches_findings(previous: SceneDraft, merged: SceneDraft, findings) -> bool:
    """A color complaint cannot authorize moving furniture or changing its size."""
    fields = {
        "missing": set(DraftObject.model_fields),
        "orientation": {"rotation_y_rad"}, "position": {"position"},
        "proportion": {"dimensions_m"}, "color": set(),
        "identity": {"asset_id", "placement", "wall", "position", "rotation_y_rad", "support_object_id", "support_surface_id", "opening_id", "dimensions_m"},
        "support": {"placement", "wall", "position", "rotation_y_rad", "support_object_id", "support_surface_id", "opening_id"},
        "opening": set(DraftOpening.model_fields),
    }
    kinds = {}
    for finding in findings:
        for key in finding.entity_ids:
            kinds.setdefault(key, set()).add(finding.kind)
    walls = {item.observation_id for item in previous.wall_assignments}
    wall_change = any(f.entity_ids and set(f.entity_ids) <= walls and f.kind in {"position","orientation","opening"} for f in findings)
    if previous.wall_assignments != merged.wall_assignments and not wall_change:
        return False
    old = {item.id: item for item in [*previous.objects, *previous.openings]}
    changed_ids = set()
    for item in [*merged.objects, *merged.openings]:
        before = old.get(item.id)
        if before != item:
            changed_ids.add(item.id)
    for item in [*merged.objects, *merged.openings]:
        before = old.get(item.id)
        if before == item:
            continue
        if before is None:
            dependency = getattr(item,"support_object_id",None) or getattr(item,"opening_id",None)
            if "missing" not in kinds.get(item.id, set()) and dependency not in changed_ids:
                return False
            continue
        permitted = set().union(*(fields[kind] for kind in kinds.get(item.id, set())))
        if wall_change:
            permitted |= {"position", "rotation_y_rad", "wall", "offset_m"}
        # Poses of dependent items may follow their reviewed support/opening.
        if getattr(item,"support_object_id",None) in changed_ids or getattr(item,"opening_id",None) in changed_ids:
            permitted |= {"position", "rotation_y_rad", "support_surface_id"}
        differences = {key for key,value in item.model_dump().items() if value != before.model_dump()[key]}
        if not differences <= permitted:
            return False
    for current,prior,allowed_kinds in (
        (merged.appearance_corrections,previous.appearance_corrections,{"color"}),
        (merged.estimate_corrections,previous.estimate_corrections,{"proportion","opening","missing","identity"}),
        (merged.interpretation_corrections,previous.interpretation_corrections,{"identity","support","missing"}),
    ):
        existing = {(record.entity_id,record.field):record for record in prior}
        for record in current:
            if record == existing.get((record.entity_id,record.field)):
                continue
            if record.entity_id is None:
                if not any(f.kind == "color" and (not f.entity_ids or set(f.entity_ids) <= walls) for f in findings):
                    return False
            elif not kinds.get(record.entity_id,set()) & allowed_kinds:
                return False
            if allowed_kinds == {"color"} and not any(
                f.kind == "color" and f.appearance_target is not None and f.appearance_target.field == record.field
                and ((record.entity_id in f.entity_ids) if record.entity_id is not None else
                     (not f.entity_ids or set(f.entity_ids) <= walls))
                for f in findings
            ):
                return False
    return True


class VisualReviewer:
    def __init__(self, analysis: "AnalysisService"):
        self.analysis = analysis

    async def review(self, proposal: SceneProposalResponse, owner: str, constraints: ReconstructionConstraints, *, timeout_seconds: float) -> SceneProposalResponse:
        assert proposal.scene is not None
        service, original = self.analysis, proposal.model_copy(deep=True)
        state = await service._owned_analysis(proposal.analysis_id, owner)
        digest = scene_hash(proposal.scene)
        key = hashlib.sha256((digest + json.dumps(constraints.model_dump(mode="json"),sort_keys=True)).encode()).hexdigest()
        if state.review_key == key and state.reviewed_proposal is not None:
            return state.reviewed_proposal.model_copy(deep=True)

        def unavailable():
            result = original.model_copy(deep=True)
            result.visual_review = VisualReviewResult(status="unavailable", judgment=None, repair="not_attempted",
                message="The visual check could not finish. The previously validated scene is retained; its photo likeness remains unverified.",
                reviewed_scene_hash=digest, result_scene_hash=digest)
            return result

        if state.review_attempted:
            return unavailable()  # Different scene or interrupted request: never replay paid work.
        try:
            service._capacity.acquire_nowait()
        except anyio.WouldBlock:
            return unavailable()
        state.review_key, state.review_attempted = key, True
        try:
            # Finish inside the graph node's deadline so optional work cannot
            # turn a usable base into a step_timeout failure.
            with anyio.fail_after(max(.001, timeout_seconds - 1)):
                result = await self._review(original, owner, constraints, state, digest)
        except (APIError, AnalysisFailure, ValidationError, ValueError, RecursionError, TimeoutError, OSError) as error:
            logging.getLogger(__name__).warning("Visual review unavailable: analysis_id=%s reason=%s", proposal.analysis_id, error.code if isinstance(error,AnalysisFailure) else type(error).__name__)
            result = unavailable()
        finally:
            service._capacity.release()
        await service._owned_analysis(proposal.analysis_id, owner)
        state.reviewed_proposal = result.model_copy(deep=True)
        logging.getLogger(__name__).info("Visual review completed: analysis_id=%s status=%s repair=%s", proposal.analysis_id, result.visual_review.status, result.visual_review.repair)
        return result

    async def _review(self, proposal, owner, constraints, state, digest):
        service, settings = self.analysis, self.analysis.settings
        analysis = apply_constraints(state.result, constraints, wall_axes=proposal.wall_axes)
        prepared = await anyio.to_thread.run_sync(service._resolve, analysis.input_id, owner)
        images = await anyio.to_thread.run_sync(service._load_images, prepared, owner)
        with anyio.fail_after(5):
            views = await anyio.to_thread.run_sync(render_views, proposal.scene, abandon_on_cancel=True)
        payload = await anyio.to_thread.run_sync(build_review_request, settings, analysis, proposal, constraints, images, views)
        await service._owned_analysis(proposal.analysis_id, owner)
        with anyio.fail_after(settings.analysis_timeout_seconds):
            response = await service.analyzer.request(payload, operation="visual_review")
        await service._owned_analysis(proposal.analysis_id, owner)
        judgment = ReviewJudgment.model_validate(response_json(response, settings.analysis_max_output_bytes))
        validate_judgment(analysis, judgment, proposal.scene)
        _, scope_limited = review_targets(analysis, proposal.scene)
        if scope_limited:
            judgment.limitations = notes(["The bounded review did not check every major item; further placement and appearance checks remain."], judgment.limitations)[:10]
            if judgment.verdict == "consistent":
                judgment.verdict = "uncertain"
        selected = select_findings(analysis, judgment)
        actionable = [finding for _,finding in selected]
        applied_findings = []
        repair = "not_needed" if judgment.verdict == "consistent" else "not_attempted"
        result = proposal.model_copy(deep=True)
        if judgment.verdict == "needs_correction" and actionable:
            problems = [issue("fidelity", f.description, *f.entity_ids) for f in actionable if f.entity_ids]
            previous = draft_from_scene(proposal.scene)
            measurements = Measurements(proposal.dimensions, [], proposal.estimated_axes)
            wall_ids = {item.id for item in analysis.observations.walls}
            room_appearance = any(f.kind == "color" and (not f.entity_ids or set(f.entity_ids) <= wall_ids) for f in actionable)
            try:
                payload = await anyio.to_thread.run_sync(partial(build_proposal_request, operation="scene_repair"), settings, analysis, measurements, previous, problems, images)
                context = json.loads(payload["input"][0]["content"][0]["text"])
                context["visual_review"] = judgment.model_dump(mode="json")
                context["selected_findings"] = [index for index,_ in selected]
                context["repair_objectives"] = [finding.model_dump(mode="json") for finding in actionable]
                context["appearance_controls"] = {obj.id:appearance_controls(obj) for obj in proposal.scene.objects}
                context["room_appearance_revision_allowed"] = room_appearance
                context["protected_object_ids"] = [obj.id for obj in proposal.scene.objects if obj.locked or obj.provenance == "user_added"]
                payload["input"][0]["content"][0]["text"] = json.dumps(context)
                # Reuse the very same bad-scene views and original photos, never
                # rendered content substituted for original photo evidence.
                for view in views:
                    payload["input"][0]["content"].extend([
                        {"type":"input_text", "text":f"Diagnostic view of the current reconstruction: {view.view_id}"},
                        {"type":"input_image", "detail":"high", "image_url":"data:image/png;base64,"+b64encode(view.image).decode("ascii")},
                    ])
                payload["instructions"] += "\nApply only repair_objectives from selected_findings, prioritizing placement and visible material colors over accessories. A targeted position/facing change must reach its placement_target without moving the reference target. A color change must use an actually rendered appearance control and approach appearance_target.value. Update dependent surface objects with their support, preserving their relative arrangements. Do not reset colors, resize furniture or move unrelated objects merely to obtain valid geometry. For object or room color/finish differences, appearance_corrections must record entity_id (null only for room finishes), field, exact ORIGINAL observation previous_value, revised_value, reason and original source_image_ids. Do not change an unaffected appearance. Do not imitate lighting or invent textures. Respect protected_object_ids; room dimensions, user answers, measured object sizes and locks cannot change. Do not redesign.\n"
                payload["model"] = settings.model_route("visual_repair").model
                enforce_review_limit(settings, payload)
                await service._owned_analysis(proposal.analysis_id, owner)
                state.visual_repair_attempted = True
                with anyio.fail_after(settings.analysis_timeout_seconds):
                    response = await service.analyzer.request(payload, operation="visual_repair")
                await service._owned_analysis(proposal.analysis_id, owner)
                patch = SceneDraft.model_validate(response_json(response, settings.analysis_max_output_bytes))
                draft = merge_repair(analysis, previous, patch, problems, allow_room_appearance=room_appearance)
                if not patch_matches_findings(previous, draft, actionable):
                    raise ValueError("The correction exceeds the visual findings.")
                candidate, report, candidate_issues = await anyio.to_thread.run_sync(assemble, analysis, measurements, draft)
                unresolved = []
                if candidate is None:
                    candidate, report, candidate_issues, unresolved = await anyio.to_thread.run_sync(assemble_partial, analysis, measurements, draft)
                if candidate is not None:
                    old_objects = {item.id:item for item in proposal.scene.objects}
                    for obj in candidate.objects:
                        old = old_objects.get(obj.id)
                        if old is not None:
                            obj.locked, obj.fixed, obj.provenance = old.locked, old.fixed or obj.fixed, old.provenance
                            if old.measurements_source == "user_confirmed":
                                obj.measurements_source = old.measurements_source
                    old_openings = {item.id:item for item in proposal.scene.openings}
                    for opening in candidate.openings:
                        old = old_openings.get(opening.id)
                        if old is not None:
                            opening.swing, opening.hinge = old.swing, old.hinge
                            opening.provenance, opening.measurements_source = old.provenance, old.measurements_source
                    report = validate_geometry(candidate)
                changed = candidate is not None and reconstruction_changed(proposal.scene, candidate)
                progress = [finding_progress(proposal.scene, candidate, finding) for finding in actionable] if candidate is not None else []
                if changed and any(addressed for addressed,_ in progress) and not any(regressed for _,regressed in progress) and candidate_allowed(analysis, proposal.scene, candidate):
                    result.scene, result.validation = candidate, report
                    result.fidelity = evaluate_fidelity(analysis, candidate)
                    result.issues, result.unresolved_entities = candidate_issues, unresolved
                    result.status = "partial" if candidate_issues or unresolved else "ready"
                    repair = "accepted"
                    applied_findings = [index for (index,_),(addressed,_) in zip(selected,progress) if addressed]
                else:
                    repair = "rejected"
            except (APIError, AnalysisFailure, ValidationError, ValueError, RecursionError, TimeoutError) as error:
                logging.getLogger(__name__).warning("Visual correction retained earlier scene: analysis_id=%s reason=%s", proposal.analysis_id, error.code if isinstance(error,AnalysisFailure) else type(error).__name__)
                repair = "failed"
        # No second review is implied by a successful geometry check.
        if judgment.verdict != "consistent":
            result.issues = [*result.issues, issue("fidelity", "Visual review found differences or uncertainty. " + ("One correction passed deterministic validation; the revised scene has not received a second visual review." if repair == "accepted" else "The last validated reconstruction is retained with these limitations."))][:50]
            result.status = "partial"
        result.visual_review = VisualReviewResult(status="reviewed", judgment=judgment, repair=repair,
            message="Visual comparison completed. " + ("One correction was applied; photo likeness of the revised scene remains unverified." if repair == "accepted" else "Any remaining differences are listed in the review."),
            reviewed_scene_hash=digest, result_scene_hash=scene_hash(result.scene), applied_findings=applied_findings)
        result.warnings = notes(result.warnings, ["Visual review is an approximate model judgment, not a measured 3D scan or guarantee of photo accuracy."])
        return SceneProposalResponse.model_validate(result.model_dump())
