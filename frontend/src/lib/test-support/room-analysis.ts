import analysisFixture from '../../data/room_analysis.json'
import proposalFixture from '../../data/room_scene_proposal.json'
import { parseRoomAnalysis, parseSceneProposal } from '../room-analysis'
import { prepared, photo } from './room-input'
import type { PreparedRoomInput } from '../room-input-response.generated'
import type { SceneProposalResponse } from '../scene-proposal-response.generated'

const fixtureExpiry = Date.now() + 3600000

export function roomInput() {
  const input = prepared([photo(), photo('other.png')], fixtureExpiry)
  input.options = parseRoomAnalysis(analysisFixture).input_options
  return input
}
export function analyzedRoom(input: PreparedRoomInput = roomInput()) {
  const data = parseRoomAnalysis(analysisFixture)
  const evidence = input.images.map(image => image.image_id)
  const mapping = new Map(data.observations.image_ids.map((id, index) => [id, evidence[Math.min(index, evidence.length - 1)]]))
  data.input_id = input.input_id
  data.input_options = { ...data.input_options, ...input.options }
  data.created_at = new Date(Date.now() - 1000).toISOString()
  data.expires_at = input.expires_at
  const withEvidence = <T extends { source_image_ids: readonly string[] }>(entities: T[]) => entities.map(entity => ({
    ...entity, source_image_ids: [...new Set(entity.source_image_ids.map(id => mapping.get(id)!))],
  }))
  return parseRoomAnalysis({ ...data, observations: { ...data.observations, image_ids: evidence,
    objects: withEvidence(data.observations.objects), openings: withEvidence(data.observations.openings), walls: withEvidence(data.observations.walls),
  } })
}
export function proposedRoom(input: PreparedRoomInput = roomInput()): SceneProposalResponse {
  return parseSceneProposal({ ...structuredClone(proposalFixture), input_id: input.input_id, expires_at: input.expires_at })
}
export function needsDimensions(input: PreparedRoomInput = roomInput()): SceneProposalResponse {
  return parseSceneProposal({ ...proposedRoom(input), status: 'needs_dimensions', scene: null, validation: null,
    dimensions: { width_m: 4, length_m: 4, height_m: null, measurements_source: 'user_confirmed' }, dimensions_to_confirm: ['height_m'] })
}
