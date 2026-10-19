import Ajv2020 from 'ajv/dist/2020.js'
import addFormats from 'ajv-formats'
import { describe, expect, it } from 'vitest'
import requestSchema from './scene-proposal-request.schema.json'
import responseSchema from './scene-proposal-response.schema.json'
import fixture from '../data/room_scene_proposal.json'
import type { SceneProposalRequest } from './scene-proposal-request.generated'
import type { SceneProposalResponse } from './scene-proposal-response.generated'

const ajv = new Ajv2020({ strict: true, allErrors: true })
addFormats(ajv)
const validRequest = ajv.compile<SceneProposalRequest>(requestSchema)
const validResponse = ajv.compile<SceneProposalResponse>(responseSchema)

describe('shared scene-proposal contracts', () => {
  it('accepts a validated synthetic base scene with explicit estimate provenance', () => {
    if (!validResponse(fixture)) throw new Error(ajv.errorsText(validResponse.errors))
    const proposal: SceneProposalResponse = fixture
    expect(proposal.status).toBe('ready')
    expect(proposal.validation?.valid).toBe(true)
    expect(proposal.scene?.room.measurements_source).toBe('user_confirmed')
    expect(proposal.scene?.objects.every(object => object.measurements_source === 'estimated')).toBe(true)
  })

  it('accepts partial confirmation without allowing client-supplied scene authority', () => {
    const request: SceneProposalRequest = {
      analysis_id: fixture.analysis_id,
      dimensions: { height_m: 2.6, measurements_source: 'user_confirmed' },
    }
    expect(validRequest(request)).toBe(true)
    expect(validRequest({ analysis_id: fixture.analysis_id })).toBe(true)
    for (const patch of [{ scene: {} }, { image_urls: [] }, { allow_estimated_dimensions: 'true' }]) {
      expect(validRequest({ ...request, ...patch })).toBe(false)
    }
  })

  it('allows honest pending and incomplete results without a scene', () => {
    expect(validResponse({ ...fixture, status: 'needs_dimensions', dimensions_to_confirm: ['height_m'], scene: null, validation: null })).toBe(true)
    expect(validResponse({ ...fixture, status: 'incomplete', scene: null, validation: null,
      issues: [{ code: 'unknown_object', entity_ids: ['unclassified_item'], message: 'Needs an approved category.' }] })).toBe(true)
    expect(validResponse({ ...fixture, status: 'optimized' })).toBe(false)
  })

  // Python additionally verifies response-state consistency, identity, authority,
  // support relationships, measurement provenance, and baseline geometry.
})
