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
from time import perf_counter

import anyio
from openai import APIError
from pydantic import ValidationError

from roometry.scenes.hashing import scene_hash
from roometry.scenes.schemas import RoomScene
from roometry.scenes.validation import validate_geometry
from roometry.vision.analyzer import response_json
from roometry.vision.draft_validation import parse_scene_draft
from roometry.vision.errors import AnalysisFailure
from roometry.vision.evidence import apply_constraints
from roometry.vision.fidelity import evaluate_fidelity
from roometry.vision.partial import assemble_partial
from roometry.vision.proposal_request import build_proposal_request
from roometry.vision.proposal_schemas import (
    DraftObject,
    DraftOpening,
    SceneDraft,
    SceneProposalResponse,
)
from roometry.vision.proposals import Measurements, assemble, issue, notes
from roometry.vision.recovery import merge_repair
from roometry.vision.review_context import repair_review_context
from roometry.vision.review_policy import (
    finding_progress,
    review_targets,
    same_values,
    select_findings,
)
from roometry.vision.review_render import render_views
from roometry.vision.review_request import (
    build_appearance_repair_request,
    build_review_request,
    enforce_review_limit,
)
from roometry.vision.review_schemas import VisualReviewResult
from roometry.vision.review_validation import parse_judgment
from roometry.vision.review_validation import validate_judgment as validate_judgment
from roometry.vision.scale import photo_span_estimates
from roometry.vision.schemas import ReconstructionConstraints, RoomAnalysisResponse
from roometry.vision.functional_groups import carry_review_groups, reconstruction_group, workstation_pairs, workstation_regressions

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
    if previous.room != candidate.room or not validate_geometry(candidate).valid or workstation_regressions(analysis, previous, candidate):
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


def recover_appearance_groups(analysis, measurements, proposal, previous, patch, selected):
    """Keep independent valid palettes on the original valid poses, without calls.

    Only a patch which already passed the finding/authority scope check reaches
    here. Per-entity audit groups still validate against the original evidence;
    unsafe geometry, unsupported colors and protected edits cannot ride along.
    """
    existing = {(c.entity_id, c.field): c for c in previous.appearance_corrections}
    groups = {}
    for record in patch.appearance_corrections:
        if record != existing.get((record.entity_id, record.field)):
            groups.setdefault(record.entity_id, []).append(record)
    retained = proposal.scene
    accepted_draft = previous
    for records in groups.values():
        corrections = {(c.entity_id, c.field): c for c in accepted_draft.appearance_corrections}
        corrections.update({(c.entity_id, c.field): c for c in records})
        draft = accepted_draft.model_copy(update={'appearance_corrections': list(corrections.values())}, deep=True)
        candidate, _, _, _ = assemble_partial(analysis, measurements, draft, recover_local=False)
        if candidate is None:
            continue
        # Poses and catalog geometry remain exactly those of the last valid
        # scene. This also preserves protected flags and existing limitations.
        colors = {obj.id: obj for obj in candidate.objects}
        safe = retained.model_copy(deep=True)
        for obj in safe.objects:
            if obj.id in colors:
                obj.color, obj.material_colors = colors[obj.id].color, colors[obj.id].material_colors
        safe.finishes = candidate.finishes
        safe.appearance_corrections = candidate.appearance_corrections
        findings = [finding for _, finding in selected]
        progress = [finding_progress(proposal.scene, safe, finding) for finding in findings]
        if (reconstruction_changed(retained, safe) and candidate_allowed(analysis, proposal.scene, safe)
            and any(finding_progress(retained, safe, finding)[0] for finding in findings if finding.kind == 'color')
            and not any(regressed for _, regressed in progress)):
            retained = safe
            accepted_draft = draft.model_copy(update={'appearance_corrections': safe.appearance_corrections}, deep=True)
    if not reconstruction_changed(proposal.scene, retained):
        return None
    applied = [index for index, finding in selected if finding_progress(proposal.scene, retained, finding)[0]]
    return retained, applied


def patch_matches_findings(previous: SceneDraft, merged: SceneDraft, findings, *, analysis=None) -> bool:
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
    pairs = workstation_pairs(analysis, previous.objects) if analysis is not None else {}
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
        if pairs.get(item.id) in changed_ids and kinds.get(pairs[item.id], set()) & {"position", "orientation"}:
            permitted |= {"position", "rotation_y_rad"}
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
        started = perf_counter()
        try:
            # Finish inside the graph node's deadline so optional work cannot
            # turn a usable base into a step_timeout failure.
            with anyio.fail_after(max(.001, timeout_seconds - 1)):
                result = await self._review(original, owner, constraints, state, digest)
        except (APIError, AnalysisFailure, ValidationError, ValueError, RecursionError, TimeoutError, OSError) as error:
            logging.getLogger(__name__).warning("Visual review unavailable: analysis_id=%s reason=%s", proposal.analysis_id, error.code if isinstance(error,AnalysisFailure) else type(error).__name__)
            result = unavailable()
            service.record_repair_outcome(state, "visual_review", "unavailable", started, error=error)
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
        judgment = parse_judgment(response_json(response, settings.analysis_max_output_bytes), analysis, proposal.scene, require_comparisons=True)
        _, scope_limited = review_targets(analysis, proposal.scene)
        if scope_limited:
            judgment.limitations = notes(["The bounded review did not check every major item; further placement and appearance checks remain."], judgment.limitations)[:10]
            if judgment.verdict == "consistent":
                judgment.verdict = "uncertain"
        selected = select_findings(analysis, judgment, proposal.scene)
        actionable = [finding for _,finding in selected]
        applied_findings = []
        repair = "not_needed" if judgment.verdict == "consistent" else "not_attempted"
        result = proposal.model_copy(deep=True)
        if judgment.verdict == "needs_correction" and actionable:
            problems = [issue("fidelity", f.description, *f.entity_ids) for f in actionable if f.entity_ids]
            previous = draft_from_scene(proposal.scene)
            span_estimates, span_notes = photo_span_estimates(analysis)
            # Preserve provenance during visual repair without deriving new sizes
            # or relabeling any user-confirmed axis as a photo estimate.
            scale_notes = span_notes if span_estimates and all(
                axis in proposal.estimated_axes and value == getattr(proposal.dimensions, axis)
                for axis, value in span_estimates.items()
            ) else []
            measurements = Measurements(proposal.dimensions, [], proposal.estimated_axes, proposal.assumed_axes, scale_notes)
            wall_ids = {item.id for item in analysis.observations.walls}
            room_appearance = any(f.kind == "color" and (not f.entity_ids or set(f.entity_ids) <= wall_ids) for f in actionable)
            try:
                repair_started = perf_counter()
                repair_response = None
                repair_phase = "request_build"
                required_photos = {key for finding in actionable for key in finding.source_image_ids}
                color_only = all(f.kind == "color" for f in actionable)
                if color_only:
                    payload = build_appearance_repair_request(settings, analysis, proposal, constraints, judgment, selected, images)
                else:
                    editable = {key for finding in actionable for key in finding.entity_ids}
                    editable = reconstruction_group(analysis, previous.objects, editable)
                    while True:
                        expanded = editable | {obj.id for obj in previous.objects if obj.support_object_id in editable or obj.opening_id in editable}
                        if expanded == editable:
                            break
                        editable = expanded
                    payload = await anyio.to_thread.run_sync(partial(build_proposal_request, operation="scene_repair", mandatory_image_ids=required_photos, repair_entity_ids=editable), settings, analysis, measurements, previous, problems, images)
                context = json.loads(payload["input"][0]["content"][0]["text"])
                if not color_only:
                    context.update(repair_review_context(analysis, proposal.scene, judgment, selected))
                context["room_appearance_revision_allowed"] = room_appearance
                context["protected_object_ids"] = [obj.id for obj in proposal.scene.objects if obj.locked or obj.provenance == "user_added"]
                if settings.scene_compact_context or color_only:
                    from roometry.vision.context_budget import photo_references
                    context = photo_references(context, analysis.observations.image_ids)
                payload["input"][0]["content"][0]["text"] = json.dumps(context, separators=(',', ':'))
                # Reuse the very same bad-scene views and original photos, never
                # rendered content substituted for original photo evidence.
                for view in views:
                    payload["input"][0]["content"].extend([
                        {"type":"input_text", "text":f"Diagnostic view of the current reconstruction: {view.view_id}"},
                        {"type":"input_image", "detail":"high", "image_url":"data:image/png;base64,"+b64encode(view.image).decode("ascii")},
                    ])
                if not color_only:
                    payload["instructions"] += "\nApply only repair_objectives from selected_findings, prioritizing placement and visible material colors over accessories. A targeted position/facing change must reach its placement_target without moving the reference target. A color change must use an actually rendered appearance control and approach appearance_target.value. Update dependent surface objects with their support, preserving their relative arrangements. Do not reset colors, resize furniture or move unrelated objects merely to obtain valid geometry. For object or room color/finish differences, appearance_corrections must record entity_id (null only for room finishes), field, exact ORIGINAL observation previous_value, revised_value, reason and original source_image_ids. Do not change an unaffected appearance. Do not imitate lighting or invent textures. Respect protected_object_ids; room dimensions, user answers, measured object sizes and locks cannot change. Do not redesign.\n"
                    payload["instructions"] += "Use appearance_revision_baselines for the exact ORIGINAL previous_value, including null. A rendered/default/current scene color is not that original value. Supply a concise photo-supported reason for each correction.\n"
                payload["model"] = settings.model_route("visual_repair").model
                enforce_review_limit(settings, payload)
                await service._owned_analysis(proposal.analysis_id, owner)
                state.visual_repair_attempted = True
                repair_phase = "provider_request"
                with anyio.fail_after(settings.analysis_timeout_seconds):
                    response = await service.analyzer.request(payload, operation="visual_repair")
                repair_response = response
                await service._owned_analysis(proposal.analysis_id, owner)
                repair_phase = "response_decode"
                decoded = response_json(response, settings.analysis_max_output_bytes)
                repair_phase = "draft_parse"
                patch = parse_scene_draft(decoded, analysis)
                repair_phase = "scope_check"
                draft = merge_repair(analysis, previous, patch, problems, allow_room_appearance=room_appearance)
                draft = carry_review_groups(analysis, proposal.scene, previous, draft, actionable)
                if not patch_matches_findings(previous, draft, actionable, analysis=analysis):
                    raise AnalysisFailure(502, "invalid_analysis", "The correction exceeds the visual findings.", diagnostics={"reason": "repair_scope_violation"})
                repair_phase = "assembly"
                recovery_ids = reconstruction_group(analysis, draft.objects, {key for finding in actionable
                    if finding.kind != 'color' for key in finding.entity_ids})
                candidate, report, candidate_issues = await anyio.to_thread.run_sync(
                    partial(assemble, recover_local=True, recovery_ids=recovery_ids), analysis, measurements, draft)
                unresolved = []
                if candidate is None:
                    candidate, report, candidate_issues, unresolved = await anyio.to_thread.run_sync(
                        partial(assemble_partial, recover_local=True, recovery_ids=recovery_ids), analysis, measurements, draft)
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
                repair_phase = "acceptance"
                preserves_scene = candidate is not None and candidate_allowed(analysis, proposal.scene, candidate)
                old_poses = {item.id:item for item in [*proposal.scene.objects, *proposal.scene.openings]}
                new_poses = {item.id:item for item in [*candidate.objects, *candidate.openings]} if candidate else {}
                pose_fields = {'position', 'rotation_y_rad', 'wall', 'offset_m', 'width_m', 'height_m',
                               'sill_height_m', 'support_object_id', 'support_surface_id', 'opening_id'}
                # An untouched unresolved finding cannot block another valid
                # correction. A changed pose must actually meet its target;
                # incidental local recovery is not a successful visual repair.
                placement_progress = all(addressed or all(
                    key in old_poses and key in new_poses and same_values(
                        old_poses[key].model_dump(include=pose_fields), new_poses[key].model_dump(include=pose_fields))
                    for key in finding.entity_ids)
                    for finding, (addressed, _) in zip(actionable, progress) if finding.placement_target is not None)
                if changed and placement_progress and any(addressed for addressed,_ in progress) and not any(regressed for _,regressed in progress) and preserves_scene:
                    result.scene, result.validation = candidate, report
                    result.fidelity = evaluate_fidelity(analysis, candidate)
                    result.issues, result.unresolved_entities = candidate_issues, unresolved
                    result.status = "partial" if candidate_issues or unresolved else "ready"
                    repair = "accepted"
                    applied_findings = [index for (index,_),(addressed,_) in zip(selected,progress) if addressed]
                else:
                    repair = "rejected"
                    recovered = await anyio.to_thread.run_sync(recover_appearance_groups,
                        analysis, measurements, proposal, previous, patch, selected)
                    if recovered is not None:
                        result.scene, applied_findings = recovered
                        result.validation = validate_geometry(result.scene)
                        result.fidelity = evaluate_fidelity(analysis, result.scene)
                        repair = "accepted"
                        result.warnings = notes([
                            "Independent valid appearance corrections were retained. Unsuccessful placement changes were rolled back to their previous validated poses."
                        ], result.warnings)
                service.record_repair_outcome(state, "visual_repair", repair, repair_started, response=repair_response,
                    details={"reason": "validated_correction" if repair == "accepted" else "candidate_not_accepted",
                             "phase": repair_phase, "changed": changed, "preserves_scene": preserves_scene,
                             "progress": any(addressed for addressed, _ in progress), "regressed": any(regressed for _, regressed in progress),
                             "issue_codes": sorted({problem.code for problem in candidate_issues})[:12],
                             "geometry_codes": sorted({error.code for error in report.errors})[:12] if report else [],
                             "selected_entity_ids": sorted({key for finding in actionable for key in finding.entity_ids})[:24]})
            except (APIError, AnalysisFailure, ValidationError, ValueError, RecursionError, TimeoutError) as error:
                logging.getLogger(__name__).warning("Visual correction retained earlier scene: analysis_id=%s reason=%s", proposal.analysis_id, error.code if isinstance(error,AnalysisFailure) else type(error).__name__)
                repair = "failed"
                service.record_repair_outcome(state, "visual_repair", repair, repair_started, response=repair_response, error=error, details={"phase": repair_phase})
        # No second review is implied by a successful geometry check.
        if judgment.verdict != "consistent":
            result.issues = [*result.issues, issue("fidelity", "Visual review found differences or uncertainty. " + ("One correction passed deterministic validation; the revised scene has not received a second visual review." if repair == "accepted" else "The last validated reconstruction is retained with these limitations."))][:50]
            result.status = "partial"
        result.visual_review = VisualReviewResult(status="reviewed", judgment=judgment, repair=repair,
            message="Visual comparison completed. " + ("One correction was applied; photo likeness of the revised scene remains unverified." if repair == "accepted" else "Any remaining differences are listed in the review."),
            reviewed_scene_hash=digest, result_scene_hash=scene_hash(result.scene), applied_findings=applied_findings)
        result.warnings = notes(result.warnings, ["Visual review is an approximate model judgment, not a measured 3D scan or guarantee of photo accuracy."])
        return SceneProposalResponse.model_validate(result.model_dump())
