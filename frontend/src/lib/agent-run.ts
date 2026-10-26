import { canonicalJson } from './canonical-json'
import Ajv2020 from 'ajv/dist/2020.js'
import addFormats from 'ajv-formats'
import schema from './agent-run.schema.json'
import errorSchema from './agent-error.schema.json'
import type { AgentState } from './agent-run.generated'
import type { RunErrorResponse } from './agent-error.generated'
import type { PreparedRoomInput } from './room-input-response.generated'
import { parseRoomAnalysis, parseSceneProposal, dimensionAxes } from './room-analysis'
import { parseRoomScene } from './scene'

export type { AgentState } from './agent-run.generated'
export type { ClarificationAnswer } from './agent-answer.generated'
export type { StartRunRequest } from './agent-start.generated'

const ajv = new Ajv2020({ strict: true, allErrors: true })
addFormats(ajv)
const valid = ajv.compile<AgentState>(schema)
const validError = ajv.compile<RunErrorResponse>(errorSchema)
type Assessment = NonNullable<AgentState['room_assessment']>
const validAssessment = ajv.compile<Assessment>({ $ref: '#/$defs/RoomAssessment', $defs: schema.$defs })
function coherentAssessment(value: unknown): value is Assessment {
  if (!validAssessment(value)) return false
  const keys = ['openness', 'storage', 'workspace', 'aesthetics'] as const
  if (keys.some(key => (value[key].status === 'rated') !== (value[key].score !== null))) return false
  const eligible = keys.filter(key => value[key].status === 'rated' && value[key].confidence !== 'low' && value[key].score! < 8)
  const recommended = eligible.reduce<typeof keys[number] | null>((best, key) => best === null || value[key].score! < value[best].score! ? key : best, null)
  return value.recommended === recommended
}
export const activeRun = (run: AgentState) => run.status === 'queued' || run.status === 'running'

export class AgentRunError extends Error {
  constructor(message: string, readonly status = 0, readonly code = 'unexpected_response') { super(message) }
}

/** Validate the server contract and cross-references before opening a viewer. */
export function parseAgentRun(data: unknown): AgentState {
  // Optional assessment metadata cannot hide a valid reconstruction. Historical
  // responses without cards are also readable; the UI explains their absence.
  if (data && typeof data === 'object' && !Array.isArray(data)) {
    const source = data as Record<string, unknown>
    if (source.persistence === undefined) data = { ...source, persistence: 'temporary' }
    if (source.room_assessment === undefined || (source.room_assessment !== null && !coherentAssessment(source.room_assessment))) {
      data = { ...(data as Record<string, unknown>), room_assessment: null }
    }
  }
  if (!valid(data)) throw new AgentRunError('The service returned an unreadable generation status.')
  if (Date.parse(data.created_at) >= Date.parse(data.expires_at) || Date.parse(data.expires_at) > Date.parse(data.input_expires_at)
    || new Set(data.image_ids).size !== data.image_ids.length) throw new AgentRunError('The generation references are inconsistent.')
  if (data.analysis) {
    parseRoomAnalysis(data.analysis)
    if (data.analysis.input_id !== data.input_id || data.analysis.expires_at !== data.input_expires_at
      || canonicalJson(data.analysis.observations.image_ids) !== canonicalJson(data.image_ids)) throw new AgentRunError('The analysis belongs to a different room.')
  }
  if (data.proposal) {
    parseSceneProposal(data.proposal)
    if (!data.analysis || data.proposal.analysis_id !== data.analysis.analysis_id || data.proposal.input_id !== data.input_id) throw new AgentRunError('The scene belongs to a different analysis.')
  }
  const scene = data.working_scene
  if (Boolean(scene) !== Boolean(data.base_scene) || (data.status === 'completed' && data.proposal?.status !== 'ready')
    || (data.status === 'partial' && data.proposal?.status !== 'partial')) throw new AgentRunError('The generation result is inconsistent.')
  if (scene && data.base_scene) {
    parseRoomScene(scene); parseRoomScene(data.base_scene)
    if (!data.proposal?.scene || !data.proposal.validation?.valid
      || canonicalJson(scene.room) !== canonicalJson(data.base_scene.room)
      || canonicalJson(scene.objects.map(obj => obj.id)) !== canonicalJson(data.base_scene.objects.map(obj => obj.id))
      || dimensionAxes.some(axis => scene.room[axis] !== data.proposal?.dimensions[axis]
        || (data.confirmed_dimensions?.[axis] != null && scene.room[axis] !== data.confirmed_dimensions[axis]))) throw new AgentRunError('The scene changed room identity or supplied measurements.')
    const expected = [...(data.analysis?.observations.objects ?? []), ...(data.analysis?.observations.openings ?? [])].map(item => item.id)
    const accounted = [...scene.objects, ...scene.openings].map(item => item.id).concat((data.proposal.unresolved_entities ?? []).map(item => item.entity_id))
    if (expected.length !== accounted.length || new Set(accounted).size !== accounted.length || expected.some(id => !accounted.includes(id))) throw new AgentRunError('Some observations are missing from the scene result.')
  }
  if (data.pending_question) {
    const q = data.pending_question
    if (data.status !== 'awaiting_answer' || new Set(q.choices.map(choice => choice.id)).size !== 3
      || data.submitted_answers?.[q.id] || !data.questions?.some(item => canonicalJson(item) === canonicalJson(q))) throw new AgentRunError('The pending question is inconsistent.')
  } else if (data.status === 'awaiting_answer') throw new AgentRunError('The generation is missing its question.')
  return structuredClone(data)
}

export function checkRunInput(run: AgentState, input: PreparedRoomInput, runId?: string) {
  if (run.input_id !== input.input_id || (runId && run.run_id !== runId)
    || Date.parse(run.input_expires_at) !== Date.parse(input.expires_at)
    || canonicalJson(run.image_ids) !== canonicalJson(input.images.map(image => image.image_id))
    || dimensionAxes.some(axis => input.options.dimensions?.measurements_source === 'user_confirmed'
      && input.options.dimensions[axis] != null && run.confirmed_dimensions?.[axis] !== input.options.dimensions[axis])) throw new AgentRunError('This generation does not match your uploaded room.')
}

export function agentFailure(data: unknown, status: number): AgentRunError {
  return validError(data) && data.detail.length
    ? new AgentRunError(data.detail[0].message, status, data.detail[0].code)
    : new AgentRunError('The generation service could not complete this request.', status)
}

/** Concurrent GET and POST responses may arrive out of order. */
export function newerRun(next: AgentState, previous: AgentState | null): boolean {
  if (!previous) return true
  const rank = (run: AgentState) => [Object.keys(run.submitted_answers ?? {}).length, run.steps_used, run.usage.model_calls, run.usage.tool_calls,
    activeRun(run) ? 0 : run.status === 'awaiting_answer' ? 1 : 2]
  const a = rank(next), b = rank(previous)
  for (let i = 0; i < a.length; i++) { if (a[i] !== b[i]) return a[i] > b[i] }
  return true
}
