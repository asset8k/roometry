import Ajv2020 from 'ajv/dist/2020.js'
import addFormats from 'ajv-formats'
import { describe, expect, it } from 'vitest'
import responseSchema from './room-analysis-response.schema.json'
import fixture from './test-support/fixtures/room_analysis.json'
import type { RoomAnalysisResponse } from './room-analysis-response.generated'

const ajv = new Ajv2020({ strict: true, allErrors: true })
addFormats(ajv)
const validResponse = ajv.compile<RoomAnalysisResponse>(responseSchema)

describe('shared room-analysis contracts', () => {
  it('accepts the synthetic response with explicit unknowns and unresolved duplicates', () => {
    if (!validResponse(fixture)) throw new Error(ajv.errorsText(validResponse.errors))
    const response: RoomAnalysisResponse = fixture
    expect(response.status).toBe('analyzed')
    expect(response.observations.estimated_dimensions).toBeNull()
    expect(response.input_options.dimensions?.height_m).toBeNull()
    expect(response.observations.objects.filter(object => object.type === 'chair')).toHaveLength(2)
    expect(response.observations.possible_duplicates).toHaveLength(1)
  })

  it('rejects missing nullable fields, scene authority, private handles and malformed times', () => {
    const observations = fixture.observations
    const { ceiling_visible: _unknown, ...missingUnknown } = observations
    expect(validResponse({ ...fixture, observations: missingUnknown })).toBe(false)
    for (const extra of [{ position: { x: 1, y: 0, z: 1 } }, { asset_id: 'bed_standard_01' }, { locked: true }, { storage_key: 'private/photo.jpg' }]) {
      expect(validResponse({ ...fixture, observations: { ...observations, objects: [{ ...observations.objects[0], ...extra }] } })).toBe(false)
    }
    for (const patch of [{ expires_at: '2026-02-30T10:00:00Z' }, { status: 'ready' }, { scene: {} }, { owner_scope: 'private' }]) {
      expect(validResponse({ ...fixture, ...patch })).toBe(false)
    }
  })

  // Reference integrity, support cycles, duplicate pairs and timestamp ordering
  // are backend rules; generated JSON Schema checks shape, not physical truth.
})
