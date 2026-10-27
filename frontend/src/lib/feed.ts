import { parseRoomScene } from './scene'
import type { RoomScene } from './scene.generated'
import { projectRequest, ProjectError } from './projects'

export interface Publication { active: boolean; id: string; revision: number; version_id: string | null; title: string; summary: string }
export interface SharingOptions { project_id: string; revision: number; title: string; versions: { id: string; label: string; preview_url: string }[]; selected_version_id: string | null; publication: Publication | null }
export interface FeedCard { id: string; title: string; summary: string; room_type: string; object_count: number; published_at: string; preview_url: string }
export interface SharedRoom extends FeedCard { scene: RoomScene }
export interface FeedPage { items: FeedCard[]; next_cursor: string | null }
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/
const base = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/+$/, '')
export const apiAsset = (path: string) => `${base}${path.slice(4)}`

export function parsePublication(value: unknown): Publication {
  const row = value as Publication
  if (!row || typeof row.active !== 'boolean' || !uuid.test(row.id) || !Number.isInteger(row.revision) || row.revision < 1
    || typeof row.title !== 'string' || typeof row.summary !== 'string' || (row.active && (!row.version_id || !uuid.test(row.version_id)))) throw new ProjectError('Couldn’t read sharing status.')
  return { active: row.active, id: row.id, revision: row.revision, version_id: row.version_id, title: row.title, summary: row.summary }
}
export function parseSharingOptions(value: unknown): SharingOptions | null {
  if (value === null) return null
  const row = value as SharingOptions
  if (!row || !uuid.test(row.project_id) || !Number.isInteger(row.revision) || row.revision < 1 || typeof row.title !== 'string' || !Array.isArray(row.versions)
    || row.versions.some(v => !uuid.test(v.id) || typeof v.label !== 'string' || v.preview_url !== `/api/projects/${row.project_id}/versions/${v.id}/preview`)
    || (row.selected_version_id !== null && !row.versions.some(v => v.id === row.selected_version_id))) throw new ProjectError('Couldn’t read saved layouts for sharing.')
  return { ...row, publication: row.publication ? parsePublication(row.publication) : null }
}
export function parseFeedCard(value: unknown): FeedCard {
  const row = value as FeedCard
  if (!row || !uuid.test(row.id) || typeof row.title !== 'string' || !row.title.trim() || row.title.length > 80
    || typeof row.summary !== 'string' || row.summary.length > 240 || typeof row.room_type !== 'string'
    || !Number.isInteger(row.object_count) || row.object_count < 0 || row.object_count > 200
    || !Number.isFinite(Date.parse(row.published_at)) || row.preview_url !== `/api/feed/${row.id}/preview`) throw new ProjectError('Couldn’t read this shared room.')
  return { id: row.id, title: row.title, summary: row.summary, room_type: row.room_type, object_count: row.object_count, published_at: row.published_at, preview_url: row.preview_url }
}
export function parseSharedRoom(value: unknown): SharedRoom {
  const card = parseFeedCard(value), scene = parseRoomScene((value as SharedRoom).scene)
  if ([scene.assumptions, scene.warnings, scene.wall_mapping, scene.wall_axis_bindings, scene.estimate_corrections, scene.interpretation_corrections, scene.appearance_corrections].some(list => list?.length)
    || scene.objects.some(o => !/^object_\d+$/.test(o.id) || !o.fixed || !o.locked)
    || scene.openings.some(o => !/^opening_\d+$/.test(o.id)) || scene.objects.length !== card.object_count || scene.room.room_type !== card.room_type) throw new ProjectError('The shared room contains unavailable data.')
  return { ...card, scene }
}
async function publicRequest(path: string, signal: AbortSignal) {
  const response = await fetch(`${base}/feed${path}`, { credentials: 'omit', cache: 'no-store', signal: AbortSignal.any([signal, AbortSignal.timeout(20000)]) })
  let data: unknown
  try { data = await response.json() } catch { throw new ProjectError('Explore is temporarily unavailable.') }
  if (!response.ok) throw new ProjectError(response.status === 404 ? 'This room is no longer shared.' : 'Explore is temporarily unavailable.', response.status)
  return data
}
export const feedClient = {
  options: async (sourceId: string, scene: RoomScene, signal: AbortSignal) => parseSharingOptions(await projectRequest('/sharing-options', signal, 'POST', { source_run_id: sourceId, scene })),
  publish: async (options: SharingOptions, version: string, title: string, summary: string, consent: boolean, signal: AbortSignal) => parsePublication(await projectRequest(`/${options.project_id}/publication`, signal, 'POST', {
    version_id: version, expected_revision: options.revision, expected_publication_revision: options.publication?.revision ?? 0, title, summary, consent,
  })),
  unpublish: async (options: SharingOptions, signal: AbortSignal) => {
    await projectRequest(`/${options.project_id}/publication`, signal, 'DELETE', { public_id: options.publication!.id, expected_publication_revision: options.publication!.revision })
  },
  list: async (signal: AbortSignal, cursor?: string): Promise<FeedPage> => {
    const data = await publicRequest(cursor ? `?cursor=${encodeURIComponent(cursor)}` : '', signal) as FeedPage
    if (!data || !Array.isArray(data.items) || (data.next_cursor !== null && (typeof data.next_cursor !== 'string' || data.next_cursor.length > 200))) throw new ProjectError('Couldn’t read Explore.')
    return { items: data.items.map(parseFeedCard), next_cursor: data.next_cursor }
  },
  read: async (id: string, signal: AbortSignal) => parseSharedRoom(await publicRequest(`/${encodeURIComponent(id)}`, signal)),
}
