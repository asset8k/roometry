import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, expect, it, vi } from 'vitest'
import { OptimizationController } from './optimization-controller'
import type { OptimizationClient, OptimizationRun, StartOptimizationRequest } from './optimization'
import { parseOptimization } from './optimization'
import { AgentRunError } from './agent-run'
import { ImprovementForm } from '../components/room-analysis/RoomImprovementWorkspace'
import { finishedRun } from './test-support/agent-run'
import { prepared } from './test-support/room-input'
import { preparedImprovement } from './test-support/optimization'

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })
function retained(run: OptimizationRun): OptimizationRun {
  const result = structuredClone(run)
  result.status = 'completed'; result.stage = 'finished'
  result.result_scene = structuredClone(run.source_scene)
  result.result_scene.objects[0].color = '#746A5B'
  result.usage.model_calls = 2
  result.message = 'A validated improvement candidate is ready for review.'
  result.attempts = [{ number: 1, outcome: 'accepted', before_score: null, after_score: null, changed_object_ids: [result.result_scene.objects[0].id],
    issues: [], summary: 'Coordinate upholstery.', review: { preferred: 'candidate', confidence: 'medium', constraints_satisfied: true, explanation: 'The materials are more cohesive.', aesthetics: null } }]
  return result
}
function setup() {
  const source = finishedRun(prepared())
  let saved: OptimizationRun
  const client: OptimizationClient = {
    prepare: vi.fn(async (_, body) => saved = preparedImprovement(source, body)),
    execute: vi.fn(async () => retained(saved)), read: vi.fn(), cancel: vi.fn(),
  }
  const controller = new OptimizationController(source, client)
  const render = () => renderToStaticMarkup(createElement(ImprovementForm, { state: controller.getSnapshot(), controller, partial: false }))
  return { source, client, controller, render }
}

it('shows quota exhaustion without locking the viewer into execution recovery', async () => {
  const { controller, client } = setup()
  vi.mocked(client.execute).mockRejectedValueOnce(new AgentRunError('This room has used its AI actions.', 429, 'session_action_limit'))
  controller.select('aesthetics'); await controller.start()
  expect(controller.getSnapshot()).toMatchObject({ phase: 'error', recovery: false, result: null, error: 'This room has used its AI actions.' })
  expect(controller.locked()).toBe(false)
  expect(client.execute).toHaveBeenCalledTimes(1)
})

it.each(['wall', 'surface'] as const)('accepts an authorized catalog TV with %s placement from the backend', placement => {
  const source = finishedRun(prepared())
  const run = preparedImprovement(source, { request_id: '77777777-7777-4777-8777-777777777777', focus: 'aesthetics' })
  const result = structuredClone(run.source_scene)
  const desk = result.objects.find(o => o.type === 'desk')!
  result.objects.push({ ...result.objects[0], id: 'new_tv', type: 'tv', asset_id: 'tv_standard_01',
    position: { x: 2, y: placement === 'wall' ? 1.2 : desk.dimensions_m.height, z: placement === 'wall' ? .03 : desk.position.z },
    dimensions_m: { width: 1.2, height: placement === 'wall' ? .68 : .74, depth: placement === 'wall' ? .06 : .28 },
    placement, wall: placement === 'wall' ? 'north' : null, support_object_id: placement === 'surface' ? desk.id : null,
    support_surface_id: placement === 'surface' ? 'tabletop' : null, opening_id: null, fixed: false, locked: false,
    provenance: 'agent_added', measurements_source: 'catalog' })
  expect(parseOptimization({ ...run, status: 'completed', result_scene: result }).result_scene?.objects.at(-1)?.type).toBe('tv')
})

it('submits preferences and executes the same linked run over the real HTTP client', async () => {
  const source = finishedRun(prepared()), original = structuredClone(source)
  const calls: string[] = []
  let saved: OptimizationRun
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    expect(init).toMatchObject({ method: 'POST', credentials: 'include', cache: 'no-store' })
    calls.push(url)
    if (url.endsWith('/execute')) {
      expect(JSON.parse(init!.body as string)).toEqual({})
      return Response.json(retained(saved))
    }
    saved = preparedImprovement(source, JSON.parse(init!.body as string) as StartOptimizationRequest)
    return Response.json(saved)
  }))
  const controller = new OptimizationController(source)
  controller.select('aesthetics')
  await controller.start()
  expect(calls).toEqual([`/api/agent-runs/${source.run_id}/optimizations`, `/api/agent-runs/${source.run_id}/optimizations/88888888-8888-4888-8888-888888888888/execute`])
  expect(controller.getSnapshot().phase).toBe('completed')
  expect(controller.getSnapshot().result!.result_scene).not.toEqual(source.working_scene)
  expect(source).toEqual(original)
})

it('shows a retained outcome, actual counters and limitations while the original viewer remains separate', async () => {
  const { controller, render } = setup()
  controller.select('aesthetics'); await controller.start()
  expect(render()).toContain('Candidate ready')
  expect(render()).toContain('2 model calls')
  expect(render()).toContain('Checks and limitations')
  expect(render()).toContain('The materials are more cohesive')
  controller.select('storage')
  expect(controller.getSnapshot().phase).toBe('editing')
})

it('keeps the same execution on connection recovery and suppresses late status regression', async () => {
  const { controller, client, source } = setup()
  controller.select('aesthetics'); await controller.prepare()
  const saved = controller.getSnapshot().result!
  vi.mocked(client.execute).mockRejectedValueOnce(new TypeError('offline'))
    .mockResolvedValueOnce({ ...saved, status: 'running', stage: 'reviewing', usage: { ...saved.usage, model_calls: 1 } })
  await controller.execute()
  expect(controller.getSnapshot().recovery).toBe(true)
  await controller.retry()
  expect(client.prepare).toHaveBeenCalledTimes(1)
  expect(client.execute).toHaveBeenCalledTimes(2)
  expect(vi.mocked(client.execute).mock.calls.every(call => call[0] === source.run_id && call[1] === saved.run_id)).toBe(true)
  vi.mocked(client.read).mockResolvedValueOnce(retained(saved)).mockResolvedValueOnce({ ...saved, status: 'running' })
  await controller.poll()
  expect(controller.getSnapshot().phase).toBe('completed')
  await controller.poll()
  expect(controller.getSnapshot().phase).toBe('completed')
})

it('cancels an in-flight execution cooperatively and never starts another run', async () => {
  const { controller, client } = setup()
  controller.select('aesthetics'); await controller.prepare()
  const saved = controller.getSnapshot().result!
  let release!: (run: OptimizationRun) => void
  vi.mocked(client.execute).mockImplementationOnce(() => new Promise(resolve => { release = resolve }))
  vi.mocked(client.cancel).mockResolvedValueOnce({ ...saved, status: 'running', stage: 'proposing' })
  vi.mocked(client.read).mockResolvedValueOnce({ ...saved, status: 'cancelled', stage: 'finished' })
  const execution = controller.execute()
  await controller.cancel()
  expect(vi.mocked(client.execute).mock.calls[0][2].aborted).toBe(true)
  expect(controller.getSnapshot().phase).toBe('cancelling')
  await controller.poll()
  release(retained(saved)); await execution
  expect(controller.getSnapshot().phase).toBe('cancelled')
  expect(client.prepare).toHaveBeenCalledTimes(1)
  expect(client.execute).toHaveBeenCalledTimes(1)
})

it('recovers a lost prepare response and then executes exactly that request after the single user submission', async () => {
  const { controller, client } = setup()
  vi.mocked(client.prepare).mockRejectedValueOnce(new TypeError('offline'))
  controller.select('aesthetics'); await controller.start()
  const first = vi.mocked(client.prepare).mock.calls[0][1]
  expect(controller.getSnapshot().recovery).toBe(true)
  await controller.retry()
  expect(vi.mocked(client.prepare).mock.calls[1][1]).toEqual(first)
  expect(client.execute).toHaveBeenCalledTimes(1)
  expect(controller.getSnapshot().phase).toBe('completed')
})

it('handles no improvement and rejects alleged improvements that alter protected room data', async () => {
  const { controller, client } = setup()
  controller.select('storage'); await controller.prepare()
  const saved = controller.getSnapshot().result!
  vi.mocked(client.execute).mockResolvedValueOnce({ ...saved, status: 'no_improvement', stage: 'finished', message: 'No safe improvement found.' })
  await controller.execute()
  expect(controller.getSnapshot().phase).toBe('no_improvement')
  const unsafe = retained(saved)
  unsafe.result_scene!.room.width_m += 1
  expect(() => parseOptimization(unsafe)).toThrow('protected room data')
})
