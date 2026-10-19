import Ajv2020 from 'ajv/dist/2020.js'
import addFormats from 'ajv-formats'
import analysisSchema from './room-analysis-response.schema.json'
import proposalSchema from './scene-proposal-response.schema.json'
import errorSchema from './room-analysis-error.schema.json'
import type { RoomAnalysisResponse } from './room-analysis-response.generated'
import type { SceneProposalResponse } from './scene-proposal-response.generated'
import type { AnalysisErrorResponse } from './room-analysis-error.generated'

const ajv = new Ajv2020({ strict: true, allErrors: true })
addFormats(ajv)
const validAnalysis = ajv.compile<RoomAnalysisResponse>(analysisSchema)
const validProposal = ajv.compile<SceneProposalResponse>(proposalSchema)
const validError = ajv.compile<AnalysisErrorResponse>(errorSchema)
export const dimensionAxes = ['width_m', 'length_m', 'height_m'] as const
export type DimensionAxis = typeof dimensionAxes[number]
export const dimensionLabels: Record<DimensionAxis, string> = { width_m: 'Width', length_m: 'Length', height_m: 'Height' }

export class RoomAnalysisError extends Error {
  constructor(message: string, readonly code = 'unexpected_response', readonly connected = true, readonly status = 0) {
    super(message)
    this.name = 'RoomAnalysisError'
  }
}

export function parseRoomAnalysis(data: unknown): RoomAnalysisResponse {
  if (!validAnalysis(data) || Date.parse(data.expires_at) <= Date.parse(data.created_at)
    || new Set(data.observations.image_ids).size !== data.observations.image_ids.length) {
    throw new RoomAnalysisError('The service returned an unexpected room analysis. Your photos are kept.')
  }
  return structuredClone(data)
}

export function parseSceneProposal(data: unknown): SceneProposalResponse {
  if (!validProposal(data)) throw new RoomAnalysisError('The service returned an unreadable scene proposal.')
  const { status, scene, validation, dimensions, dimensions_to_confirm: pending, estimated_axes: estimated, issues } = data
  const invalid = new Set(pending).size !== pending.length || new Set(estimated).size !== estimated.length
    || estimated.some(axis => dimensions[axis] == null)
    || (dimensions.measurements_source === 'user_confirmed' && estimated.length > 0)
    || (status === 'needs_dimensions' && !pending.length)
    || (status === 'incomplete' && !issues.length)
    || (validation !== null && validation.valid !== (validation.errors.length === 0))
    || (status !== 'ready' && scene !== null)
    || (status === 'ready' && (!scene || !validation?.valid || issues.length > 0 || pending.length > 0
      || dimensionAxes.some(axis => dimensions[axis] !== scene.room[axis])
      || dimensions.measurements_source !== scene.room.measurements_source
      || (estimated.length > 0) !== (dimensions.measurements_source === 'estimated')))
  if (invalid) throw new RoomAnalysisError('The service returned an inconsistent scene proposal. No new scene was opened.')
  return structuredClone(data)
}

export function analysisFailure(data: unknown, status: number): RoomAnalysisError {
  if (validError(data)) return new RoomAnalysisError(data.detail.map(issue => issue.message).join(' '), data.detail[0].code, true, status)
  return new RoomAnalysisError('The room service could not complete this request. Please try again.', 'unexpected_response', true, status)
}
