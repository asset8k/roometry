"""Stateless demo previews; do not treat client-supplied bases as saved state."""

from roometry.scenes.edit_schemas import (
    EditIssue,
    PreviewEditRequest,
    PreviewEditResponse,
)
from roometry.scenes.clearance_acceptance import clearance_regressions
from roometry.scenes.mutations import EditRejected, apply_edit
from roometry.scenes.validation import validate_geometry


def preview_edit(request: PreviewEditRequest) -> PreviewEditResponse:
    request = PreviewEditRequest.model_validate(request)
    base = request.scene
    base_validation = validate_geometry(base)
    if not base_validation.valid:
        return PreviewEditResponse(
            accepted=False,
            scene=base,
            changed_object_ids=(),
            edit_errors=(
                EditIssue(
                    code="invalid_base_geometry",
                    entity_ids=tuple(
                        sorted(
                            {
                                entity_id
                                for issue in base_validation.errors
                                for entity_id in issue.entity_ids
                            }
                        )
                    ),
                    message="Supplied base scene fails baseline geometry; no edit was attempted.",
                ),
            ),
            base_validation=base_validation,
            candidate_validation=None,
        )

    try:
        candidate = apply_edit(base, request.edit)
    except EditRejected as error:
        return PreviewEditResponse(
            accepted=False,
            scene=base,
            changed_object_ids=(),
            edit_errors=(
                EditIssue(
                    code=error.code,
                    entity_ids=error.entity_ids,
                    message=str(error),
                ),
            ),
            base_validation=base_validation,
            candidate_validation=None,
        )

    candidate_validation = validate_geometry(candidate)
    if not candidate_validation.valid:
        return PreviewEditResponse(
            accepted=False,
            scene=base,
            changed_object_ids=(),
            edit_errors=(),
            base_validation=base_validation,
            candidate_validation=candidate_validation,
        )

    clearance_errors = clearance_regressions(base_validation, candidate_validation)
    if clearance_errors:
        return PreviewEditResponse(
            accepted=False, scene=base, changed_object_ids=(),
            edit_errors=clearance_errors, base_validation=base_validation,
            candidate_validation=candidate_validation,
        )

    originals = {obj.id: obj for obj in base.objects}
    changed = tuple(
        sorted(obj.id for obj in candidate.objects if obj != originals[obj.id])
    )
    return PreviewEditResponse(
        accepted=True,
        scene=candidate,
        changed_object_ids=changed,
        edit_errors=(),
        base_validation=base_validation,
        candidate_validation=candidate_validation,
    )
