import { afterEach, describe, expect, it, vi } from 'vitest'
import { requestEditPreview } from './api'
import { loadExampleScene } from './scene'
import { acceptedMove } from './test-support/edit-responses'

const move = { operation: 'move' as const, object_id: 'desk_1', position: { x: 0.85, z: 2 } }
afterEach(() => vi.unstubAllGlobals())

describe('edit preview API client', () => {
  it('posts the full accepted base and absolute edit, and validates the returned scene', async () => {
    const scene = loadExampleScene()
    const payload = acceptedMove(scene)
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(payload), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const controller = new AbortController()
    expect(await requestEditPreview(scene, move, controller.signal)).toEqual(payload)
    const [url, options] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url.endsWith('/scenes/preview-edit')).toBe(true)
    expect(options.method).toBe('POST')
    expect(options.headers).toEqual({ 'Content-Type': 'application/json' })
    expect(JSON.parse(options.body as string)).toEqual({ scene, edit: move })
    expect(options.signal).toBeInstanceOf(AbortSignal)
  })

  it.each([422, 500, 503])('rejects HTTP %s without returning an accepted layout', async status => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status })))
    await expect(requestEditPreview(loadExampleScene(), move, new AbortController().signal)).rejects.toThrow()
  })

  it('rejects malformed JSON and unexpected response shapes', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('not JSON', { status: 200 })))
    await expect(requestEditPreview(loadExampleScene(), move, new AbortController().signal)).rejects.toThrow()
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"accepted":true}', { status: 200 })))
    await expect(requestEditPreview(loadExampleScene(), move, new AbortController().signal)).rejects.toThrow()
  })
})
