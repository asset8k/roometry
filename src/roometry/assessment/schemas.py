"""Estimated, versioned card scores; these never authorize scene edits."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from roometry.scenes.schemas import Identifier
from roometry.scenes.scoring import Criterion, ScoreMetric

CATEGORIES = ("openness", "storage", "workspace", "aesthetics")


class AssessmentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, revalidate_instances="always")


class AssessmentCard(AssessmentModel):
    status: Literal["rated", "unassessed", "not_applicable"]
    score: Annotated[float, Field(ge=0, le=10, allow_inf_nan=False)] | None
    confidence: Literal["low", "medium", "high"]
    source: Literal["geometry", "photos"]
    explanation: Annotated[str, Field(min_length=1, max_length=400)]
    entity_ids: Annotated[list[Identifier], Field(max_length=200)] = Field(default_factory=list)
    metrics: Annotated[list[ScoreMetric], Field(max_length=12)] = Field(default_factory=list)

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if (self.status == "rated") != (self.score is not None):
            raise ValueError("Only assessed categories have a numeric rating.")
        return self


def recommendation(cards) -> Criterion | None:
    eligible = [key for key in CATEGORIES if getattr(cards, key).status == "rated"
                and getattr(cards, key).confidence != "low" and getattr(cards, key).score < 8]
    return min(eligible, key=lambda key: getattr(cards, key).score) if eligible else None


class RoomAssessment(AssessmentModel):
    policy_version: Literal["room-assessment-v1", "room-assessment-v2", "room-assessment-v3"] = "room-assessment-v3"
    scene_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    openness: AssessmentCard
    storage: AssessmentCard
    workspace: AssessmentCard
    aesthetics: AssessmentCard
    recommended: Criterion | None
    notes: Annotated[list[str], Field(max_length=8)]

    @model_validator(mode="after")
    def consistent_recommendation(self) -> Self:
        if self.recommended != recommendation(self):
            raise ValueError("Recommend only the lowest supported, relevant score below eight.")
        return self


AESTHETIC_FIELDS = ("color_harmony", "material_cohesion", "visual_balance", "visual_order", "lighting_composition")
AESTHETIC_INSTRUCTIONS = """Use the same fixed five-part aesthetic rubric: color_harmony,
material_cohesion, visual_balance, visual_order, lighting_composition. Each aspect
is 0=weak/conflicting, 1=mixed, 2=cohesive. Do not reward expense or impose a style.
Judge visible evidence, not camera exposure or missing catalog textures.
"""


class AestheticScores(AssessmentModel):
    # Fixed anchors: 0 weak, 1 mixed, 2 strong. Personal taste is not inferred.
    color_harmony: Annotated[int, Field(ge=0, le=2)]
    material_cohesion: Annotated[int, Field(ge=0, le=2)]
    visual_balance: Annotated[int, Field(ge=0, le=2)]
    visual_order: Annotated[int, Field(ge=0, le=2)]
    lighting_composition: Annotated[int, Field(ge=0, le=2)]

    @property
    def total(self) -> float:
        return float(sum(getattr(self, key) for key in AESTHETIC_FIELDS))


class AestheticRubric(AestheticScores):
    confidence: Literal["low", "medium", "high"]
    explanation: Annotated[str, Field(min_length=1, max_length=320)]
    source_image_ids: Annotated[list[str], Field(min_length=1, max_length=10)]


class PhotoAssessment(AssessmentModel):
    aesthetics: AestheticRubric | None


class AestheticComparison(AssessmentModel):
    retained: AestheticScores
    candidate: AestheticScores
    confidence: Literal["low", "medium", "high"]
    explanation: Annotated[str, Field(min_length=1, max_length=320)]
