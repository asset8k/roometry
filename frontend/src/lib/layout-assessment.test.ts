import { afterEach, describe, expect, it, vi } from 'vitest'
import { LayoutAssessmentController } from './layout-assessment'
import { requestLayoutScore } from './api'
import { parseLayoutScore } from './layout-scoring'
import { loadExampleScene, loadAlternativeScene } from './test-support/scenes'
import fixture from './test-support/fixtures/layout_score.json'
import type { LayoutScoreReport } from './layout-scoring'

const report = () => parseLayoutScore(fixture)
function deferred() {
  let resolve!: (report: LayoutScoreReport) => void
  let reject!: (error: Error) => void
  const promise = new Promise<LayoutScoreReport>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
afterEach(() => vi.unstubAllGlobals())

describe('accepted scene assessment', () => {
  it.each(['success', 'error'])('ignores stale %s when switching or resetting scenes', async late => {
    const old = deferred(), current = deferred()
    const client = vi.fn().mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise)
    const controller = new LayoutAssessmentController(client)
    const first = controller.load(loadExampleScene(), ['workspace'])
    const second = controller.load(loadAlternativeScene(), ['more_open_space'])
    expect(client.mock.calls[0][2].aborted).toBe(true)
    expect(controller.getSnapshot().report).toBeNull()
    current.resolve(report()); await second
    if (late === 'success') old.resolve({ ...report(), overall_score: 99 })
    else old.reject(new Error('Old request failed'))
    await first
    expect(controller.getSnapshot().status).toBe('ready')
    expect(controller.getSnapshot().report?.overall_score).toBeNull()
  })
  it('clears an obsolete report on failure and retries without changing the scene', async () => {
    const client = vi.fn().mockResolvedValueOnce(report()).mockRejectedValueOnce(new Error('Offline')).mockResolvedValueOnce(report())
    const controller = new LayoutAssessmentController(client)
    const scene = loadExampleScene(), before = structuredClone(scene)
    await controller.load(scene, ['general_analysis'])
    await controller.load(scene, ['general_analysis'])
    expect(controller.getSnapshot()).toEqual({ status: 'error', report: null })
    await controller.load(scene, ['general_analysis'])
    expect(controller.getSnapshot().status).toBe('ready')
    expect(scene).toEqual(before)
  })
  it('cancels late completion on unmount', async () => {
    const pending = deferred()
    const controller = new LayoutAssessmentController(() => pending.promise)
    const result = controller.load(loadExampleScene(), undefined)
    controller.cancel()
    pending.resolve(report()); await result
    expect(controller.getSnapshot().report).toBeNull()
  })
  it('posts scene and goals to the deterministic score endpoint with a deadline', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(fixture)))
    vi.stubGlobal('fetch', fetchMock)
    const scene = loadExampleScene()
    await requestLayoutScore(scene, ['general_analysis'], new AbortController().signal)
    const [url, options] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toMatch(/\/scenes\/score$/)
    expect(JSON.parse(options.body as string)).toEqual({ scene, goals: ['general_analysis'] })
    expect(options.signal).toBeInstanceOf(AbortSignal)
  })
  it.each([500, 422])('rejects assessment HTTP %s', async status => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status })))
    await expect(requestLayoutScore(loadExampleScene(), undefined, new AbortController().signal)).rejects.toThrow()
  })
})
