"""Read-only demo scene calculations; no saved-project access or revisions."""

from fastapi import APIRouter

from roometry.scenes.edit_schemas import (
    PreviewEditRequest,
    PreviewEditResponse,
    ValidateSceneRequest,
)
from roometry.scenes.service import preview_edit
from roometry.scenes.validation import GeometryReport, validate_geometry

router = APIRouter(prefix="/api/scenes", tags=["scenes"])


@router.post(
    "/validate",
    response_model=GeometryReport,
    summary="Validate a supplied demo scene's baseline geometry",
    description="Stateless calculation. Does not save scenes or establish ownership or full physical acceptance.",
)
def validate_scene_endpoint(request: ValidateSceneRequest) -> GeometryReport:
    return validate_geometry(request.scene)


@router.post(
    "/preview-edit",
    response_model=PreviewEditResponse,
    summary="Preview an absolute move or rotation on a supplied demo scene",
    description=(
        "Stateless calculation. Protection checks use the supplied base scene, not trusted saved state. "
        "A rejected edit returns that base unchanged. Nothing is persisted."
    ),
)
def preview_edit_endpoint(request: PreviewEditRequest) -> PreviewEditResponse:
    return preview_edit(request)
