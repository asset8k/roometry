"""Recover independent review items without turning invalid evidence into authority.

The response envelope and resource limits remain strict. Bad findings/checks are
quarantined locally; missing or contradictory checks become explicitly uncertain.
Only a finding with a valid, agreeing check can authorize a correction.
"""

from collections import Counter
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, ValidationError

from roometry.scenes.schemas import Note, SceneModel
from roometry.vision.review_policy import appearance_controls, review_targets
from roometry.vision.review_render import VIEW_IDS
from roometry.vision.review_schemas import ReviewCheck, ReviewFinding, ReviewJudgment
from roometry.vision.review_comparisons import apply_comparisons, parse_comparisons


class ReviewEnvelope(SceneModel):
    verdict: Literal["consistent", "needs_correction", "uncertain"]
    summary: Note
    findings: Annotated[list[object], Field(max_length=12)]
    limitations: Annotated[list[object], Field(max_length=10)]
    checks: Annotated[list[object], Field(max_length=25)]


def finding_aspects(finding, check):
    if finding.kind == "color":
        return {"appearance"}
    # Identity includes unsupported artwork/shape differences as well as wrong
    # furniture models. It is not necessarily a placement complaint. An
    # appearance-only identity limitation cannot authorize an asset/pose edit.
    if finding.kind == "identity" and not finding.repairable:
        return {aspect for aspect in ("placement", "appearance") if getattr(check, aspect) == "difference"} or {"placement", "appearance"}
    return {"placement"}


def related(finding, key, walls):
    return key in finding.entity_ids if key is not None else (
        finding.kind == "color" and (not finding.entity_ids or set(finding.entity_ids) <= walls)
    )


def validate_finding_evidence(analysis, finding):
    ids = {item.id for item in [*analysis.observations.walls, *analysis.observations.objects, *analysis.observations.openings]}
    allowed = (ids, set(analysis.observations.image_ids), set(VIEW_IDS))
    for values, valid in zip((finding.entity_ids, finding.source_image_ids, finding.view_ids), allowed):
        if not set(values) <= valid or len(values) != len(set(values)):
            raise ValueError("Review findings must cite unique original evidence and known identities.")
    sources = {item.id: set(item.source_image_ids) for item in [*analysis.observations.walls, *analysis.observations.objects, *analysis.observations.openings]}
    if finding.entity_ids and not set(finding.source_image_ids) <= set().union(*(sources[key] for key in finding.entity_ids)):
        raise ValueError("Review findings require their subjects' recorded photo evidence.")


def validate_finding_target(analysis, finding, scene):
    ids = {item.id for item in [*analysis.observations.walls, *analysis.observations.objects, *analysis.observations.openings]}
    walls = {item.id for item in analysis.observations.walls}
    objects = {item.id: item for item in scene.objects}
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
        if target.field in {"floor_color", "wall_color", "floor_pattern"}:
            if finding.entity_ids and not set(finding.entity_ids) <= walls:
                raise ValueError("Room appearance targets may reference only observed walls or room finishes.")
        elif not finding.entity_ids or any(key not in objects or target.field not in appearance_controls(objects[key]) for key in finding.entity_ids):
            raise ValueError("Choose a material control actually rendered by this object's catalog model.")
    elif finding.appearance_target is not None:
        raise ValueError("Appearance targets belong only to repairable color findings.")


def validate_check(check, target):
    if len(set(check.source_image_ids)) != len(check.source_image_ids) or not set(check.source_image_ids) <= set(target["source_image_ids"]):
        raise ValueError("Each object check requires its own recorded photo evidence.")
    if (check.placement == "not_applicable") != (check.entity_id is None) or (check.appearance == "not_applicable" and target["kind"] != "opening"):
        raise ValueError("Major furniture needs placement and appearance checks.")
    if check.entity_id is not None and not target["retained"] and (
        check.placement == "consistent" or check.appearance == "consistent"
    ):
        raise ValueError("An omitted major item cannot be reported as a consistent reconstruction.")
    relations = [(item.target_id, item.relation) for item in check.relations]
    materials = [item.field for item in check.materials]
    allowed = {(item["target_id"], item["relation"]) for item in target["relation_probes"]}
    if len(relations) != len(set(relations)) or not set(relations) <= allowed or len(materials) != len(set(materials)) or not set(materials) <= set(target["material_probes"]):
        raise ValueError("Photo comparisons must uniquely identify server-selected relationships and rendered material regions.")


def validate_judgment(analysis, judgment, scene):
    """Strict postcondition, also usable by callers inspecting saved judgments."""
    targets, _ = review_targets(analysis, scene)
    expected = {target["entity_id"]: target for target in targets}
    walls = {item.id for item in analysis.observations.walls}
    if {check.entity_id for check in judgment.checks} != set(expected):
        raise ValueError("Complete the server-selected placement and appearance checks.")
    for finding in judgment.findings:
        validate_finding_evidence(analysis, finding)
        validate_finding_target(analysis, finding, scene)
    for check in judgment.checks:
        validate_check(check, expected[check.entity_id])
        for aspect in ("placement", "appearance"):
            matching = [f for f in judgment.findings if related(f, check.entity_id, walls) and aspect in finding_aspects(f, check)]
            status = getattr(check, aspect)
            if (status == "difference" and not matching) or (status == "consistent" and matching):
                raise ValueError("Each reported difference must agree with its object check and finding.")
            if status == "uncertain" and any(f.repairable for f in matching):
                raise ValueError("Uncertain checks cannot authorize corrections.")


def parse_judgment(decoded, analysis, scene, *, require_comparisons=False):
    envelope = ReviewEnvelope.model_validate(decoded)
    targets, _ = review_targets(analysis, scene)
    expected = {target["entity_id"]: target for target in targets}
    walls = {item.id for item in analysis.observations.walls}
    warnings, findings, checks = [], [], {}
    limitations = []
    for index, raw in enumerate(envelope.limitations, 1):
        try:
            limitations.append(TypeAdapter(Note).validate_python(raw, strict=True))
        except ValidationError:
            warnings.append(f"Review limitation {index} was skipped because its data was invalid.")
    for index, raw in enumerate(envelope.findings, 1):
        try:
            finding = ReviewFinding.model_validate(raw)
            validate_finding_evidence(analysis, finding)
        except (ValidationError, ValueError):
            warnings.append(f"Review finding {index} was skipped because its data or photo evidence was invalid.")
            continue
        try:
            validate_finding_target(analysis, finding, scene)
        except ValueError:
            finding.repairable = False
            finding.placement_target = finding.appearance_target = None
            warnings.append(f"Review finding {index} is retained as a limitation; its correction target was unsupported.")
        findings.append(finding)

    # Quarantine all conflicting duplicates, rather than selecting an arbitrary
    # first/last check. JSON objects with duplicate keys are rejected upstream.
    parsed = []
    for index, raw in enumerate(envelope.checks, 1):
        try:
            base = {**raw, "relations": [], "materials": []} if isinstance(raw, dict) else raw
            check = ReviewCheck.model_validate(base)
            if check.entity_id not in expected:
                raise ValueError("Unknown review target")
            validate_check(check, expected[check.entity_id])
            comparisons = parse_comparisons(raw, expected[check.entity_id], warnings)
            check = ReviewCheck.model_validate({**check.model_dump(), **comparisons})
        except (ValidationError, ValueError):
            warnings.append(f"Review check {index} was skipped because its data or photo evidence was invalid.")
            continue
        parsed.append(check)
    counts = Counter(raw["entity_id"] for raw in envelope.checks
                     if isinstance(raw, dict) and "entity_id" in raw
                     and (raw["entity_id"] is None or isinstance(raw["entity_id"], str))
                     and raw["entity_id"] in expected)
    for check in parsed:
        if counts[check.entity_id] == 1:
            checks[check.entity_id] = check
    if any(count > 1 for count in counts.values()):
        warnings.append("Duplicate review checks were excluded; the affected items remain unverified.")
    for key, target in expected.items():
        if key not in checks:
            checks[key] = ReviewCheck(entity_id=key,
                placement="not_applicable" if key is None else "uncertain",
                appearance="not_applicable" if target["kind"] == "opening" else "uncertain",
                evidence="This item did not receive a valid, unique photo comparison; it remains unverified.",
                source_image_ids=target["source_image_ids"])
            warnings.append("An omitted or invalid item check was marked uncertain; other valid checks were retained.")

    if require_comparisons:
        findings = apply_comparisons(scene, expected, checks, findings, warnings)

    # A valid subject finding still needs an agreeing subject check before it
    # can spend a repair call. Disagreement stays visible, never silently fixed.
    for index, finding in enumerate(findings, 1):
        subjects = [check for check in checks.values() if related(finding, check.entity_id, walls)]
        if not subjects or any(
            getattr(check, aspect) != "difference"
            for check in subjects for aspect in finding_aspects(finding, check)
        ):
            if finding.repairable:
                warnings.append(f"Correction for retained finding {index} was withheld because its item check was missing or disagreed.")
            finding.repairable = False
            finding.placement_target = finding.appearance_target = None
    for check in checks.values():
        for aspect in ("placement", "appearance"):
            status = getattr(check, aspect)
            matching = [f for f in findings if related(f, check.entity_id, walls) and aspect in finding_aspects(f, check)]
            if (status == "difference" and not matching) or (status == "consistent" and matching):
                setattr(check, aspect, "uncertain")
                warnings.append("A conflicting review finding/check was marked uncertain; it cannot authorize a correction.")
    # An uncertain counterpart can also affect a multi-subject finding.
    for finding in findings:
        if any(getattr(check, aspect) == "uncertain" for check in checks.values()
               if related(finding, check.entity_id, walls) for aspect in finding_aspects(finding, check)):
            finding.repairable = False
            finding.placement_target = finding.appearance_target = None

    incomplete = bool(warnings) or any(status == "uncertain" for check in checks.values() for status in (check.placement, check.appearance))
    verdict = "needs_correction" if findings else "uncertain" if incomplete or envelope.verdict != "consistent" else "consistent"
    # Keep a server warning ahead of model-filled limitation slots. Never echo
    # rejected model values or foreign IDs in public diagnostics.
    warnings = list(dict.fromkeys(warnings))
    recovery_notes = (["Some visual-review items could not be validated; valid items were retained and unverified checks remain uncertain.", *warnings] if warnings else [])
    summary = "The detailed photo comparisons identify reconstruction differences; remaining uncertainty is listed below." if envelope.verdict == "consistent" and verdict != "consistent" else envelope.summary
    judgment = ReviewJudgment(verdict=verdict, summary=summary, findings=findings,
        checks=list(checks.values()), limitations=list(dict.fromkeys([*recovery_notes, *limitations]))[:10])
    validate_judgment(analysis, judgment, scene)
    return judgment
