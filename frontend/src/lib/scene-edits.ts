import Ajv2020 from 'ajv/dist/2020.js'
import responseSchema from './edit-response.schema.json'
import type { Edit } from './edit-request.generated'
import type { PreviewEditResponse } from './edit-response.generated'
import type { RoomScene, SceneObject } from './scene.generated'

export type SceneEdit = Edit
export type EditMode = 'move' | 'rotate'

const validator = new Ajv2020({ allErrors: true, strict: true })
const validateResponse = validator.compile<PreviewEditResponse>(responseSchema)

export function parseEditPreview(data: unknown): PreviewEditResponse {
  if (!validateResponse(data)) throw new Error('Unexpected edit response')
  const reports = [data.base_validation, data.candidate_validation].filter(report => report !== null)
  if (reports.some(report => report.valid !== (report.errors.length === 0) || report.policy_version !== 'components-v1')) {
    throw new Error('Inconsistent validation report')
  }
  if (data.accepted && (!data.base_validation.valid || !data.candidate_validation?.valid || data.edit_errors.length)) {
    throw new Error('Unvalidated edit response')
  }
  if (!data.accepted && (data.changed_object_ids.length || (!data.edit_errors.length && !reports.some(report => !report.valid)))) {
    throw new Error('Inconsistent rejected edit')
  }
  if (new Set(data.changed_object_ids).size !== data.changed_object_ids.length ||
      data.changed_object_ids.some(id => !data.scene.objects.some(object => object.id === id))) {
    throw new Error('Unexpected changed object IDs')
  }
  return data
}

export function objectLabel(object: SceneObject): string {
  return object.type.replaceAll('_', ' ')
}

/** UI restrictions complement backend checks; they cannot establish ownership. */
export function editRestriction(scene: RoomScene, object: SceneObject): string | null {
  if (object.locked) return 'This object is locked. Its position, angle, and color are protected.'
  if (object.fixed) return 'This fixture stays in place. You can still preview its color.'
  if (object.placement === 'surface') return 'Move its supporting furniture to carry this object with it.'
  if (object.placement !== 'floor') return 'Direct wall and ceiling editing is not available yet.'
  if (scene.objects.some(child => child.support_object_id === object.id && (child.locked || child.fixed))) {
    return 'This furniture supports a locked or fixed object and must stay in place.'
  }
  return null
}

export function normalizeAngle(angle: number): number {
  const remainder = angle % (2 * Math.PI)
  const result = remainder < 0 ? remainder + 2 * Math.PI : remainder
  return result === 2 * Math.PI || Object.is(result, -0) ? 0 : result
}

export function validEditValues(edit: SceneEdit): boolean {
  return edit.operation === 'rotate' ? Number.isFinite(edit.rotation_y_rad)
    : [edit.position.x, edit.position.z].every(value => Number.isFinite(value) && Math.abs(value) <= 100)
}

/** Visual candidate only. Python remains responsible for spatial validation. */
export function previewTransform(scene: RoomScene, edit: SceneEdit): RoomScene {
  const parent = scene.objects.find(object => object.id === edit.object_id)
  if (!parent || editRestriction(scene, parent) || !validEditValues(edit)) return scene
  const x = edit.operation === 'move' ? edit.position.x : parent.position.x
  const z = edit.operation === 'move' ? edit.position.z : parent.position.z
  const angle = edit.operation === 'rotate' ? normalizeAngle(edit.rotation_y_rad) : parent.rotation_y_rad
  if (![x, z, angle].every(Number.isFinite) || Math.abs(x) > 100 || Math.abs(z) > 100) return scene
  if (x === parent.position.x && z === parent.position.z && angle === parent.rotation_y_rad) return scene
  const delta = angle - parent.rotation_y_rad
  const c = Math.cos(delta), s = Math.sin(delta)
  return {
    ...scene,
    objects: scene.objects.map(object => {
      if (object.id === parent.id) {
        return { ...object, position: { ...object.position, x, z }, rotation_y_rad: angle }
      }
      if (object.support_object_id !== parent.id) return object
      const dx = object.position.x - parent.position.x
      const dz = object.position.z - parent.position.z
      return {
        ...object,
        position: { ...object.position, x: x + c * dx + s * dz, z: z - s * dx + c * dz },
        rotation_y_rad: normalizeAngle(object.rotation_y_rad + delta),
      }
    }),
  }
}

export function describeRejection(result: PreviewEditResponse, scene: RoomScene): string[] {
  const issues = result.edit_errors.length ? result.edit_errors
    : !result.base_validation.valid ? result.base_validation.errors
      : result.candidate_validation?.errors ?? []
  return issues.map(issue => {
    const labels = issue.entity_ids.map(id => {
      const object = scene.objects.find(item => item.id === id)
      return object ? objectLabel(object) : scene.openings.find(item => item.id === id)?.type ?? 'object'
    })
    return `${labels.join(' / ')}: ${issue.message}`
  })
}
