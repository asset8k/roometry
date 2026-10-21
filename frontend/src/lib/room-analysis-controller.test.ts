import { describe, expect, it, vi } from 'vitest'
import { RoomAnalysisController } from './room-analysis-controller'
import { RoomAnalysisError } from './room-analysis'
import { analyzedRoom, proposedRoom, needsDimensions, roomInput, partialRoom } from './test-support/room-analysis'
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
    expect(controller.getSnapshot()).toMatchObject({ busy: null, measurements: { width_m: '4', length_m: '4', height_m: '' }, enteredAxes: ['width_m', 'length_m'] })
    expect(controller.getSnapshot().analysis?.observations.possible_duplicates).toHaveLength(1)
    vi.mocked(client.propose).mockResolvedValueOnce(needsDimensions(input))
    await controller.build()
    expect(controller.getSnapshot().proposal?.status).toBe('needs_dimensions')
    expect(controller.getSnapshot().scene).toBeNull()
    controller.changeMeasurements({ height_m: '2.6' })
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
    await controller.build()
    expect(client.propose).toHaveBeenCalledWith({ analysis_id: analysis.analysis_id, allow_estimated_dimensions: true, dimensions: { width_m: 4, length_m: 4, measurements_source: 'user_confirmed' } }, expect.any(AbortSignal))
  })
  it('uses typed edits directly and keeps untouched photo estimates separate', async () => {
    const { controller, client, input } = setup()
    const analysis = analyzedRoom(input)
    analysis.observations.estimated_dimensions = { width_m: 9, length_m: 8, height_m: 2.7 }
    vi.mocked(client.analyze).mockResolvedValueOnce(analysis)
    await controller.analyze()
    controller.changeMeasurements({ width_m: '5' })
    await controller.build()
    expect(client.propose).toHaveBeenCalledTimes(1)
    expect(client.propose).toHaveBeenCalledWith({ analysis_id: analysis.analysis_id, allow_estimated_dimensions: true,
      dimensions: { width_m: 5, length_m: 4, measurements_source: 'user_confirmed' } }, expect.any(AbortSignal))
    // The displayed height is still a photo estimate, never a submitted user value.
    expect(vi.mocked(client.propose).mock.calls[0][0].dimensions?.height_m).toBeUndefined()
  })
  it.each(['0', '-1', 'Infinity', '11', 'NaN', '0x2'])('rejects invalid room height %s before making a scene call', async height_m => {
    const { controller, client } = setup()
    await controller.analyze()
    controller.changeMeasurements({ height_m })
    await controller.build()
    expect(client.propose).not.toHaveBeenCalled()
    expect(controller.getSnapshot().notice?.kind).toBe('error')
  })
  it('accepts corrected dimensions directly and blocks duplicate submissions', async () => {
    const pending = deferred<ReturnType<typeof proposedRoom>>()
    const { controller, client } = setup({ propose: vi.fn(() => pending.promise) })
    await controller.analyze()
    controller.changeMeasurements({ height_m: '2.6' })
    controller.changeMeasurements({ width_m: '4.1' })
    expect(controller.getSnapshot().enteredAxes).toContain('width_m')
    controller.changeMeasurements({ width_m: '4' })
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
    await controller.build()
    const previous = controller.getSnapshot().scene
    vi.mocked(client.propose).mockResolvedValueOnce({ ...proposedRoom(), status: 'incomplete', scene: null, validation: null,
      issues: [{ code: 'proposal_limit_reached', entity_ids: [], message: 'Attempt limit reached.' }] })
    await controller.build()
    expect(controller.getSnapshot().scene).toBe(previous)
    expect(controller.getSnapshot().notice?.text).toContain('last validated')
  })
  it('opens a partial scene and keeps its unresolved metadata on a failed update', async () => {
    const { controller, client, input } = setup({ propose: vi.fn(async () => partialRoom()) })
    await controller.analyze()
    controller.changeMeasurements({ height_m: '2.6' })
    await controller.build()
    const previous = controller.getSnapshot().scene
    expect(previous?.objects).toHaveLength(4)
    expect(controller.getSnapshot().sceneProposal?.status).toBe('partial')
    expect(controller.getSnapshot().notice?.text).toContain('partial')
    vi.mocked(client.propose).mockResolvedValueOnce({ ...proposedRoom(input), status: 'incomplete', scene: null, validation: null,
      issues: [{ code: 'proposal_limit_reached', entity_ids: [], message: 'Attempt limit reached.' }] })
    await controller.build()
    expect(controller.getSnapshot().scene).toBe(previous)
    expect(controller.getSnapshot().sceneProposal?.unresolved_entities?.[0].entity_id).toBe('bed_observed')
    expect(client.analyze).toHaveBeenCalledTimes(1)
    controller.release()
    expect(controller.getSnapshot().sceneProposal).toBeNull()
  })
  it.each(['missing', 'invented', 'wrong_kind'])('rejects a partial result with %s observation accounting', async defect => {
    const invalid = partialRoom()
    if (defect === 'missing') invalid.scene!.objects.pop()
    if (defect === 'invented') invalid.unresolved_entities![0].entity_id = 'invented'
    if (defect === 'wrong_kind') invalid.unresolved_entities![0].kind = 'opening'
    const { controller } = setup({ propose: vi.fn(async () => invalid) })
    await controller.analyze()
    await controller.build()
    expect(controller.getSnapshot().scene).toBeNull()
    expect(controller.getSnapshot().notice?.text).toContain('every observed')
  })
  it('replaces partial metadata when a complete scene becomes available', async () => {
    const { controller, client } = setup({ propose: vi.fn(async () => partialRoom()) })
    await controller.analyze()
    await controller.build()
    vi.mocked(client.propose).mockResolvedValueOnce(proposedRoom())
    await controller.build()
    expect(controller.getSnapshot().scene?.objects).toHaveLength(5)
    expect(controller.getSnapshot().sceneProposal?.status).toBe('ready')
    expect(controller.getSnapshot().sceneProposal?.unresolved_entities).toEqual([])
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
    await analysis.build()
    expect(analysis.getSnapshot().scene?.room.height_m).toBe(2.6)
    expect(client.analyze).toHaveBeenCalledTimes(1)
    await inputs.discard()
    expect(analysis.getSnapshot()).toMatchObject({ analysis: null, scene: null, input: null })
    unsubscribe()
  })
})

it('keeps a newly filled estimate separate on subsequent submissions', async () => {
  const input = roomInput(), analysis = analyzedRoom(input)
  const pending = needsDimensions(input)
  pending.dimensions = { width_m: 4, length_m: 4, height_m: 2.6, measurements_source: 'estimated' }
  pending.estimated_axes = ['height_m']
  const client: Client = { analyze: vi.fn(async () => analysis), propose: vi.fn(async () => pending) }
  const controller = new RoomAnalysisController(client)
  controller.setInput(input)
  await controller.analyze()
  await controller.build()
  expect(controller.getSnapshot().measurements.height_m).toBe('2.6')
  expect(controller.getSnapshot().enteredAxes).not.toContain('height_m')
  await controller.build()
  expect(client.propose).toHaveBeenCalledTimes(2)
  expect(client.propose).toHaveBeenLastCalledWith({ analysis_id: analysis.analysis_id, allow_estimated_dimensions: true,
    dimensions: { width_m: 4, length_m: 4, measurements_source: 'user_confirmed' } }, expect.any(AbortSignal))
})
