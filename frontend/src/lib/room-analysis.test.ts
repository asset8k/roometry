import { describe, expect, it } from 'vitest'
import { parseRoomAnalysis, parseSceneProposal, RoomAnalysisError } from './room-analysis'
import { analyzedRoom, proposedRoom, needsDimensions, partialRoom } from './test-support/room-analysis'

describe('analysis and proposal semantics', () => {
  it('accepts a pending physical wall assignment without a scene or missing numeric dimensions', () => {
    const pending = { ...proposedRoom(), status: 'needs_wall_assignment', scene: null, validation: null,
      wall_axes: { status: 'needs_clarification', reference_axis: 'width_m', reference_span_m: 4, candidate_wall_ids: ['window_wall', 'tv_wall'], bindings: [], warnings: [] } }
    expect(parseSceneProposal(pending).status).toBe('needs_wall_assignment')
    expect(() => parseSceneProposal({ ...pending, wall_axes: null })).toThrow()
    expect(() => parseSceneProposal({ ...pending, dimensions_to_confirm: ['width_m'] })).toThrow()
    expect(() => parseSceneProposal({ ...pending, scene: proposedRoom().scene })).toThrow()
  })
  it('accepts an honest dimension request and incomplete result without opening a scene', () => {
    expect(parseSceneProposal(needsDimensions()).scene).toBeNull()
    expect(parseSceneProposal({ ...proposedRoom(), status: 'incomplete', scene: null, validation: null,
      issues: [{ code: 'geometry', entity_ids: ['bed_observed'], message: 'Outside room.' }] }).scene).toBeNull()
  })
  it.each([
    { scene: null }, { validation: null }, { dimensions_to_confirm: ['height_m'] },
    { estimated_axes: ['width_m'] }, { status: 'incomplete' },
  ])('rejects invalid ready/incomplete states before rendering %j', patch => {
    expect(() => parseSceneProposal({ ...proposedRoom(), ...patch })).toThrow(RoomAnalysisError)
  })
  it('rejects contradictory geometry flags and scene dimensions', () => {
    const data = proposedRoom()
    expect(() => parseSceneProposal({ ...data, validation: { ...data.validation, valid: false } })).toThrow()
    expect(() => parseSceneProposal({ ...data, dimensions: { ...data.dimensions, width_m: 9 } })).toThrow()
    expect(() => parseRoomAnalysis({ ...analyzedRoom(), observations: {} })).toThrow()
  })
  it('accepts a validated partial scene while retaining explicit unresolved observations', () => {
    const partial = partialRoom()
    expect(parseSceneProposal(partial).status).toBe('partial')
    expect(partial.scene?.objects).toHaveLength(4)
    expect(partial.unresolved_entities?.[0].entity_id).toBe('bed_observed')
  })
  it.each([
    { status: 'ready' }, { scene: null }, { validation: null }, { unresolved_entities: [] }, { issues: [] },
    { dimensions_to_confirm: ['height_m'] },
  ])('rejects contradictory partial result %j', patch => {
    expect(() => parseSceneProposal({ ...partialRoom(), ...patch })).toThrow(RoomAnalysisError)
  })
  it('rejects duplicate unresolved IDs and a deferred observation appearing in the scene', () => {
    const partial = partialRoom(), ready = proposedRoom()
    expect(() => parseSceneProposal({ ...partial, unresolved_entities: [...partial.unresolved_entities!, ...partial.unresolved_entities!] })).toThrow()
    expect(() => parseSceneProposal({ ...partial, scene: ready.scene })).toThrow()
    const unresolved = structuredClone(partial.unresolved_entities!)
    unresolved[0].reason.entity_ids = ['chair_a']
    expect(() => parseSceneProposal({ ...partial, unresolved_entities: unresolved })).toThrow()
  })
})

describe('provisional reconstruction semantics', () => {
  it('accepts all retained objects when geometry passed but a relationship still needs correction', () => {
    const ready = proposedRoom()
    const issue = { code: 'fidelity' as const, entity_ids: ['bed_observed', 'window_observed'], message: 'Facing direction needs correction.' }
    const fidelity = { policy_version: 'reconstruction_v1' as const, major_object_coverage: 1, relationship_score: 0, proportion_score: null, color_score: 1, recognizable: false,
      checks: [{ kind: 'facing' as const, entity_ids: issue.entity_ids, passed: false, required: true, message: issue.message }], unassessed: [] }
    const preview = parseSceneProposal({ ...ready, status: 'partial', fidelity, unresolved_entities: [], issues: [issue] })
    expect(preview.scene?.objects).toEqual(ready.scene?.objects)
    expect(preview.unresolved_entities).toEqual([])
    expect(() => parseSceneProposal({ ...preview, status: 'ready', issues: [] })).toThrow()
    expect(() => parseSceneProposal({ ...preview, validation: { ...preview.validation, valid: false } })).toThrow()
  })
})
