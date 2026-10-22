import Ajv2020 from 'ajv/dist/2020.js'
import addFormats from 'ajv-formats'
import analysisSchema from './room-analysis-response.schema.json'
import proposalSchema from './scene-proposal-response.schema.json'
import type { RoomAnalysisResponse } from './room-analysis-response.generated'
import type { SceneProposalResponse } from './scene-proposal-response.generated'

const ajv = new Ajv2020({ strict: true, allErrors: true })
addFormats(ajv)
const validAnalysis = ajv.compile<RoomAnalysisResponse>(analysisSchema)
const validProposal = ajv.compile<SceneProposalResponse>(proposalSchema)
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
  const unresolved = data.unresolved_entities ?? []
  const unresolvedIds = new Set(unresolved.map(item => item.entity_id))
  const retained = [...(scene?.objects ?? []), ...(scene?.openings ?? [])].map(item => item.id)
  const renderable = status === 'ready' || status === 'partial'
  const invalid = new Set(pending).size !== pending.length || new Set(estimated).size !== estimated.length
    || estimated.some(axis => dimensions[axis] == null)
    || (dimensions.measurements_source === 'user_confirmed' && estimated.length > 0)
    || (status === 'needs_dimensions' && !pending.length)
    || (status === 'needs_wall_assignment' && (data.wall_axes?.status !== 'needs_clarification' || pending.length > 0 || unresolved.length > 0))
    || (status === 'incomplete' && !issues.length)
    || (validation !== null && validation.valid !== (validation.errors.length === 0))
    || unresolvedIds.size !== unresolved.length || new Set(retained).size !== retained.length
    || unresolved.some(item => !item.reason.entity_ids.includes(item.entity_id))
    || retained.some(id => unresolvedIds.has(id))
    || (status === 'ready' && (issues.length > 0 || unresolved.length > 0))
    || (status === 'partial' && (!retained.length || !issues.length || (!unresolved.length && !issues.some(issue => issue.code === 'fidelity'))))
    || (status === 'ready' && data.fidelity?.checks.some(check => check.required && !check.passed))
    || (status === 'needs_dimensions' && unresolved.length > 0)
    || (!renderable && scene !== null)
    || (renderable && (!scene || !validation?.valid || pending.length > 0
      || dimensionAxes.some(axis => dimensions[axis] !== scene.room[axis])
      || dimensions.measurements_source !== scene.room.measurements_source
      || (estimated.length > 0) !== (dimensions.measurements_source === 'estimated')))
  if (invalid) throw new RoomAnalysisError('The service returned an inconsistent scene proposal. No new scene was opened.')
  return structuredClone({ ...data, unresolved_entities: unresolved })
}
