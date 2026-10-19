import Ajv2020 from 'ajv/dist/2020.js'
import addFormats from 'ajv-formats'
import { describe, expect, it } from 'vitest'
import requestSchema from './room-analysis-request.schema.json'
import observationsSchema from './room-observations.schema.json'
import responseSchema from './room-analysis-response.schema.json'
import fixture from '../data/room_analysis.json'
import type { AnalyzeRoomRequest } from './room-analysis-request.generated'
import type { RoomObservations } from './room-observations.generated'
import type { RoomAnalysisResponse } from './room-analysis-response.generated'

const ajv = new Ajv2020({ strict: true, allErrors: true })
addFormats(ajv)
const validRequest = ajv.compile<AnalyzeRoomRequest>(requestSchema)
const validObservations = ajv.compile<RoomObservations>(observationsSchema)
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
    expect(validObservations(response.observations)).toBe(true)
  })

  it('accepts only an opaque input ID for analysis requests', () => {
    const request: AnalyzeRoomRequest = { input_id: fixture.input_id }
    expect(validRequest(request)).toBe(true)
    for (const invalid of [{}, { input_id: '../private' }, { ...request, options: {} }, { ...request, image_urls: ['https://example.com/photo'] }]) {
      expect(validRequest(invalid)).toBe(false)
    }
  })

  it('rejects missing nullable fields, scene authority, private handles and malformed times', () => {
    const observations = fixture.observations
    const { ceiling_visible: _unknown, ...missingUnknown } = observations
    expect(validObservations(missingUnknown)).toBe(false)
    for (const extra of [{ position: { x: 1, y: 0, z: 1 } }, { asset_id: 'bed_standard_01' }, { locked: true }, { storage_key: 'private/photo.jpg' }]) {
      expect(validObservations({ ...observations, objects: [{ ...observations.objects[0], ...extra }] })).toBe(false)
    }
    for (const patch of [{ expires_at: '2026-02-30T10:00:00Z' }, { status: 'ready' }, { scene: {} }, { owner_scope: 'private' }]) {
      expect(validResponse({ ...fixture, ...patch })).toBe(false)
    }
  })

  // Reference integrity, support cycles, duplicate pairs and timestamp ordering
  // are backend rules; generated JSON Schema checks shape, not physical truth.
})
