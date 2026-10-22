"""Read-only demo scene calculations; no saved-project access or revisions."""

from fastapi import APIRouter

from roometry.scenes.edit_schemas import (
    PreviewEditRequest,
    PreviewEditResponse,
    ValidateSceneRequest,
)
from roometry.scenes.service import preview_edit
from roometry.scenes.scoring import LayoutScoreReport, ScoreLayoutRequest, score_layout
from roometry.scenes.validation import GeometryReport, validate_geometry

router = APIRouter(prefix="/api/scenes", tags=["scenes"])


@router.post(
    "/score",
    response_model=LayoutScoreReport,
    summary="Score soft layout goals with explicit measurements and formulas",
    description="Stateless calculation. Returns openness, nominal storage and local workspace proxies, separate geometry findings, and unassessed aesthetics. Does not modify scenes or authorize edits. An overall score is unset when a selected criterion cannot be assessed.",
)
def score_scene_endpoint(request: ScoreLayoutRequest) -> LayoutScoreReport:
    return score_layout(request.scene, request.goals, request.policy)


@router.post(
    "/validate",
    response_model=GeometryReport,
    summary="Validate scene geometry, door clearance and basic walking routes",
    description="Stateless calculation. Door and walking findings are advisory; valid refers to component geometry. Walking results use configurable planning thresholds and a bounded grid. Does not save scenes or establish ownership or full physical acceptance.",
)
def validate_scene_endpoint(request: ValidateSceneRequest) -> GeometryReport:
    return validate_geometry(request.scene, door_policy=request.door_policy, walking_policy=request.walking_policy)


@router.post(
    "/preview-edit",
    response_model=PreviewEditResponse,
    summary="Preview an absolute move or rotation on a supplied demo scene",
    description=(
        "Stateless calculation. Protection checks use the supplied base scene, not trusted saved state. "
        "New clearance obstructions and route regressions are rejected relative to the supplied base using server defaults. "
        "Existing clearance defects remain viewable. A rejected edit returns that base unchanged. Nothing is persisted."
    ),
)
def preview_edit_endpoint(request: PreviewEditRequest) -> PreviewEditResponse:
    return preview_edit(request)
