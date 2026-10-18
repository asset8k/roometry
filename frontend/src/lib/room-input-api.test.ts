import { afterEach, describe, expect, it, vi } from 'vitest'
import { discardRoomInput, fetchUploadLimits, uploadRoomInput } from './api'
import { RoomInputError } from './room-input'
import { limits, photo, prepared } from './test-support/room-input'

afterEach(() => vi.unstubAllGlobals())
const signal = () => new AbortController().signal

describe('room input API client', () => {
  it('loads complete policy with credentials and validates it', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify(limits)))
    vi.stubGlobal('fetch', fetcher)
    expect(await fetchUploadLimits(signal())).toEqual(limits)
    const [url, options] = fetcher.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/rooms/input-limits')
    expect(options.credentials).toBe('include')
    expect(options.cache).toBe('no-store')
  })
  it('uses repeated file parts and JSON text, leaving the multipart boundary to fetch', async () => {
    const files = [photo(), photo('phone.heic', 'image/heic')]
    const fetcher = vi.fn(async () => new Response(JSON.stringify(prepared(files)), { status: 201 }))
    vi.stubGlobal('fetch', fetcher)
    const options = { dimensions: { width_m: 4, length_m: null }, budget: { amount: 0, currency: 'USD' } }
    expect((await uploadRoomInput(files, options, signal())).images).toHaveLength(2)
    const [url, request] = fetcher.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/rooms/inputs')
    expect(request.method).toBe('POST')
    expect(request.headers).toBeUndefined()
    expect(request.credentials).toBe('include')
    const form = request.body as FormData
    expect(form.getAll('images').map(value => (value as File).name)).toEqual(['room.png', 'phone.heic'])
    expect(JSON.parse(form.get('options') as string)).toEqual(options)
  })
  it('preserves actionable server errors without treating a known rejection as an uncertain commit', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: [
      { code: 'invalid_image', message: 'This photo could not be decoded.', file_index: 1 },
    ] }), { status: 422 })))
    try { await uploadRoomInput([photo(), photo()], {}, signal()); expect.fail('Expected rejection') } catch (error) {
      expect(error).toBeInstanceOf(RoomInputError)
      expect((error as RoomInputError).issues[0].file_index).toBe(1)
      expect((error as RoomInputError).uncertain).toBe(false)
    }
  })
  it('does not echo unstructured private server errors', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('private traceback and path', { status: 500 })))
    await expect(uploadRoomInput([photo()], {}, signal())).rejects.not.toThrow('private traceback')
  })
  it('rejects response mismatches and marks an ambiguous successful POST as unconfirmed', async () => {
    const result = prepared()
    result.images[0].original_bytes += 1
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(result), { status: 201 })))
    try { await uploadRoomInput([photo()], {}, signal()); expect.fail('Expected mismatch') } catch (error) {
      expect((error as RoomInputError).uncertain).toBe(true)
    }
  })
  it.each([204, 404])('treats discard status %s as no longer accessible', async status => {
    const fetcher = vi.fn(async () => new Response(null, { status }))
    vi.stubGlobal('fetch', fetcher)
    await discardRoomInput('id/with path', signal())
    const [url, options] = fetcher.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/rooms/inputs/id%2Fwith%20path')
    expect(options.method).toBe('DELETE')
    expect(options.credentials).toBe('include')
  })
  it.each([200, 503])('does not clear inputs after unexpected discard status %s', async status => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status })))
    await expect(discardRoomInput('input', signal())).rejects.toThrow()
  })
})
