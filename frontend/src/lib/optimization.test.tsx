import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AgentRunError } from './agent-run'
import type { AgentState } from './agent-run'
import { OptimizationController } from './optimization-controller'
import { checkOptimization, improvementPreferences, optimizationClient, parseOptimization } from './optimization'
import type { OptimizationClient, OptimizationRun, StartOptimizationRequest } from './optimization'
import RoomAssessmentCards from '../components/room-analysis/RoomAssessmentCards'
import { ImprovementForm } from '../components/room-analysis/RoomImprovementWorkspace'
import { finishedRun } from './test-support/agent-run'
import { prepared } from './test-support/room-input'
import { preparedImprovement } from './test-support/optimization'

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })
function setup() {
  const source = finishedRun(prepared())
  const client: OptimizationClient = {
    prepare: vi.fn(async (_, body) => preparedImprovement(source, body)),
    read: vi.fn(), cancel: vi.fn(), execute: vi.fn(),
  }
  const controller = new OptimizationController(source, client)
  const render = () => renderToStaticMarkup(createElement(ImprovementForm, { state: controller.getSnapshot(), controller, partial: source.status === 'partial' }))
  return { source, client, controller, render }
}

describe('improvement selection and linked request', () => {
  it('connects choosing a focus and optional preferences to the real client without uploading or reconstructing', async () => {
    const source = finishedRun(prepared()), before = structuredClone(source)
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      expect(url).toBe(`/api/agent-runs/${source.run_id}/optimizations`)
      expect(init).toMatchObject({ method: 'POST', credentials: 'include', cache: 'no-store' })
      const body = JSON.parse(init!.body as string) as StartOptimizationRequest
      expect(body).toEqual({ request_id: expect.any(String), focus: 'storage',
        preferences: { budget: null, style: 'Warm minimal', constraints: 'Keep the desk near the window.' } })
      return Response.json(preparedImprovement(source, body))
    })
    vi.stubGlobal('fetch', fetcher)
    const controller = new OptimizationController(source)
    // No request is made from rendering, selecting or entering preferences.
    controller.select('storage')
    controller.change({ style: '  Warm minimal  ', constraints: ' Keep the desk near the window. ' })
    expect(fetcher).not.toHaveBeenCalled()
    await controller.prepare()
    expect(controller.getSnapshot().phase).toBe('prepared')
    expect(controller.getSnapshot().result!.run_id).not.toBe(source.run_id)
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(source).toEqual(before)
    const html = renderToStaticMarkup(createElement(ImprovementForm, { state: controller.getSnapshot(), controller, partial: false }))
    expect(html).toContain('original room is unchanged')
    expect(html).toContain('Run improvement')
  })

  it.each(['openness', 'storage', 'workspace', 'aesthetics'] as const)('preserves the selected %s focus and treats all preferences as optional', async focus => {
    const { controller, client } = setup()
    controller.select(focus)
    await controller.prepare()
    expect(vi.mocked(client.prepare).mock.calls[0][1]).toMatchObject({ focus, preferences: { budget: null, style: null, constraints: null } })
    expect(controller.getSnapshot().result!.focus).toBe(focus)
  })

  it('enables all four choices regardless of score, with workspace creation available', () => {
    const card: NonNullable<AgentState['room_assessment']>['openness'] = { status: 'unassessed', score: null, confidence: 'low', source: 'geometry', explanation: 'Uncertain.', entity_ids: [], metrics: [] }
    const html = renderToStaticMarkup(createElement(RoomAssessmentCards, { assessment: {
      policy_version: 'room-assessment-v1', scene_hash: 'a'.repeat(64), openness: card, storage: card,
      workspace: { ...card, status: 'not_applicable' }, aesthetics: { ...card, source: 'photos' }, recommended: null, notes: [],
    }, onImprove: vi.fn() }))
    expect(html).not.toContain('disabled=""')
    expect(html).toContain('Create a workspace')
    expect(html).toContain('Recommendations are optional')
  })

  it('shows optional preferences without budget or currency and bounds invalid input', async () => {
    const { controller, client, render } = setup()
    expect(render()).toBe('')
    controller.select('aesthetics')
    expect(render()).not.toContain('Budget')
    expect(render()).not.toContain('Currency')
    expect(render()).toContain('Style preference (optional)')
    expect(render()).toContain('Improve room')
    controller.change({ style: 'a'.repeat(101) }); await controller.prepare()
    expect(controller.getSnapshot().error).toContain('within their displayed limits')
    expect(client.prepare).not.toHaveBeenCalled()
    expect(improvementPreferences({ style: ' ', constraints: ' ' })).toEqual({ budget: null, style: null, constraints: null })
    expect(() => improvementPreferences({ style: 'a'.repeat(101), constraints: '' })).toThrow()
  })

  it('retries an ambiguous response with the identical ID and frozen preferences while suppressing duplicate clicks', async () => {
    const { controller, client, source } = setup()
    let release!: (value: OptimizationRun) => void
    vi.mocked(client.prepare).mockRejectedValueOnce(new TypeError('offline'))
    controller.select('openness'); await controller.prepare()
    const original = structuredClone(vi.mocked(client.prepare).mock.calls[0][1])
    expect(controller.getSnapshot().recovery).toBe(true)
    controller.change({ constraints: 'changed later' }); controller.select('storage')
    vi.mocked(client.prepare).mockImplementationOnce(() => new Promise(resolve => { release = resolve }))
    const retry = controller.retry()
    await controller.retry(); await controller.prepare()
    expect(client.prepare).toHaveBeenCalledTimes(2)
    expect(vi.mocked(client.prepare).mock.calls[1][1]).toEqual(original)
    release(preparedImprovement(source, original)); await retry
    expect(controller.getSnapshot().phase).toBe('prepared')
  })

  it('cancels the separate request and recovers cancellation without recreating it', async () => {
    const { controller, client } = setup()
    controller.select('storage'); await controller.prepare()
    const result = controller.getSnapshot().result!
    vi.mocked(client.cancel).mockRejectedValueOnce(new TypeError('offline'))
      .mockResolvedValueOnce({ ...result, status: 'cancelled' })
    await controller.cancel()
    expect(controller.getSnapshot().recovery).toBe(true)
    await controller.retry()
    expect(client.prepare).toHaveBeenCalledTimes(1)
    expect(client.cancel).toHaveBeenCalledTimes(2)
    expect(controller.getSnapshot().phase).toBe('cancelled')
    controller.select('workspace')
    expect(controller.getSnapshot().phase).toBe('editing')
  })

  it('rejects a response with another original room or focus and retains the displayed reconstruction', async () => {
    const { controller, client, source } = setup()
    const before = structuredClone(source)
    vi.mocked(client.prepare).mockImplementationOnce(async (_, body) => ({ ...preparedImprovement(source, body), focus: 'storage' }))
    controller.select('openness'); await controller.prepare()
    expect(controller.getSnapshot().phase).toBe('error')
    expect(controller.getSnapshot().result).toBeNull()
    expect(source).toEqual(before)
  })

  it('disables expired sources and reports server expiry', async () => {
    const { controller, client, source } = setup()
    const expired = new OptimizationController(source, client, () => Date.parse(source.expires_at) + 1)
    expired.select('workspace')
    expect(expired.getSnapshot().phase).toBe('expired')
    expect(client.prepare).not.toHaveBeenCalled()
    controller.select('workspace')
    vi.mocked(client.prepare).mockRejectedValueOnce(new AgentRunError('Expired', 404))
    await controller.prepare()
    expect(controller.getSnapshot().phase).toBe('expired')
    expect(controller.getSnapshot().recovery).toBe(false)
  })

  it('aborts an in-flight preparation when its reconstruction is unmounted', async () => {
    const { controller, client, source } = setup()
    let release!: (value: OptimizationRun) => void
    vi.mocked(client.prepare).mockImplementationOnce(() => new Promise(resolve => { release = resolve }))
    const disconnect = controller.connect()
    controller.select('aesthetics')
    const pending = controller.prepare()
    const [, body, signal] = vi.mocked(client.prepare).mock.calls[0]
    disconnect()
    expect(signal.aborted).toBe(true)
    release(preparedImprovement(source, body)); await pending
    expect(controller.getSnapshot().result).toBeNull()
  })

  it('keeps partial reconstruction limitations visible during the second input step', () => {
    const { controller } = setup()
    controller.select('workspace')
    const html = renderToStaticMarkup(createElement(ImprovementForm, { state: controller.getSnapshot(), controller, partial: true }))
    expect(html).toContain('Some items remain unresolved')
    expect(html).toContain('Starts from the visible layout')
  })

  it('validates the HTTP contracts and maps owned errors without trusting returned data', async () => {
    const { source } = setup()
    const body: StartOptimizationRequest = { request_id: '77777777-7777-4777-8777-777777777777', focus: 'openness' }
    const result = preparedImprovement(source, body)
    expect(parseOptimization(result)).toEqual(result)
    expect(() => parseOptimization({ ...result, status: 'completed' })).toThrow()
    expect(() => checkOptimization({ ...result, source_run_id: result.run_id }, source, body)).toThrow()
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ detail: [{ code: 'run_not_found', message: 'Unavailable.' }] }, { status: 404 })))
    await expect(optimizationClient.read(source.run_id, result.run_id, new AbortController().signal)).rejects.toMatchObject({ status: 404 })
  })
})

it('accepts catalog inventory changes while keeping protected objects and catalog dimensions enforced', () => {
  const source = finishedRun(prepared())
  const run = preparedImprovement(source, { request_id: '77777777-7777-4777-8777-777777777777', focus: 'storage' })
  run.status = 'completed'; run.stage = 'finished'; run.result_scene = structuredClone(run.source_scene)
  const added = { ...structuredClone(run.source_scene.objects[0]), id: 'new_storage', type: 'wardrobe' as const,
    asset_id: 'wardrobe_standard_01' as const, placement: 'floor' as const, support_object_id: null, support_surface_id: null,
    wall: null, opening_id: null, position: { x: 1, y: 0, z: 1 }, dimensions_m: { width: 1.2, height: 2.1, depth: .6 },
    provenance: 'agent_added' as const, measurements_source: 'catalog' as const, fixed: false, locked: false }
  run.result_scene.objects.push(added)
  expect(parseOptimization(run).result_scene!.objects).toHaveLength(run.source_scene.objects.length + 1)
  added.dimensions_m.width = 20
  expect(() => parseOptimization(run)).toThrow('protected room data')
  added.dimensions_m.width = 1.2
  run.source_scene.objects[0].locked = true
  run.result_scene.objects = run.result_scene.objects.filter(o => o.id !== run.source_scene.objects[0].id)
  expect(() => parseOptimization(run)).toThrow('protected room data')
})
