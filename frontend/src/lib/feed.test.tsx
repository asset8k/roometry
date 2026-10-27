import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, expect, it, vi } from 'vitest'
import { feedClient, parseFeedCard, parseSharedRoom, parseSharingOptions } from './feed'
import type { SharingOptions } from './feed'
import { loadExampleScene } from './test-support/scenes'
import { isRoomRoute } from './workspace-navigation'
import PublicRoomPage from '../components/site/PublicRoomPage'

const id = '12345678-1234-4234-8234-123456789abc'
const version = '87654321-1234-4234-8234-123456789abc'
const card = { id, title: 'Quiet studio', summary: 'A place to focus.', room_type: 'bedroom', object_count: loadExampleScene().objects.length,
  published_at: '2026-10-27T00:00:00Z', preview_url: `/api/feed/${id}/preview` }
const options: SharingOptions = { project_id: id, revision: 4, title: 'Private workspace', versions: [{ id: version, label: 'Original', preview_url: `/api/projects/${id}/versions/${version}/preview` }], selected_version_id: version, publication: null }
function shared() {
  const scene = loadExampleScene(), ids = new Map(scene.objects.map((o, i) => [o.id, `object_${i + 1}`])), openings = new Map(scene.openings.map((o, i) => [o.id, `opening_${i + 1}`]))
  scene.objects.forEach(o => { o.id = ids.get(o.id)!; o.fixed = true; o.locked = true; o.support_object_id = ids.get(o.support_object_id!) ?? null; o.opening_id = openings.get(o.opening_id!) ?? null })
  scene.openings.forEach(o => { o.id = openings.get(o.id)! })
  scene.assumptions = []; scene.warnings = []
  return { ...card, scene }
}
afterEach(() => vi.unstubAllGlobals())

it('accepts only API-owned preview paths and discards nonpublic card metadata', () => {
  expect(parseFeedCard({ ...card, private_chat: 'hidden' })).toEqual(card)
  expect(() => parseFeedCard({ ...card, preview_url: 'https://other.example/private-photo.jpg' })).toThrow()
  expect(() => parseSharingOptions({ ...options, versions: [{ ...options.versions[0], preview_url: `/api/projects/${version}/images/${id}` }] })).toThrow()
  expect(() => parseSharingOptions({ ...options, selected_version_id: id })).toThrow()
})
it('requires a read-only sanitized renderer document rather than a private run', () => {
  expect(parseSharedRoom(shared()).scene.objects.every(o => o.fixed && o.locked)).toBe(true)
  const privateNotes = shared(); privateNotes.scene.assumptions = ['Private answer']
  expect(() => parseSharedRoom(privateNotes)).toThrow()
  const editable = shared(); editable.scene.objects[0].locked = false
  expect(() => parseSharedRoom(editable)).toThrow()
  expect(() => parseSharedRoom({ ...shared(), object_count: 100 })).toThrow()
})
it('browses anonymously without forwarding account cookies, using the returned cursor', async () => {
  const fetcher = vi.fn(async () => Response.json({ items: [card], next_cursor: 'next+/=' }))
  vi.stubGlobal('fetch', fetcher)
  const first = await feedClient.list(new AbortController().signal)
  await feedClient.list(new AbortController().signal, first.next_cursor!)
  expect(fetcher.mock.calls).toHaveLength(2)
  const calls = fetcher.mock.calls as unknown as [string, RequestInit][]
  expect(calls[0][1]).toMatchObject({ credentials: 'omit', cache: 'no-store' })
  expect(calls[1][0]).toBe('/api/feed?cursor=next%2B%2F%3D')
})
it('publishes exactly the selected saved version with explicit consent and both revisions', async () => {
  const publication = { active: true, id, version_id: version, revision: 1, title: card.title, summary: card.summary }
  const fetcher = vi.fn(async () => Response.json(publication))
  vi.stubGlobal('fetch', fetcher)
  expect(await feedClient.publish(options, version, card.title, card.summary, true, new AbortController().signal)).toEqual(publication)
  const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit]
  expect(url).toBe(`/api/projects/${id}/publication`)
  expect(init).toMatchObject({ method: 'POST', credentials: 'include', cache: 'no-store' })
  expect(JSON.parse(init.body as string)).toEqual({ version_id: version, expected_revision: 4, expected_publication_revision: 0, title: card.title, summary: card.summary, consent: true })
  expect(init.body).not.toContain('scene')
})
it('revokes the active publication by revision without deleting the private room', async () => {
  const fetcher = vi.fn(async () => new Response(null, { status: 204 }))
  vi.stubGlobal('fetch', fetcher)
  await feedClient.unpublish({ ...options, publication: { active: true, id: version, version_id: version, revision: 2, title: card.title, summary: '' } }, new AbortController().signal)
  const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit]
  expect(url).toBe(`/api/projects/${id}/publication`)
  expect(init.method).toBe('DELETE')
  expect(JSON.parse(init.body as string)).toEqual({ public_id: version, expected_publication_revision: 2 })
})
it('shows revocation as unavailable and never mounts a creation or paid execution flow', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ detail: [] }, { status: 404 })))
  await expect(feedClient.read(id, new AbortController().signal)).rejects.toThrow('no longer shared')
  const html = renderToStaticMarkup(<PublicRoomPage id={id} onCreate={vi.fn()}/>)
  expect(html).toContain('Opening shared room')
  expect(html).not.toMatch(/Save|Refine your room|Edit layout|Creating your room/)
  expect(isRoomRoute(`#shared/${id}`)).toBe(true)
})
