import { describe, expect, it } from 'vitest'
import { Group } from 'three'
import { loadExampleScene } from './test-support/scenes'
import { editRestriction, parseEditPreview, previewTransform } from './scene-edits'
import { acceptedMove, rejectedMove, geometryReport } from './test-support/edit-responses'
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

  it('carries nested supports and protects a locked grandchild', () => {
    const scene = structuredClone(loadExampleScene())
    const child = scene.objects.find(object => object.id === 'computer_1')!
    const grandchild = structuredClone(child)
    grandchild.id = 'nested_child'
    grandchild.support_object_id = child.id
    grandchild.position.y += .1
    scene.objects.push(grandchild)
    const preview = previewTransform(scene, move)
    expect(preview.objects.find(object => object.id === grandchild.id)?.position).toEqual({ x: .85, y: .85, z: 2.1 })
    expect(grandchild.position.x).toBe(.65)
    grandchild.locked = true
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
  it('accepts advisory walking findings without hiding a geometry-valid scene', () => {
    const data = acceptedMove(loadExampleScene())
    data.base_validation.walking_space = {
      policy_version: 'walking-v1', status: 'blocked',
      policy: { minimum_width_m: .6, preferred_width_m: .8, probe_height_m: 1.8, floor_gap_m: .02, cell_size_m: .1, significant_region_area_m2: .5 },
      grid_step_x_m: .1, grid_step_z_m: .1, grid_cells: 1400,
      walkable_center_area_m2: 4, main_region_area_m2: 3, disconnected_center_area_m2: 1, significant_region_count: 2,
      entrances: [{ door_id: 'door_1', status: 'blocked', main_region_reachable: false, reachable_door_ids: [], unreachable_door_ids: [] }],
      issues: [{ code: 'entrance_route_blocked', entity_ids: ['door_1'], message: 'No sampled entrance route was found.' }],
    }
    data.base_validation.warnings.push(...data.base_validation.walking_space.issues)
    const result = parseEditPreview(data)
    expect(result.accepted).toBe(true)
    expect(result.base_validation.valid).toBe(true)
    expect(result.base_validation.walking_space?.status).toBe('blocked')
  })
  it('accepts advisory door findings without treating them as component geometry errors', () => {
    const data = acceptedMove(loadExampleScene())
    data.base_validation.door_clearance = {
      policy_version: 'door-clearance-v1', policy: { access_depth_m: .8, floor_gap_m: .02 },
      doors: [{ door_id: 'door_1', access_status: 'blocked', swing_status: 'unknown', access_object_ids: ['desk_1'], swing_object_ids: [] }],
      issues: [{ code: 'door_access_blocked', entity_ids: ['door_1', 'desk_1'], message: 'Door access zone is obstructed.' },
        { code: 'door_swing_unknown', entity_ids: ['door_1'], message: 'The hinge is unknown.' }],
    }
    data.base_validation.warnings.push(...data.base_validation.door_clearance.issues)
    const result = parseEditPreview(data)
    expect(result.accepted).toBe(true)
    expect(result.base_validation.valid).toBe(true)
    expect(result.base_validation.door_clearance?.doors[0].swing_status).toBe('unknown')
  })
  it('accepts the documented success and rejection contracts', () => {
    const scene = loadExampleScene()
    expect(parseEditPreview(acceptedMove(scene)).accepted).toBe(true)
    expect(parseEditPreview(rejectedMove(scene)).accepted).toBe(false)
  })

  it.each(['missing-scene', 'false-acceptance', 'inconsistent-report', 'missing-validation', 'unknown-changed-id', 'unsupported-policy', 'outdated-policy'])('rejects %s responses before commit', fault => {
    const data = acceptedMove(loadExampleScene())
    if (fault === 'missing-scene') delete (data as Partial<typeof data>).scene
    if (fault === 'false-acceptance') data.edit_errors.push({ code: 'object_locked', entity_ids: ['desk_1'], message: 'Locked' })
    if (fault === 'inconsistent-report') data.base_validation.valid = false
    if (fault === 'missing-validation') data.candidate_validation = null
    if (fault === 'unknown-changed-id') data.changed_object_ids.push('missing_1')
    if (fault === 'unsupported-policy') delete data.base_validation.policy_version
    if (fault === 'outdated-policy') Object.assign(data.base_validation, { policy_version: 'baseline-v1' })
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

it('parses a clearance rejection with valid component geometry as a rejected edit', () => {
  const scene = loadExampleScene()
  const response = {
    accepted: false, scene, changed_object_ids: [],
    base_validation: geometryReport(), candidate_validation: geometryReport(),
    edit_errors: [{ code: 'clearance_regression', entity_ids: ['door_1', 'desk_1'], message: 'Door access blocked. Previous layout retained.' }],
  }
  expect(parseEditPreview(response).accepted).toBe(false)
})
