import { canonicalJson } from './canonical-json'
import Ajv2020 from 'ajv/dist/2020.js'
import addFormats from 'ajv-formats'
import schema from './optimization-run.schema.json'
import requestSchema from './optimization-request.schema.json'
import { AgentRunError, agentFailure } from './agent-run'
import type { AgentState } from './agent-run'
import { parseRoomScene } from './scene'
import { dimensionsFor, getCatalogAsset } from '../components/room-viewer/asset-catalog'
import type { OptimizationRun } from './optimization-run.generated'
import type { StartOptimizationRequest, OptimizationPreferences, Focus } from './optimization-request.generated'
import { refreshAllowance } from './quota'

export type { OptimizationRun, StartOptimizationRequest, OptimizationPreferences, Focus }
export const improvementLabels: Record<Focus, string> = {
  openness: 'Space & movement', storage: 'Storage & organization', workspace: 'Workspace', aesthetics: 'Aesthetics & cohesion',
}
const ajv = new Ajv2020({ strict: true, allErrors: true }); addFormats(ajv)
const valid = ajv.compile<OptimizationRun>(schema)
const validRequest = ajv.compile<StartOptimizationRequest>(requestSchema)
export function parseOptimization(data: unknown): OptimizationRun {
  if (data && typeof data === 'object') data = { instruction: null, parent_run_id: null, ...data }
  // Saved runs may predate repair proposals and the rendered review rubric.
  // Fill only absent optional fields before enforcing the full contract.
  if (data && typeof data === 'object' && Array.isArray((data as { attempts?: unknown }).attempts)) {
    const source = data as { attempts: { review?: Record<string, unknown> | null }[] }
    data = { ...source, attempts: source.attempts.map(attempt => attempt && typeof attempt === 'object' ? {
      proposal: null, ...attempt,
      ...(attempt.review && attempt.review.aesthetics === undefined ? { review: { ...attempt.review, aesthetics: null } } : {}),
    } : attempt) }
  }
  if (!valid(data) || Date.parse(data.created_at) >= Date.parse(data.expires_at)
    || (data.baseline_assessment && data.baseline_assessment.scene_hash !== data.source_scene_hash)) {
    throw new AgentRunError('The service returned an unreadable improvement request.')
  }
  parseRoomScene(data.source_scene)
  if (data.result_scene) {
    parseRoomScene(data.result_scene)
    const source = data.source_scene
    if (canonicalJson(data.result_scene.room) !== canonicalJson(source.room)
      || canonicalJson(data.result_scene.openings) !== canonicalJson(source.openings)
      || source.objects.some(old => (old.fixed || old.locked)
        && canonicalJson(old) !== canonicalJson(data.result_scene!.objects.find(o => o.id === old.id)))
      || data.result_scene.objects.some(object => {
        const old = source.objects.find(o => o.id === object.id)
        const immutable = (item: typeof object) => ({ ...item, position: null, rotation_y_rad: null, color: null, material_colors: null, support_surface_id: null })
        if (old && canonicalJson(immutable(old)) === canonicalJson(immutable(object))) return false
        const asset = getCatalogAsset(object.asset_id)
        if (!asset || !asset.placements.includes(object.placement) || !['floor', 'wall', 'surface'].includes(object.placement) || object.fixed || object.locked
          || object.provenance !== 'agent_added' || object.measurements_source !== 'catalog'
          || (old && (old.placement !== 'floor' || object.placement !== 'floor' || old.type !== object.type))) return true
        const [width, height, depth] = dimensionsFor(asset, object.placement)
        return object.dimensions_m.width !== width || object.dimensions_m.height !== height || object.dimensions_m.depth !== depth
      })) throw new AgentRunError('The improvement changed protected room data.')
  }
  if ((data.status === 'completed' && (!data.result_scene || canonicalJson(data.result_scene) === canonicalJson(data.source_scene)))
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
    || canonicalJson(run.source_scene) !== canonicalJson(request.context?.scene ?? source.working_scene)
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
  if (body !== undefined) refreshAllowance()
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
export interface ImprovementDraft { style: string; constraints: string }
export function improvementPreferences(draft: ImprovementDraft): OptimizationPreferences {
  const preferences: OptimizationPreferences = {
    budget: null,
    style: draft.style.trim() || null, constraints: draft.constraints.trim() || null,
  }
  if (!validRequest({ request_id: '77777777-7777-4777-8777-777777777777', focus: 'openness', preferences })) {
    throw new Error('Keep style and notes within their displayed limits.')
  }
  return preferences
}
