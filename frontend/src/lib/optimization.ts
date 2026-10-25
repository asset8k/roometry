import Ajv2020 from 'ajv/dist/2020.js'
import addFormats from 'ajv-formats'
import schema from './optimization-run.schema.json'
import requestSchema from './optimization-request.schema.json'
import { AgentRunError, agentFailure } from './agent-run'
import type { AgentState } from './agent-run'
import { parseRoomScene } from './scene'
import type { OptimizationRun } from './optimization-run.generated'
import type { StartOptimizationRequest, OptimizationPreferences, Focus } from './optimization-request.generated'

export type { OptimizationRun, StartOptimizationRequest, OptimizationPreferences, Focus }
export const improvementLabels: Record<Focus, string> = {
  openness: 'Space & movement', storage: 'Storage & organization', workspace: 'Workspace', aesthetics: 'Aesthetics & cohesion',
}
const ajv = new Ajv2020({ strict: true, allErrors: true }); addFormats(ajv)
const valid = ajv.compile<OptimizationRun>(schema)
const validRequest = ajv.compile<StartOptimizationRequest>(requestSchema)
export function parseOptimization(data: unknown): OptimizationRun {
  if (data && typeof data === 'object') data = { instruction: null, parent_run_id: null, ...data }
  // Historical review responses predate the optional rendered rubric. Normalize
  // only that absent field before enforcing the complete serialized contract.
  if (data && typeof data === 'object' && Array.isArray((data as { attempts?: unknown }).attempts)) {
    const source = data as { attempts: { review?: Record<string, unknown> | null }[] }
    data = { ...source, attempts: source.attempts.map(attempt => attempt?.review && attempt.review.aesthetics === undefined
      ? { ...attempt, review: { ...attempt.review, aesthetics: null } } : attempt) }
  }
  if (!valid(data) || Date.parse(data.created_at) >= Date.parse(data.expires_at)
    || (data.baseline_assessment && data.baseline_assessment.scene_hash !== data.source_scene_hash)) {
    throw new AgentRunError('The service returned an unreadable improvement request.')
  }
  parseRoomScene(data.source_scene)
  if (data.result_scene) {
    parseRoomScene(data.result_scene)
    const source = data.source_scene
    if (JSON.stringify(data.result_scene.room) !== JSON.stringify(source.room)
      || JSON.stringify(data.result_scene.openings) !== JSON.stringify(source.openings)
      || JSON.stringify(data.result_scene.objects.map(o => o.id)) !== JSON.stringify(source.objects.map(o => o.id))
      || source.objects.some(old => {
        const changed = data.result_scene!.objects.find(o => o.id === old.id)!
        return ((old.fixed || old.locked) && JSON.stringify(old) !== JSON.stringify(changed))
          || JSON.stringify({ ...old, position: null, rotation_y_rad: null, color: null, material_colors: null }) !== JSON.stringify({ ...changed, position: null, rotation_y_rad: null, color: null, material_colors: null })
      })) throw new AgentRunError('The improvement changed protected room data.')
  }
  if ((data.status === 'completed' && (!data.result_scene || JSON.stringify(data.result_scene) === JSON.stringify(data.source_scene)))
    || (data.result_assessment && !data.result_scene)) throw new AgentRunError('The improvement result is inconsistent.')
  return structuredClone(data)
}
export function checkOptimization(run: OptimizationRun, source: AgentState, request: StartOptimizationRequest) {
  const preferences = request.preferences
  const samePreferences = run.preferences.style === (preferences?.style ?? null)
    && run.preferences.constraints === (preferences?.constraints ?? null)
    && (run.preferences.budget?.amount ?? null) === (preferences?.budget?.amount ?? null)
    && (run.preferences.budget?.currency ?? null) === (preferences?.budget?.currency ?? null)
  if (run.source_run_id !== source.run_id || run.input_id !== source.input_id || run.focus !== request.focus
    || !samePreferences
    || (run.instruction ?? null) !== (request.instruction ?? null) || (run.parent_run_id ?? null) !== (request.context?.parent_run_id ?? null)
    || JSON.stringify(run.source_scene) !== JSON.stringify(request.context?.scene ?? source.working_scene)
    || Date.parse(run.expires_at) > Date.parse(source.expires_at)
    || (!request.context && source.room_assessment && source.room_assessment.scene_hash !== run.source_scene_hash)) {
    throw new AgentRunError('This improvement request does not match your original room and chosen preferences.')
  }
}
const baseUrl = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/+$/, '')
async function request(path: string, signal: AbortSignal, body?: unknown) {
  const response = await fetch(`${baseUrl}/agent-runs${path}`, {
    method: body === undefined ? 'GET' : 'POST', credentials: 'include', cache: 'no-store',
    ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
    signal: AbortSignal.any([signal, AbortSignal.timeout(path.endsWith('/execute') ? 1800000 : 15000)]),
  })
  let data: unknown
  try { data = await response.json() } catch { throw new AgentRunError('The service response was interrupted. Retry to recover the same improvement request.') }
  if (!response.ok) throw agentFailure(data, response.status)
  return parseOptimization(data)
}
export const optimizationClient = {
  prepare: (sourceId: string, body: StartOptimizationRequest, signal: AbortSignal) => request(`/${encodeURIComponent(sourceId)}/optimizations`, signal, body),
  read: (sourceId: string, runId: string, signal: AbortSignal) => request(`/${encodeURIComponent(sourceId)}/optimizations/${encodeURIComponent(runId)}`, signal),
  execute: (sourceId: string, runId: string, signal: AbortSignal) => request(`/${encodeURIComponent(sourceId)}/optimizations/${encodeURIComponent(runId)}/execute`, signal, {}),
  cancel: (sourceId: string, runId: string, signal: AbortSignal) => request(`/${encodeURIComponent(sourceId)}/optimizations/${encodeURIComponent(runId)}/cancel`, signal, {}),
}
export type OptimizationClient = typeof optimizationClient
export interface ImprovementDraft { budget: string; currency: string; style: string; constraints: string }
export function improvementPreferences(draft: ImprovementDraft): OptimizationPreferences {
  const amount = draft.budget.trim() ? Number(draft.budget) : null
  if (amount !== null && (!Number.isFinite(amount) || amount < 0 || amount > 1000000)) throw new Error('Enter a budget from 0 to 1,000,000, or leave it blank.')
  const preferences: OptimizationPreferences = {
    budget: amount === null ? null : { amount, currency: draft.currency.trim().toUpperCase() },
    style: draft.style.trim() || null, constraints: draft.constraints.trim() || null,
  }
  if (!validRequest({ request_id: '77777777-7777-4777-8777-777777777777', focus: 'openness', preferences })) {
    throw new Error('Use a three-letter currency code and keep style and notes within their displayed limits.')
  }
  return preferences
}
