import { afterEach, describe, expect, it, vi } from 'vitest'
import { RoomInputController } from './room-input-controller'
import { RoomGenerationController, generationProgress } from './room-generation-controller'
import { AgentRunError } from './agent-run'
import type { AgentState } from './agent-run'
import type { AgentClient } from './agent-api'
import { agentRun, finishedRun, waitingRun } from './test-support/agent-run'
import { limits, photo, prepared } from './test-support/room-input'

function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(yes => { resolve = yes }); return { promise, resolve } }
const releases: (() => void)[] = []
afterEach(() => { releases.splice(0).forEach(release => release()); vi.useRealTimers() })
async function setup(overrides: Partial<AgentClient> = {}, height = '2.6') {
  let batch = prepared([photo()]), clock = Date.now()
  const upload = vi.fn(async (files: readonly File[], options: typeof batch.options) => { batch = prepared(files); batch.options = options; return batch })
  const inputs = new RoomInputController({ limits: async () => limits, upload, discard: vi.fn(async () => {}) }, { create: () => 'blob:photo', revoke: vi.fn() }, () => clock)
  const client: AgentClient = {
    start: vi.fn(async () => finishedRun(batch)), read: vi.fn(async () => finishedRun(batch)), find: vi.fn(async () => agentRun(batch)),
    answer: vi.fn(async () => finishedRun(batch)), cancel: vi.fn(async () => agentRun(batch, { status: 'cancelled', pending_tool: null })), ...overrides,
  }
  const generation = new RoomGenerationController(inputs, client, () => clock, 10_000)
  const release = generation.connect(); releases.push(release)
  await inputs.loadLimits(); inputs.addPhotos([photo()]); inputs.change({ width: '4', length: '4', height })
  return { inputs, generation, client, upload, batch: () => batch, advance: (ms: number) => { clock += ms } }
}

describe('guided room generation', () => {
  it('uploads once and opens the validated editable scene from one action', async () => {
    const { generation, upload, client, batch } = await setup()
    await generation.generate()
    expect(upload).toHaveBeenCalledTimes(1)
    expect(client.start).toHaveBeenCalledWith({ input_id: batch().input_id, request_id: expect.any(String) }, expect.any(AbortSignal))
    expect(generation.getSnapshot().phase).toBe('completed')
    expect(generation.getSnapshot().run?.working_scene?.objects.length).toBeGreaterThan(0)
    expect(generation.locked()).toBe(false)
    await generation.generate()
    expect(client.start).toHaveBeenCalledTimes(1)
  })
  it('pauses for measurements and resumes the same run without uploading or starting again', async () => {
    const s = await setup({}, '')
    vi.mocked(s.client.start).mockImplementation(async () => waitingRun(s.batch()))
    vi.mocked(s.client.answer).mockImplementation(async (_id, answer) => ({ ...finishedRun(s.batch()),
      questions: waitingRun(s.batch()).questions, submitted_answers: { q1: { ...answer, dimensions: {
        width_m: null, length_m: null, height_m: answer.dimensions?.height_m ?? null,
      } } }, clarification_answers: { q1: answer.choice_id } }))
    await s.generation.generate()
    expect(s.generation.getSnapshot().phase).toBe('awaiting_answer')
    expect(s.generation.locked()).toBe(true)
    const answer = { question_id: 'q1', choice_id: 'enter_measurements', dimensions: { height_m: 2.6 } }
    await s.generation.answer(answer)
    expect(s.client.answer).toHaveBeenCalledWith('66666666-6666-4666-8666-666666666666', answer, expect.any(AbortSignal))
    expect(s.generation.getSnapshot().phase).toBe('completed')
    expect(s.upload).toHaveBeenCalledTimes(1); expect(s.client.start).toHaveBeenCalledTimes(1)
  })
  it('retains partial geometry and its unresolved observations', async () => {
    const s = await setup()
    vi.mocked(s.client.start).mockImplementation(async () => finishedRun(s.batch(), true))
    await s.generation.generate()
    expect(s.generation.getSnapshot().phase).toBe('partial')
    expect(s.generation.getSnapshot().run?.proposal?.unresolved_entities?.length).toBe(1)
  })
  it.each(['invalid_analysis', 'analysis_limit_reached'])('uploads a fresh batch on an explicit retry after %s, keeping files and measurements', async code => {
    const s = await setup()
    vi.mocked(s.client.start).mockImplementationOnce(async () => agentRun(s.batch(), { status: 'failed', last_error: 'tool_failed', pending_tool: null,
      tool_feedback: [{ name: 'analyze_photos', outcome: 'error', message: 'Photo analysis failed.', details: { code } }] }))
    await s.generation.generate()
    expect(s.generation.getSnapshot().phase).toBe('failed')
    expect(s.upload).toHaveBeenCalledTimes(1)
    const photos = s.inputs.getSnapshot().photos
    const draft = s.inputs.getSnapshot().draft
    await s.generation.generate()
    expect(s.upload).toHaveBeenCalledTimes(2)
    expect(s.inputs.getSnapshot().photos).toBe(photos)
    expect(s.inputs.getSnapshot().draft).toBe(draft)
    expect(s.generation.getSnapshot().phase).toBe('completed')
    expect(vi.mocked(s.client.start).mock.calls[0][0].request_id).not.toBe(vi.mocked(s.client.start).mock.calls[1][0].request_id)
  })
  it('reuses uploaded photos and cached analysis when retrying a decision failure', async () => {
    const s = await setup()
    vi.mocked(s.client.start).mockImplementationOnce(async () => agentRun(s.batch(), { status: 'failed', last_error: 'decision_failed', pending_tool: null,
      tool_feedback: [{ name: null, outcome: 'error', message: 'Model unavailable.', details: { code: 'provider_unavailable' } }] }))
    await s.generation.generate()
    await s.generation.generate()
    expect(s.upload).toHaveBeenCalledTimes(1)
    expect(s.generation.getSnapshot().phase).toBe('completed')
  })
  it('prevents duplicate uploads and ignores cancelled late upload completion', async () => {
    const pending = deferred<ReturnType<typeof prepared>>()
    const s = await setup()
    s.upload.mockReturnValueOnce(pending.promise)
    const first = s.generation.generate()
    await s.generation.generate()
    await s.generation.cancel()
    pending.resolve(prepared([photo()]))
    await first
    expect(s.upload).toHaveBeenCalledTimes(1); expect(s.client.start).not.toHaveBeenCalled()
    expect(s.generation.getSnapshot().phase).toBe('cancelled')
  })
  it('recovers a lost start response with the exact same request ID', async () => {
    const s = await setup()
    vi.mocked(s.client.start).mockRejectedValueOnce(new TypeError('offline'))
    await s.generation.generate()
    expect(s.generation.getSnapshot().recovery).toBe(true)
    await s.generation.generate()
    expect(s.client.start).toHaveBeenCalledTimes(1)
    await s.generation.retry()
    expect(vi.mocked(s.client.start).mock.calls[0][0]).toEqual(vi.mocked(s.client.start).mock.calls[1][0])
    expect(s.upload).toHaveBeenCalledTimes(1)
    expect(s.generation.getSnapshot().phase).toBe('completed')
  })
  it('finds progress using only read-only polling while the start POST remains in flight', async () => {
    const pending = deferred<AgentState>()
    const s = await setup({ start: vi.fn(() => pending.promise) })
    const running = s.generation.generate()
    await vi.waitFor(() => expect(s.client.start).toHaveBeenCalledTimes(1))
    await s.generation.poll()
    expect(s.client.find).toHaveBeenCalledTimes(1); expect(s.client.start).toHaveBeenCalledTimes(1)
    expect(s.generation.getSnapshot().phase).toBe('running')
    pending.resolve(finishedRun(s.batch())); await running
  })
  it('does not let older poll responses replace the completed scene or reset manual editor inputs', async () => {
    const read = deferred<AgentState>(), start = deferred<AgentState>()
    const s = await setup({ start: vi.fn(() => start.promise), find: vi.fn(() => read.promise) })
    const running = s.generation.generate()
    await vi.waitFor(() => expect(s.client.start).toHaveBeenCalledTimes(1))
    const polling = s.generation.poll()
    start.resolve(finishedRun(s.batch())); await running
    const scene = s.generation.getSnapshot().run?.working_scene
    read.resolve(agentRun(s.batch())); await polling
    expect(s.generation.getSnapshot().phase).toBe('completed')
    vi.mocked(s.client.read).mockImplementation(async () => finishedRun(s.batch()))
    await s.generation.poll()
    expect(s.generation.getSnapshot().run?.working_scene).toBe(scene)
  })
  it('locks and retries the exact submitted answer after an ambiguous network failure', async () => {
    const s = await setup({}, '')
    vi.mocked(s.client.start).mockImplementation(async () => waitingRun(s.batch()))
    vi.mocked(s.client.answer).mockRejectedValueOnce(new TypeError('network'))
    await s.generation.generate()
    const answer = { question_id: 'q1', choice_id: 'enter_measurements', dimensions: { height_m: 2.6 } }
    await s.generation.answer(answer)
    answer.dimensions.height_m = 3
    expect(s.generation.getSnapshot().answering).toBe(true)
    expect(s.generation.getSnapshot().recovery).toBe(true)
    await s.generation.retry()
    const calls = vi.mocked(s.client.answer).mock.calls
    expect(calls[0][1]).toEqual(calls[1][1]); expect(calls[1][1].dimensions?.height_m).toBe(2.6)
    expect(s.upload).toHaveBeenCalledTimes(1)
  })
  it('keeps the question available after a definitive validation rejection', async () => {
    const s = await setup({}, '')
    vi.mocked(s.client.start).mockImplementation(async () => waitingRun(s.batch()))
    vi.mocked(s.client.answer).mockRejectedValueOnce(new AgentRunError('Enter a requested measurement.', 422, 'invalid_answer'))
    await s.generation.generate(); await s.generation.answer({ question_id: 'q1', choice_id: 'enter_measurements' })
    expect(s.generation.getSnapshot().notice?.text).toBe('Enter a requested measurement.')
    expect(s.generation.getSnapshot().answering).toBe(false)
    expect(s.generation.getSnapshot().recovery).toBe(false)
    expect(s.generation.getSnapshot().phase).toBe('awaiting_answer')
  })
  it('cancels before the first response without creating another run and ignores late progress', async () => {
    const pending = deferred<AgentState>()
    const s = await setup({ start: vi.fn(() => pending.promise) })
    const running = s.generation.generate()
    await vi.waitFor(() => expect(s.client.start).toHaveBeenCalledTimes(1))
    await s.generation.cancel()
    expect(s.client.find).toHaveBeenCalledTimes(1); expect(s.client.cancel).toHaveBeenCalledTimes(1)
    expect(s.generation.getSnapshot().phase).toBe('cancelled')
    pending.resolve(agentRun(s.batch())); await running
    expect(s.generation.getSnapshot().phase).toBe('cancelled'); expect(s.client.start).toHaveBeenCalledTimes(1)
  })
  it('continues monitoring cooperative cancellation until the server confirms it', async () => {
    const s = await setup()
    vi.mocked(s.client.start).mockImplementation(async () => agentRun(s.batch()))
    vi.mocked(s.client.cancel).mockImplementationOnce(async () => agentRun(s.batch()))
    await s.generation.generate(); await s.generation.cancel()
    expect(s.generation.getSnapshot().cancelling).toBe(true)
    vi.mocked(s.client.read).mockImplementation(async () => agentRun(s.batch(), { status: 'cancelled', pending_tool: null }))
    await s.generation.poll()
    expect(s.generation.getSnapshot().cancelling).toBe(false)
    expect(s.generation.getSnapshot().phase).toBe('cancelled')
  })
  it('invalidates the run and rejects late results when inputs change', async () => {
    const s = await setup()
    await s.generation.generate()
    s.inputs.change({ description: 'Different room details' })
    expect(s.generation.getSnapshot().run).toBeNull()
    await s.generation.generate()
    expect(s.upload).toHaveBeenCalledTimes(2)
    expect(vi.mocked(s.client.start).mock.calls[0][0].request_id).not.toBe(vi.mocked(s.client.start).mock.calls[1][0].request_id)
  })
  it('expires a pending question after a server 404, retaining local photo selections', async () => {
    const s = await setup({}, '')
    vi.mocked(s.client.start).mockImplementation(async () => waitingRun(s.batch()))
    vi.mocked(s.client.answer).mockRejectedValueOnce(new AgentRunError('Expired', 404, 'run_not_found'))
    await s.generation.generate(); await s.generation.answer({ question_id: 'q1', choice_id: 'leave_unknown' })
    expect(s.generation.getSnapshot().phase).toBe('expired')
    expect(s.inputs.getSnapshot().photos).toHaveLength(1)
  })
  it('never starts the agent if upload validation fails', async () => {
    const s = await setup()
    s.inputs.removePhoto(s.inputs.getSnapshot().photos[0].id)
    await s.generation.generate()
    expect(s.upload).not.toHaveBeenCalled(); expect(s.client.start).not.toHaveBeenCalled()
    expect(s.generation.getSnapshot().phase).toBe('idle')
  })
  it('clears an unacknowledged answer when cancellation is confirmed', async () => {
    const pending = deferred<AgentState>()
    const s = await setup({}, '')
    vi.mocked(s.client.start).mockImplementation(async () => waitingRun(s.batch()))
    vi.mocked(s.client.answer).mockReturnValueOnce(pending.promise)
    const cancelled = () => ({ ...waitingRun(s.batch()), status: 'cancelled' as const, pending_question: null })
    vi.mocked(s.client.cancel).mockImplementation(async () => cancelled())
    await s.generation.generate()
    const answering = s.generation.answer({ question_id: 'q1', choice_id: 'leave_unknown' })
    await s.generation.cancel()
    expect(s.generation.getSnapshot().phase).toBe('cancelled')
    expect(s.generation.getSnapshot().answering).toBe(false)
    pending.resolve(cancelled()); await answering
    expect(s.generation.getSnapshot().phase).toBe('cancelled')
  })
})

it.each(['search_furniture', 'get_furniture_details', 'list_furniture_categories'] as const)('describes %s as furniture inspection', name => {
  const input = prepared([photo()])
  const run = agentRun(input, { pending_tool: { call_id: 'mcp_1', name, arguments: {} } })
  expect(generationProgress({ phase: 'running', run, cancelling: false, answering: false, recovery: false, notice: null }))
    .toBe('Checking furniture models and dimensions…')
})
