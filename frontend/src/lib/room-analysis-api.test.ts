import { afterEach, describe, expect, it, vi } from 'vitest'
import { analyzeRoom, proposeRoomScene } from './api'
import { parseRoomAnalysis, parseSceneProposal, RoomAnalysisError } from './room-analysis'
import { analyzedRoom, proposedRoom, needsDimensions } from './test-support/room-analysis'

afterEach(() => vi.unstubAllGlobals())
const signal = () => new AbortController().signal

describe('room analysis API boundary', () => {
  it('sends only the owned upload handle with credentials and no automatic retries', async () => {
    const result = analyzedRoom(), fetcher = vi.fn(async () => new Response(JSON.stringify(result)))
    vi.stubGlobal('fetch', fetcher)
    expect(await analyzeRoom(result.input_id, signal())).toEqual(result)
    const [url, options] = fetcher.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/rooms/analyze')
    expect(options).toMatchObject({ method: 'POST', credentials: 'include', cache: 'no-store' })
    expect(JSON.parse(options.body as string)).toEqual({ input_id: result.input_id })
    expect(options.signal).toBeInstanceOf(AbortSignal)
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it('confirms measurements against an analysis ID without sending photos or observations again', async () => {
    const result = proposedRoom(), fetcher = vi.fn(async () => new Response(JSON.stringify(result)))
    vi.stubGlobal('fetch', fetcher)
    const request = { analysis_id: result.analysis_id, dimensions: { height_m: 2.6, measurements_source: 'user_confirmed' as const } }
    expect(await proposeRoomScene(request, signal())).toEqual(result)
    const [url, options] = fetcher.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/rooms/propose-scene')
    expect(JSON.parse(options.body as string)).toEqual(request)
    expect(options.credentials).toBe('include')
  })
  it.each(['analysis_unavailable', 'analysis_refused', 'analysis_incomplete', 'provider_rate_limited', 'analysis_limit_reached'])('keeps actionable typed %s failures', async code => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ detail: [{ code, message: 'Review this room request.' }] }), { status: 503 }))
    vi.stubGlobal('fetch', fetcher)
    await expect(analyzeRoom(analyzedRoom().input_id, signal())).rejects.toMatchObject({ code, message: 'Review this room request.', connected: true })
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it.each(['private traceback', '{"private": "secret"}'])('does not echo unexpected provider responses', async text => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(text, { status: 502 })))
    await expect(analyzeRoom(analyzedRoom().input_id, signal())).rejects.toBeInstanceOf(RoomAnalysisError)
    await expect(analyzeRoom(analyzedRoom().input_id, signal())).rejects.not.toThrow('private')
  })
  it('rejects mismatched IDs and altered confirmations', async () => {
    const result = proposedRoom()
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(result))))
    await expect(proposeRoomScene({ analysis_id: '44444444-4444-4444-8444-444444444444' }, signal())).rejects.toThrow('does not match')
    await expect(proposeRoomScene({ analysis_id: result.analysis_id, dimensions: { height_m: 3, measurements_source: 'user_confirmed' } }, signal())).rejects.toThrow('confirmed measurements')
  })
  it('accepts an honest dimension request and incomplete result without opening a scene', () => {
    expect(parseSceneProposal(needsDimensions()).scene).toBeNull()
    expect(parseSceneProposal({ ...proposedRoom(), status: 'incomplete', scene: null, validation: null,
      issues: [{ code: 'geometry', entity_ids: ['bed_observed'], message: 'Outside room.' }] }).scene).toBeNull()
  })
  it.each([
    { scene: null }, { validation: null }, { dimensions_to_confirm: ['height_m'] },
    { estimated_axes: ['width_m'] }, { status: 'incomplete' },
  ])('rejects invalid ready/incomplete states before rendering %j', patch => {
    expect(() => parseSceneProposal({ ...proposedRoom(), ...patch })).toThrow(RoomAnalysisError)
  })
  it('rejects contradictory geometry flags and scene dimensions', () => {
    const data = proposedRoom()
    expect(() => parseSceneProposal({ ...data, validation: { ...data.validation, valid: false } })).toThrow()
    expect(() => parseSceneProposal({ ...data, dimensions: { ...data.dimensions, width_m: 9 } })).toThrow()
    expect(() => parseRoomAnalysis({ ...analyzedRoom(), observations: {} })).toThrow()
  })
  it('rejects analysis for a different input and honors caller cancellation', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify(analyzedRoom())))
    vi.stubGlobal('fetch', fetcher)
    const controller = new AbortController()
    await expect(analyzeRoom('44444444-4444-4444-8444-444444444444', controller.signal)).rejects.toThrow('does not match')
    const options = (fetcher.mock.calls[0] as unknown as [string, RequestInit])[1]
    controller.abort()
    expect(options.signal?.aborted).toBe(true)
  })
})
