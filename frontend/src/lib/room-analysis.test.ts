import { describe, expect, it } from 'vitest'
import { parseRoomAnalysis, parseSceneProposal, RoomAnalysisError } from './room-analysis'
import { analyzedRoom, proposedRoom, needsDimensions, partialRoom } from './test-support/room-analysis'

describe('analysis and proposal semantics', () => {
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
