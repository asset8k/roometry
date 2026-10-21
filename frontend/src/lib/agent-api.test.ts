import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { createElement } from 'react'
import { agentClient } from './agent-api'
import { parseAgentRun } from './agent-run'
import type { ClarificationAnswer } from './agent-run'
import { RoomInputController } from './room-input-controller'
import { RoomGenerationController } from './room-generation-controller'
import RoomGenerationWorkspace from '../components/room-analysis/RoomGenerationWorkspace'
import { limits, photo, prepared } from './test-support/room-input'
import { finishedRun, waitingRun } from './test-support/agent-run'

afterEach(() => vi.unstubAllGlobals())

describe('agent HTTP integration', () => {
  it('connects upload → three-choice question → same-run resume → editable viewer over the real API client', async () => {
    let input = prepared([photo()])
    const calls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? 'GET'} ${url}`)
      if (url.endsWith('/input-limits')) return Response.json(limits)
      if (url.endsWith('/rooms/inputs')) {
        const form = init!.body as FormData
        input = prepared([photo()]); input.options = JSON.parse(form.get('options') as string)
        expect(form.getAll('images')).toHaveLength(1)
        return Response.json(input, { status: 201 })
      }
      expect(init?.credentials).toBe('include'); expect(init?.cache).toBe('no-store')
      if (url.endsWith('/agent-runs')) {
        expect(JSON.parse(init!.body as string)).toEqual({ input_id: input.input_id, request_id: expect.any(String) })
        return Response.json(waitingRun(input))
      }
      const answer = JSON.parse(init!.body as string) as ClarificationAnswer
      expect(answer).toEqual({ question_id: 'q1', choice_id: 'enter_measurements', dimensions: { height_m: 2.6 } })
      return Response.json({ ...finishedRun(input), questions: waitingRun(input).questions,
        submitted_answers: { q1: { ...answer, dimensions: { width_m: null, length_m: null, height_m: 2.6 } } }, clarification_answers: { q1: answer.choice_id } })
    }))
    const inputs = new RoomInputController(undefined, { create: () => 'blob:photo', revoke: vi.fn() })
    const generation = new RoomGenerationController(inputs)
    const release = generation.connect()
    try {
      await inputs.loadLimits(); inputs.addPhotos([photo()]); inputs.change({ width: '4', length: '4' })
      await generation.generate()
      const render = () => renderToStaticMarkup(createElement(RoomGenerationWorkspace, { state: generation.getSnapshot(), controller: generation, onConnectionChange: vi.fn() }))
      expect(generation.getSnapshot().phase).toBe('awaiting_answer')
      const html = render()
      expect(html.match(/type="radio"/g)).toHaveLength(3)
      expect(html).toContain('Question 1 of up to 3')
      expect(html).not.toContain('Build base scene')
      expect(html).not.toContain('3D room viewer')
      await generation.answer({ question_id: 'q1', choice_id: 'enter_measurements', dimensions: { height_m: 2.6 } })
      expect(generation.getSnapshot().phase).toBe('completed')
      expect(render()).toContain('3D room viewer')
      expect(render()).toContain('Your bedroom')
      expect(calls).toEqual(['GET /api/rooms/input-limits', 'POST /api/rooms/inputs', 'POST /api/agent-runs', 'POST /api/agent-runs/66666666-6666-4666-8666-666666666666/answer'])
    } finally { release() }
  })
  it('uses a GET for discovery and sends cancellation as an owned JSON operation', async () => {
    const input = prepared([photo()])
    const fetcher = vi.fn(async () => Response.json(finishedRun(input)))
    vi.stubGlobal('fetch', fetcher)
    await agentClient.find('request-id', new AbortController().signal)
    expect(fetcher.mock.calls[0]).toEqual(['/api/agent-runs?request_id=request-id', expect.objectContaining({ method: 'GET', credentials: 'include', cache: 'no-store' })])
    await agentClient.cancel('run-id', new AbortController().signal)
    expect(fetcher.mock.calls[1]).toEqual(['/api/agent-runs/run-id/cancel', expect.objectContaining({ method: 'POST', body: '{}' })])
  })
  it('rejects inconsistent and unvalidated scenes before any viewer is opened', () => {
    const run = finishedRun(prepared([photo()]))
    expect(() => parseAgentRun({ ...run, input_id: '77777777-7777-4777-8777-777777777777' })).toThrow()
    expect(() => parseAgentRun({ ...run, pending_question: waitingRun(prepared([photo()])).pending_question })).toThrow()
    expect(() => parseAgentRun({ ...run, proposal: { ...run.proposal, validation: { ...run.proposal!.validation, valid: false } } })).toThrow()
    const changed = structuredClone(run)
    changed.working_scene!.room.width_m = 10
    expect(() => parseAgentRun(changed)).toThrow()
  })
})
