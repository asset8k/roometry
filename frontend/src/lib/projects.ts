import type { RoomScene } from './scene.generated'
import { canonicalJson } from './canonical-json'

export interface SavedMessage { id: string; role: 'user' | 'assistant'; text: string }
export interface SaveSnapshot {
  has_editable_copy?: boolean
  improvement_run_ids: string[]
  manual_copies: { parent_run_id: string | null; scene: RoomScene }[]
  view: 'original' | 'improved' | 'editing'
  editing_parent_run_id: string | null
  selected_improvement_id: string | null
}
export interface SaveProjectRequest extends SaveSnapshot {
  request_id: string; source_run_id: string; title: string
  project_id: string | null; expected_revision: number; messages: SavedMessage[]
}
export interface SavedProject {
  id: string; title: string; revision: number; image_count: number; version_count: number
  saved_at: string; visibility: 'private'
}
export class ProjectError extends Error {
  constructor(message: string, readonly status = 0) { super(message) }
}
const baseUrl = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/+$/, '')
export function parseSavedProject(value: unknown): SavedProject {
  const data = value as Partial<SavedProject> | null
  if (!data || typeof data !== 'object' || typeof data.id !== 'string' || !/^[a-f0-9-]{36}$/.test(data.id)
    || typeof data.title !== 'string' || !data.title.trim() || data.title.length > 120 || data.visibility !== 'private'
    || !Number.isInteger(data.revision) || data.revision! < 1 || !Number.isInteger(data.image_count) || data.image_count! < 1
    || !Number.isInteger(data.version_count) || data.version_count! < 2 || typeof data.saved_at !== 'string' || !Number.isFinite(Date.parse(data.saved_at))) {
    throw new ProjectError('Couldn’t confirm the save. Retry the same save to check it.')
  }
  return data as SavedProject
}
export async function saveProject(body: SaveProjectRequest, signal: AbortSignal): Promise<SavedProject> {
  const response = await fetch(`${baseUrl}/projects`, { method: 'POST', credentials: 'include', cache: 'no-store',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: AbortSignal.any([signal, AbortSignal.timeout(60000)]) })
  let data: unknown
  try { data = await response.json() } catch { throw new ProjectError('Couldn’t confirm the save. Retry the same save to check it.', response.ok ? 0 : response.status) }
  if (!response.ok) {
    const message = (data as { detail?: { message?: unknown }[] })?.detail?.[0]?.message
    throw new ProjectError(typeof message === 'string' && message.length <= 500 ? message : 'Your room could not be saved. Your workspace is kept.', response.status)
  }
  return parseSavedProject(data)
}

interface SaveState { owner: string | null; saved: SavedProject | null; pending: boolean; retry: boolean; error: string | null }
interface Entry { saved: SavedProject | null; request: SaveProjectRequest | null; signature?: string }
export function savedWorkspaceSignature(sourceRunId: string, snapshot: SaveSnapshot, messages: SavedMessage[]) {
  return canonicalJson({ source_run_id: sourceRunId, manual_copies: snapshot.manual_copies, improvement_run_ids: snapshot.improvement_run_ids,
    view: snapshot.view, has_editable_copy: snapshot.has_editable_copy ?? false, editing_parent_run_id: snapshot.editing_parent_run_id, selected_improvement_id: snapshot.selected_improvement_id, messages })
}
/** Per-account save receipts; retries use a frozen request and never regenerate a room. */
export class ProjectSaveController {
  private entries = new Map<string, Entry>()
  private state: SaveState = { owner: null, saved: null, pending: false, retry: false, error: null }
  private active: AbortController | null = null
  private listeners = new Set<() => void>()
  constructor(private client = saveProject) {}
  getSnapshot = () => this.state
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener) } }
  confirmedSignature = () => this.state.owner ? this.entries.get(this.state.owner)?.signature : undefined
  private update(patch: Partial<SaveState>) { this.state = { ...this.state, ...patch }; this.listeners.forEach(listener => listener()) }
  restoreSaved = (owner: string, saved: SavedProject) => {
    if (!this.entries.has(owner)) this.entries.set(owner, { saved, request: null })
  }
  setOwner = (owner: string | null) => {
    if (owner === this.state.owner) return
    this.active?.abort(); this.active = null
    const entry = owner ? this.entries.get(owner) : null
    this.update({ owner, saved: entry?.saved ?? null, pending: false, retry: !!entry?.request, error: null })
  }
  save = async (sourceRunId: string, title: string, snapshot: () => SaveSnapshot, messages: SavedMessage[]) => {
    const owner = this.state.owner
    if (!owner || this.state.pending) return false
    const entry = this.entries.get(owner) ?? { saved: null, request: null }
    this.entries.set(owner, entry)
    try {
      entry.request ??= { ...snapshot(), request_id: crypto.randomUUID(), source_run_id: sourceRunId, title: title.trim(),
        project_id: entry.saved?.id ?? null, expected_revision: entry.saved?.revision ?? 0, messages: structuredClone(messages) }
    } catch (error) { this.update({ error: (error as Error).message }); return false }
    const abort = new AbortController(); this.active = abort
    this.update({ pending: true, error: null })
    try {
      const saved = await this.client(entry.request, abort.signal)
      if (abort.signal.aborted || this.state.owner !== owner) return false
      entry.signature = savedWorkspaceSignature(entry.request.source_run_id, entry.request, entry.request.messages)
      entry.saved = saved; entry.request = null
      this.update({ saved, pending: false, retry: false }); return true
    } catch (error) {
      if (abort.signal.aborted || this.state.owner !== owner) return false
      const rejected = error instanceof ProjectError && [400, 401, 403, 404, 409, 413, 422].includes(error.status)
      if (rejected) entry.request = null
      this.update({ pending: false, retry: !rejected, error: error instanceof ProjectError ? error.message : 'Couldn’t confirm the save. Retry the same save to check it.' })
      return false
    } finally { if (this.active === abort) this.active = null }
  }
  release = () => { this.active?.abort(); this.active = null }
}

import { parseAgentRun } from './agent-run'
import type { AgentState } from './agent-run'
import { parseRoomScene } from './scene'
import { parseOptimization } from './optimization'
import type { OptimizationRun } from './optimization'
export interface ProjectSummary { id: string; title: string; revision: number; room_type: string; updated_at: string; visibility: 'private' }
export interface RecentRun { id: string; status: string; room_type: string; expires_at: string }
export interface OpenedProject {
  project: { id: string; title: string; revision: number; visibility: 'private'; images: { id: string; url: string }[];
    versions: { id: string; scene: RoomScene }[]; snapshot: { messages: SavedMessage[];
      improvements: Record<string, OptimizationRun>; versions: { key: string; parent_key: string | null; version_id: string }[];
      comparison: Pick<SaveSnapshot, 'view' | 'editing_parent_run_id' | 'selected_improvement_id' | 'has_editable_copy'> } }
  run: AgentState
}
export async function projectRequest(path: string, signal: AbortSignal, method = 'GET', body?: object): Promise<unknown> {
  const response = await fetch(`${baseUrl}/projects${path}`, { method, credentials: 'include', cache: 'no-store',
    signal: AbortSignal.any([signal, AbortSignal.timeout(20000)]), ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}) })
  if (response.status === 204) return null
  let data: unknown
  try { data = await response.json() } catch { throw new ProjectError('Couldn’t read the saved room response. Please retry.') }
  if (!response.ok) {
    const message = (data as { detail?: { message?: unknown }[] })?.detail?.[0]?.message
    throw new ProjectError(typeof message === 'string' ? message : 'This saved room is unavailable.', response.status)
  }
  return data
}
export function parseProjectSummary(value: unknown): ProjectSummary {
  const item = value as ProjectSummary
  if (!item || typeof item.id !== 'string' || !/^[a-f0-9-]{36}$/.test(item.id) || typeof item.title !== 'string'
    || !item.title.trim() || !Number.isInteger(item.revision) || item.revision < 1 || item.visibility !== 'private'
    || typeof item.room_type !== 'string' || !Number.isFinite(Date.parse(item.updated_at))) throw new ProjectError('Couldn’t read the saved rooms.')
  return item
}
export function parseOpenedProject(value: unknown): OpenedProject {
  const data = value as OpenedProject
  if (!data?.project || data.project.visibility !== 'private' || typeof data.project.id !== 'string' || typeof data.project.title !== 'string'
    || !Number.isInteger(data.project.revision) || !Array.isArray(data.project.versions) || !Array.isArray(data.project.images)
    || !data.project.snapshot || !Array.isArray(data.project.snapshot.messages) || !Array.isArray(data.project.snapshot.versions)) throw new ProjectError('Couldn’t read this saved room.')
  const run = parseAgentRun(data.run)
  if (!run.working_scene) throw new ProjectError('This room has no saved reconstruction.')
  const versions = data.project.versions.map(item => ({ ...item, scene: parseRoomScene(item.scene) }))
  const ids = new Set(versions.map(item => item.id))
  if (data.project.snapshot.versions.some(item => !ids.has(item.version_id))) throw new ProjectError('A saved scene version is unavailable.')
  const improvements = Object.fromEntries(Object.entries(data.project.snapshot.improvements).map(([key, item]) => [key, parseOptimization(item)]))
  const snapshot = data.project.snapshot
  if (!['original', 'improved', 'editing'].includes(snapshot.comparison.view)
    || (snapshot.comparison.has_editable_copy !== undefined && typeof snapshot.comparison.has_editable_copy !== 'boolean')
    || snapshot.messages.some(item => !item || !['user', 'assistant'].includes(item.role) || typeof item.text !== 'string' || typeof item.id !== 'string')) throw new ProjectError('The saved workspace is incomplete.')
  const keys = new Set(snapshot.versions.map(item => item.key))
  const active = snapshot.comparison.view === 'original' ? 'original' : snapshot.comparison.view === 'improved' ? snapshot.comparison.selected_improvement_id : `manual:${snapshot.comparison.editing_parent_run_id ?? 'original'}`
  if (!active || !keys.has(active)) throw new ProjectError('The saved comparison is unavailable.')
  return { project: { ...data.project, versions, snapshot: { ...snapshot, improvements } }, run }
}
export const projectsClient = {
  list: async (signal: AbortSignal, offset = 0) => {
    const value = await projectRequest(`?offset=${offset}`, signal)
    if (!Array.isArray(value)) throw new ProjectError('Couldn’t read saved rooms.')
    return value.map(parseProjectSummary)
  },
  recent: async (signal: AbortSignal): Promise<RecentRun[]> => {
    const value = await projectRequest('/runs', signal)
    if (!Array.isArray(value) || value.some(item => typeof item.id !== 'string' || typeof item.status !== 'string' || typeof item.room_type !== 'string')) throw new ProjectError('Couldn’t read recoverable runs.')
    return value
  },
  open: async (id: string, signal: AbortSignal) => parseOpenedProject(await projectRequest(`/${encodeURIComponent(id)}/open`, signal, 'POST', {})),
  rename: (room: ProjectSummary, title: string, signal: AbortSignal) => projectRequest(`/${room.id}`, signal, 'PATCH', { title, expected_revision: room.revision }),
  delete: (room: ProjectSummary, signal: AbortSignal) => projectRequest(`/${room.id}`, signal, 'DELETE', { expected_revision: room.revision }),
}
