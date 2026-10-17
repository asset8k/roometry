import { describe, expect, it } from 'vitest'
import { Group } from 'three'
import { loadExampleScene } from './scene'
import { editRestriction, parseEditPreview, previewTransform } from './scene-edits'
import { acceptedMove, rejectedMove } from './test-support/edit-responses'
import { gizmoEdit } from '../components/room-viewer/gizmo-edit'

const move = { operation: 'move' as const, object_id: 'desk_1', position: { x: 0.85, z: 2.1 } }

describe('visual edit candidates', () => {
  it('moves the support and child without mutating source, dimensions, elevation, or metadata', () => {
    const scene = loadExampleScene()
    const before = structuredClone(scene)
    const draft = previewTransform(scene, move)
    expect(draft.objects.find(object => object.id === 'desk_1')?.position).toEqual({ x: 0.85, y: 0, z: 2.1 })
    expect(draft.objects.find(object => object.id === 'computer_1')?.position).toEqual({ x: 0.85, y: 0.75, z: 2.1 })
    for (const object of draft.objects) {
      const original = scene.objects.find(item => item.id === object.id)!
      expect(object.dimensions_m).toEqual(original.dimensions_m)
      expect(object.color).toBe(original.color)
      expect(object.fixed).toBe(original.fixed)
      expect(object.locked).toBe(original.locked)
    }
    expect(scene).toEqual(before)
  })

  it('rotates an off-center child around the support pivot with relative orientation', () => {
    const scene = structuredClone(loadExampleScene())
    const child = scene.objects.find(object => object.id === 'computer_1')!
    child.position.x = 0.75
    child.position.z = 2.05
    child.rotation_y_rad = 0.2
    const draft = previewTransform(scene, { operation: 'rotate', object_id: 'desk_1', rotation_y_rad: Math.PI / 2 })
    const rotated = draft.objects.find(object => object.id === 'computer_1')!
    expect(rotated.position.x).toBeCloseTo(0.7)
    expect(rotated.position.z).toBeCloseTo(1.9)
    expect(rotated.rotation_y_rad).toBeCloseTo(0.2 + Math.PI / 2)
    expect(child.position.x).toBe(0.75)
  })

  it.each(['bed_1', 'tv_1', 'computer_1', 'chandelier_1'])('keeps protected/nonfloor %s inspectable but uneditable', id => {
    const scene = loadExampleScene()
    expect(editRestriction(scene, scene.objects.find(object => object.id === id)!)).toBeTruthy()
    expect(previewTransform(scene, { ...move, object_id: id })).toBe(scene)
  })

  it('disables a support whose child is protected', () => {
    const scene = structuredClone(loadExampleScene())
    scene.objects.find(object => object.id === 'computer_1')!.locked = true
    expect(editRestriction(scene, scene.objects.find(object => object.id === 'desk_1')!)).toContain('supports')
    expect(previewTransform(scene, move)).toBe(scene)
  })

  it('normalizes angles and ignores nonfinite values, unknown objects, and unchanged poses', () => {
    const scene = loadExampleScene()
    const rotated = previewTransform(scene, { operation: 'rotate', object_id: 'desk_1', rotation_y_rad: -Math.PI / 2 })
    expect(rotated.objects.find(object => object.id === 'desk_1')!.rotation_y_rad).toBeCloseTo(3 * Math.PI / 2)
    expect(previewTransform(scene, { ...move, object_id: 'missing_1' })).toBe(scene)
    expect(previewTransform(scene, { ...move, position: { x: NaN, z: 2 } })).toBe(scene)
    expect(previewTransform(scene, { ...move, position: { x: 0.65, z: 2 } })).toBe(scene)
    expect(previewTransform(scene, { operation: 'rotate', object_id: 'desk_1', rotation_y_rad: 2 * Math.PI })).toBe(scene)
  })

  it.each([0, Math.PI / 2, 3 * Math.PI / 4, Math.PI, 3 * Math.PI / 2])('extracts full gizmo yaw at %s without Euler folding', angle => {
    const object = loadExampleScene().objects.find(item => item.id === 'desk_1')!
    const proxy = new Group()
    proxy.quaternion.setFromAxisAngle(proxy.up, angle)
    const edit = gizmoEdit(object, proxy, 'rotate')
    expect(edit.operation).toBe('rotate')
    if (edit.operation !== 'rotate') throw new Error('Wrong operation')
    expect(Math.cos(edit.rotation_y_rad)).toBeCloseTo(Math.cos(angle))
    expect(Math.sin(edit.rotation_y_rad)).toBeCloseTo(Math.sin(angle))
  })
})

describe('generated edit response validation', () => {
  it('accepts the documented success and rejection contracts', () => {
    const scene = loadExampleScene()
    expect(parseEditPreview(acceptedMove(scene)).accepted).toBe(true)
    expect(parseEditPreview(rejectedMove(scene)).accepted).toBe(false)
  })

  it.each(['missing-scene', 'false-acceptance', 'inconsistent-report', 'missing-validation', 'unknown-changed-id', 'unsupported-policy'])('rejects %s responses before commit', fault => {
    const data = acceptedMove(loadExampleScene())
    if (fault === 'missing-scene') delete (data as Partial<typeof data>).scene
    if (fault === 'false-acceptance') data.edit_errors.push({ code: 'object_locked', entity_ids: ['desk_1'], message: 'Locked' })
    if (fault === 'inconsistent-report') data.base_validation.valid = false
    if (fault === 'missing-validation') data.candidate_validation = null
    if (fault === 'unknown-changed-id') data.changed_object_ids.push('missing_1')
    if (fault === 'unsupported-policy') delete data.base_validation.policy_version
    expect(() => parseEditPreview(data)).toThrow()
  })

  it('rejects inconsistent rejections and duplicate changed IDs', () => {
    const rejected = rejectedMove(loadExampleScene())
    rejected.changed_object_ids.push('desk_1')
    expect(() => parseEditPreview(rejected)).toThrow()
    const accepted = acceptedMove(loadExampleScene())
    accepted.changed_object_ids.push('desk_1')
    expect(() => parseEditPreview(accepted)).toThrow()
  })
})
