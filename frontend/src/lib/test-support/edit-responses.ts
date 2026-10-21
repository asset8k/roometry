import type { GeometryReport, PreviewEditResponse } from '../edit-response.generated'
import type { RoomScene } from '../scene.generated'

export function geometryReport(valid = true): GeometryReport {
  return {
    policy_version: 'components-v1', tolerance_m: 0.000001, rotation_tolerance_rad: 0.000001,
    valid, warnings: [], errors: valid ? [] : [{
      code: 'object_overlap', entity_ids: ['bed_1', 'desk_1'], message: 'Objects overlap.',
    }],
  }
}

export function acceptedMove(scene: RoomScene, x = 0.85): PreviewEditResponse {
  const candidate = structuredClone(scene)
  for (const object of candidate.objects) {
    if (object.id === 'desk_1' || object.id === 'computer_1') object.position.x = x
  }
  return {
    accepted: true, scene: candidate, changed_object_ids: ['computer_1', 'desk_1'],
    edit_errors: [], base_validation: geometryReport(), candidate_validation: geometryReport(),
  }
}

export function rejectedMove(scene: RoomScene): PreviewEditResponse {
  return {
    accepted: false, scene: structuredClone(scene), changed_object_ids: [], edit_errors: [],
    base_validation: geometryReport(), candidate_validation: geometryReport(false),
  }
}
