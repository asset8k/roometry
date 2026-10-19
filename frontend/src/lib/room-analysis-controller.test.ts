import { describe, expect, it, vi } from 'vitest'
import { RoomAnalysisController } from './room-analysis-controller'
import { RoomAnalysisError } from './room-analysis'
import { analyzedRoom, proposedRoom, needsDimensions, roomInput } from './test-support/room-analysis'
import { RoomInputController } from './room-input-controller'
import { limits, photo } from './test-support/room-input'

type Client = NonNullable<ConstructorParameters<typeof RoomAnalysisController>[0]>
function setup(overrides: Partial<Client> = {}) {
  const input = roomInput()
  const client: Client = { analyze: vi.fn(async () => analyzedRoom(input)), propose: vi.fn(async () => proposedRoom(input)), ...overrides }
  const controller = new RoomAnalysisController(client)
  controller.setInput(input)
  return { input, client, controller }
}
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (value: Error) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

describe('room reconstruction lifecycle', () => {
  it('analyzes once, confirms missing dimensions, and opens a validated base scene', async () => {
    const { controller, client, input } = setup()
    await controller.analyze()
    expect(controller.getSnapshot()).toMatchObject({ busy: null, measurements: { width_m: '4', length_m: '4', height_m: '' }, confirmed: false })
    expect(controller.getSnapshot().analysis?.observations.possible_duplicates).toHaveLength(1)
    vi.mocked(client.propose).mockResolvedValueOnce(needsDimensions(input))
    await controller.build()
    expect(controller.getSnapshot().proposal?.status).toBe('needs_dimensions')
    expect(controller.getSnapshot().scene).toBeNull()
    controller.changeMeasurements({ height_m: '2.6' })
    controller.confirm(true)
    await controller.build()
    expect(client.analyze).toHaveBeenCalledTimes(1)
    expect(client.propose).toHaveBeenLastCalledWith(expect.objectContaining({ analysis_id: analyzedRoom(input).analysis_id,
      dimensions: { width_m: 4, length_m: 4, height_m: 2.6, measurements_source: 'user_confirmed' } }), expect.any(AbortSignal))
    expect(controller.getSnapshot().scene?.objects).toHaveLength(5)
    expect(controller.getSnapshot().proposal?.status).toBe('ready')
    await controller.analyze()
    expect(client.analyze).toHaveBeenCalledTimes(1)
  })
  it('keeps confirmed measurements instead of conflicting photo estimates', async () => {
    const { input, controller, client } = setup()
    const analysis = analyzedRoom(input)
    analysis.observations.estimated_dimensions = { width_m: 9, length_m: 8, height_m: 2.7 }
    vi.mocked(client.analyze).mockResolvedValueOnce(analysis)
    await controller.analyze()
    expect(controller.getSnapshot().measurements).toEqual({ width_m: '4', length_m: '4', height_m: '2.7' })
    controller.acceptEstimates(true)
    await controller.build()
    expect(client.propose).toHaveBeenCalledWith({ analysis_id: analysis.analysis_id, allow_estimated_dimensions: true }, expect.any(AbortSignal))
  })
  it('requires explicit confirmation for typed edits and does not relabel them as estimates', async () => {
    const { controller, client } = setup()
    await controller.analyze()
    controller.changeMeasurements({ height_m: '2.6' })
    await controller.build()
    expect(client.propose).not.toHaveBeenCalled()
    expect(controller.getSnapshot().notice?.text).toContain('confirmation')
    controller.acceptEstimates(true)
    await controller.build()
    expect(client.propose).not.toHaveBeenCalled()
    controller.confirm(true)
    await controller.build()
    expect(client.propose).toHaveBeenCalledTimes(1)
  })
  it.each(['0', '-1', 'Infinity', '11', 'NaN', '0x2'])('rejects invalid room height %s before making a scene call', async height_m => {
    const { controller, client } = setup()
    await controller.analyze()
    controller.changeMeasurements({ height_m })
    controller.confirm(true)
    await controller.build()
    expect(client.propose).not.toHaveBeenCalled()
    expect(controller.getSnapshot().notice?.kind).toBe('error')
  })
  it('resets confirmation when measurements change and blocks duplicate submissions', async () => {
    const pending = deferred<ReturnType<typeof proposedRoom>>()
    const { controller, client } = setup({ propose: vi.fn(() => pending.promise) })
    await controller.analyze()
    controller.changeMeasurements({ height_m: '2.6' })
    controller.confirm(true)
    controller.changeMeasurements({ width_m: '4.1' })
    expect(controller.getSnapshot().confirmed).toBe(false)
    controller.changeMeasurements({ width_m: '4' })
    controller.confirm(true)
    const first = controller.build()
    await controller.build()
    controller.changeMeasurements({ width_m: '7' })
    expect(controller.getSnapshot().measurements.width_m).toBe('4')
    expect(client.propose).toHaveBeenCalledTimes(1)
    pending.resolve(proposedRoom())
    await first
    expect(controller.getSnapshot().scene).not.toBeNull()
  })
  it('retains observations and measurements on provider failure and retries without reanalyzing', async () => {
    const { controller, client } = setup()
    await controller.analyze()
    controller.changeMeasurements({ height_m: '2.6' })
    controller.confirm(true)
    vi.mocked(client.propose).mockRejectedValueOnce(new RoomAnalysisError('Provider busy', 'provider_rate_limited', true, 429))
    await controller.build()
    expect(controller.getSnapshot()).toMatchObject({ busy: null, measurements: { height_m: '2.6' }, notice: { kind: 'error', text: 'Provider busy' } })
    await controller.build()
    expect(client.analyze).toHaveBeenCalledTimes(1)
    expect(controller.getSnapshot().scene).not.toBeNull()
  })
  it('keeps a previous accepted scene when a new proposal is incomplete', async () => {
    const { controller, client } = setup()
    await controller.analyze()
    controller.changeMeasurements({ height_m: '2.6' })
    controller.confirm(true)
    await controller.build()
    const previous = controller.getSnapshot().scene
    vi.mocked(client.propose).mockResolvedValueOnce({ ...proposedRoom(), status: 'incomplete', scene: null, validation: null,
      issues: [{ code: 'proposal_limit_reached', entity_ids: [], message: 'Attempt limit reached.' }] })
    await controller.build()
    expect(controller.getSnapshot().scene).toBe(previous)
    expect(controller.getSnapshot().notice?.text).toContain('last validated')
  })
  it.each(['success', 'failure'])('ignores late analysis %s after replacing the uploaded room', async late => {
    const pending = deferred<ReturnType<typeof analyzedRoom>>()
    const { controller, client } = setup({ analyze: vi.fn(() => pending.promise) })
    const running = controller.analyze()
    const next = roomInput()
    next.input_id = '44444444-4444-4444-8444-444444444444'
    controller.setInput(next)
    const current = controller.getSnapshot()
    expect(vi.mocked(client.analyze).mock.calls[0][1].aborted).toBe(true)
    if (late === 'success') pending.resolve(analyzedRoom())
    else pending.reject(new Error('late'))
    await running
    expect(controller.getSnapshot()).toBe(current)
    expect(controller.getSnapshot().analysis).toBeNull()
  })
  it.each(['success', 'failure'])('ignores late scene %s after cancellation', async late => {
    const pending = deferred<ReturnType<typeof proposedRoom>>()
    const { controller, client } = setup({ propose: vi.fn(() => pending.promise) })
    await controller.analyze()
    controller.changeMeasurements({ height_m: '2.6' })
    controller.confirm(true)
    const running = controller.build()
    controller.cancel()
    const current = controller.getSnapshot()
    expect(vi.mocked(client.propose).mock.calls[0][1].aborted).toBe(true)
    if (late === 'success') pending.resolve(proposedRoom())
    else pending.reject(new Error('late'))
    await running
    expect(controller.getSnapshot()).toBe(current)
    expect(controller.getSnapshot().scene).toBeNull()
    expect(controller.getSnapshot().analysis).not.toBeNull()
  })
  it('rejects analysis referencing other photos and scene responses for another room', async () => {
    const { controller, client } = setup()
    const foreign = analyzedRoom()
    foreign.observations.image_ids.reverse()
    vi.mocked(client.analyze).mockResolvedValueOnce(foreign)
    await controller.analyze()
    expect(controller.getSnapshot().analysis).toBeNull()
    await controller.analyze()
    controller.changeMeasurements({ height_m: '2.6' })
    controller.confirm(true)
    vi.mocked(client.propose).mockResolvedValueOnce({ ...proposedRoom(), input_id: '44444444-4444-4444-8444-444444444444' })
    await controller.build()
    expect(controller.getSnapshot().scene).toBeNull()
    expect(controller.getSnapshot().notice?.kind).toBe('error')
  })
  it('expires state before a scene request and discards results that arrive after expiry', async () => {
    let now = Date.now()
    const input = roomInput()
    const pending = deferred<ReturnType<typeof analyzedRoom>>()
    const client: Client = { analyze: vi.fn(() => pending.promise), propose: vi.fn(async () => proposedRoom(input)) }
    const controller = new RoomAnalysisController(client, () => now)
    controller.setInput(input)
    const running = controller.analyze()
    now = Date.parse(input.expires_at)
    pending.resolve(analyzedRoom(input))
    await running
    expect(controller.getSnapshot().analysis).toBeNull()
    expect(controller.getSnapshot().notice?.text).toContain('expired')
    await controller.build()
    expect(client.propose).not.toHaveBeenCalled()
  })
  it('invalidates cached analysis and scene after form changes, discard, and release', async () => {
    const { controller, input } = setup()
    await controller.analyze()
    controller.setInput(input, false)
    expect(controller.getSnapshot().analysis).toBeNull()
    controller.setInput(input)
    await controller.analyze()
    controller.release()
    expect(controller.getSnapshot()).toMatchObject({ input: null, analysis: null, scene: null })
  })
  it('does not resurrect an input that the backend has revoked', async () => {
    const { controller, input } = setup({ analyze: vi.fn(async () => { throw new RoomAnalysisError('Expired', 'input_not_found', true, 404) }) })
    await controller.analyze()
    controller.setInput(input)
    expect(controller.getSnapshot().input).toBeNull()
  })
  it('connects upload, analysis, dimension confirmation, scene validation, and discard', async () => {
    const input = roomInput()
    const inputs = new RoomInputController({ limits: async () => limits, upload: async () => input, discard: async () => {} }, { create: () => 'blob:photo', revoke: vi.fn() })
    const { controller: analysis, client } = setup()
    analysis.setInput(null)
    const sync = () => {
      const state = inputs.getSnapshot()
      analysis.setInput(state.accepted, !state.busy && !state.dirty && !state.uncertain)
    }
    const unsubscribe = inputs.subscribe(sync)
    await inputs.loadLimits()
    inputs.addPhotos([photo(), photo()])
    await inputs.submit()
    await analysis.analyze()
    analysis.changeMeasurements({ height_m: '2.6' })
    analysis.confirm(true)
    await analysis.build()
    expect(analysis.getSnapshot().scene?.room.height_m).toBe(2.6)
    expect(client.analyze).toHaveBeenCalledTimes(1)
    await inputs.discard()
    expect(analysis.getSnapshot()).toMatchObject({ analysis: null, scene: null, input: null })
    unsubscribe()
  })
})

it('requires confirmation again if a response fills a previously blank estimate', async () => {
  const input = roomInput(), analysis = analyzedRoom(input)
  const pending = needsDimensions(input)
  pending.dimensions = { width_m: 4, length_m: 4, height_m: 2.6, measurements_source: 'estimated' }
  pending.estimated_axes = ['height_m']
  const client: Client = { analyze: vi.fn(async () => analysis), propose: vi.fn(async () => pending) }
  const controller = new RoomAnalysisController(client)
  controller.setInput(input)
  await controller.analyze()
  controller.confirm(true)
  await controller.build()
  expect(controller.getSnapshot().measurements.height_m).toBe('2.6')
  expect(controller.getSnapshot().confirmed).toBe(false)
  await controller.build()
  expect(client.propose).toHaveBeenCalledTimes(1)
  expect(controller.getSnapshot().notice?.text).toContain('confirmation')
})
