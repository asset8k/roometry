"""Bounded visual judgments are evidence, never authority to bypass geometry."""

from typing import Annotated, Literal, Self
import re

from pydantic import Field, model_validator

from roometry.scenes.schemas import Color, Identifier, Note, SceneModel

ViewId = Literal["cutaway_nw", "cutaway_se", "top"]
CheckStatus = Literal["consistent", "difference", "uncertain", "not_applicable"]
AppearanceField = Literal["color", "wood", "metal", "linen", "inset", "rubber", "enamel", "floor_color", "wall_color", "floor_pattern"]
# Keep the wire shape unchanged while exposing the local value restriction to
# Structured Outputs. The validators below still enforce field/value pairing.
AppearanceValue = Color | Literal["wood", "tile", "plain"]
VALUE_FORMAT = "Use wood, tile or plain only for floor_pattern. Every color field requires a six-digit #RRGGBB value, never a color name or description."


class RelationComparison(SceneModel):
    target_id: Identifier
    relation: Literal["near", "against_wall", "faces"]
    photo_support: Literal["supported", "contradicted", "unknown"]


class MaterialComparison(SceneModel):
    field: AppearanceField
    photo_value: AppearanceValue | None = Field(description=VALUE_FORMAT + " Use null when the photo does not establish a useful approximate color.")

    @model_validator(mode="after")
    def supported_value(self) -> Self:
        if self.photo_value is not None:
            valid = self.photo_value in {"wood", "tile", "plain"} if self.field == "floor_pattern" else re.fullmatch(r"#[0-9a-fA-F]{6}", self.photo_value)
            if not valid:
                raise ValueError("Photo material evidence requires a supported color or floor pattern.")
        return self


class ReviewCheck(SceneModel):
    entity_id: Identifier | None  # null is the room finishes check.
    placement: CheckStatus
    appearance: CheckStatus
    evidence: Annotated[str, Field(min_length=1, max_length=320)]
    source_image_ids: Annotated[list[str], Field(min_length=1, max_length=10)]
    # Empty defaults read historical checkpoints. New requests select bounded
    # comparisons; the server computes differences rather than trusting a vote.
    relations: Annotated[list[RelationComparison], Field(max_length=6)] = Field(default_factory=list)
    materials: Annotated[list[MaterialComparison], Field(max_length=7)] = Field(default_factory=list)


class PlacementTarget(SceneModel):
    relation: Literal["near", "against_wall", "faces"]
    target_id: Identifier


class AppearanceTarget(SceneModel):
    field: AppearanceField
    value: AppearanceValue = Field(description=VALUE_FORMAT)

    @model_validator(mode="after")
    def valid_value(self) -> Self:
        valid = self.value in {"wood", "tile", "plain"} if self.field == "floor_pattern" else re.fullmatch(r"#[0-9a-fA-F]{6}", self.value)
        if not valid:
            raise ValueError("Appearance targets require a supported color or floor pattern.")
        return self


class ReviewFinding(SceneModel):
    kind: Literal["missing", "orientation", "position", "proportion", "color", "identity", "support", "opening"]
    entity_ids: Annotated[list[Identifier], Field(max_length=8)]
    confidence: Literal["low", "medium", "high"]
    severity: Literal["minor", "major"]
    source_image_ids: Annotated[list[str], Field(min_length=1, max_length=10)]
    view_ids: Annotated[list[ViewId], Field(min_length=1, max_length=3)]
    description: Note
    correction: Note
    placement_target: PlacementTarget | None = None
    appearance_target: AppearanceTarget | None = None
    repairable: bool = True


class ReviewJudgment(SceneModel):
    verdict: Literal["consistent", "needs_correction", "uncertain"]
    summary: Note
    findings: Annotated[list[ReviewFinding], Field(max_length=12)]
    limitations: Annotated[list[Note], Field(max_length=10)]
    # Optional only for reading historical checkpoints. New reviews require
    # exactly the server-selected targets, validated before any paid repair.
    checks: Annotated[list[ReviewCheck], Field(max_length=25)] = Field(default_factory=list)

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.verdict == "consistent" and self.findings:
            raise ValueError("A consistent review cannot conceal reported differences.")
        if self.verdict == "needs_correction" and not self.findings:
            raise ValueError("Corrections require specific evidence-backed findings.")
        if len({check.entity_id for check in self.checks}) != len(self.checks):
            raise ValueError("Review each selected entity once.")
        if self.verdict == "consistent" and any(
            status in {"difference", "uncertain"}
            for check in self.checks for status in (check.placement, check.appearance)
        ):
            raise ValueError("A consistent review requires completed placement and appearance checks.")
        return self


class VisualReviewResult(SceneModel):
    status: Literal["reviewed", "unavailable"]
    judgment: ReviewJudgment | None
    repair: Literal["not_needed", "not_attempted", "accepted", "rejected", "failed"]
    message: Note
    reviewed_scene_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    result_scene_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    # A repair is validated deterministically; there is no second paid review.
    revised_scene_visually_reviewed: Literal[False] = False
    applied_findings: Annotated[list[int], Field(max_length=12)] = Field(default_factory=list)

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if (self.status == "reviewed") != (self.judgment is not None):
            raise ValueError("Only completed reviews have a judgment.")
        if self.repair != "accepted" and self.result_scene_hash != self.reviewed_scene_hash:
            raise ValueError("Unaccepted repairs must preserve the reviewed scene.")
        if len(set(self.applied_findings)) != len(self.applied_findings) or any(
            index < 0 or self.judgment is None or index >= len(self.judgment.findings)
            for index in self.applied_findings
        ) or (self.applied_findings and self.repair != "accepted"):
            raise ValueError("Applied findings must identify accepted changes from this review.")
        return self
