import { describe, expect, it, vi } from 'vitest'
import { RoomWorkflowController } from './room-workflow-controller'
import { RoomInputController } from './room-input-controller'
import { RoomAnalysisController } from './room-analysis-controller'
import { RoomAnalysisError } from './room-analysis'
import { analyzedRoom, proposedRoom, roomInput } from './test-support/room-analysis'
import { limits, photo } from './test-support/room-input'

type InputClient = NonNullable<ConstructorParameters<typeof RoomInputController>[0]>
type AnalysisClient = NonNullable<ConstructorParameters<typeof RoomAnalysisController>[0]>
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(yes => { resolve = yes })
  return { promise, resolve }
}
async function setup(inputOverrides: Partial<InputClient> = {}, analysisOverrides: Partial<AnalysisClient> = {}) {
  let clock = Date.now(), batch = roomInput(), uploads = 0
  const calls: string[] = []
  const inputClient: InputClient = {
    limits: vi.fn(async () => limits),
    upload: vi.fn(async (_files, options) => {
      calls.push('upload')
      batch = roomInput()
      if (++uploads > 1) batch.input_id = '44444444-4444-4444-8444-444444444444'
      batch.options = options
      batch.expires_at = new Date(clock + 3600000).toISOString()
      return batch
    }), discard: vi.fn(async () => {}), ...inputOverrides,
  }
  const analysisClient: AnalysisClient = {
    analyze: vi.fn(async () => { calls.push('analyze'); return analyzedRoom(batch) }),
    propose: vi.fn(async () => proposedRoom(batch)), ...analysisOverrides,
  }
  const inputs = new RoomInputController(inputClient, { create: () => 'blob:photo', revoke: vi.fn() }, () => clock)
  const analysis = new RoomAnalysisController(analysisClient, () => clock)
  const workflow = new RoomWorkflowController(inputs, analysis)
  const disconnect = workflow.connect()
  await inputs.loadLimits()
  inputs.addPhotos([photo(), photo('other.png')])
  return { inputs, analysis, workflow, inputClient, analysisClient, calls, disconnect, advance: (ms: number) => { clock += ms } }
}

describe('one-action room analysis', () => {
  it('uploads and analyzes in order from a single action, passing the accepted room ID', async () => {
    const { workflow, inputs, analysis, calls, inputClient, analysisClient } = await setup()
    inputs.change({ width: '5', length: '6', height: '2.8' })
    await workflow.analyze()
    expect(calls).toEqual(['upload', 'analyze'])
    expect(inputClient.upload).toHaveBeenCalledTimes(1)
    expect(analysisClient.analyze).toHaveBeenCalledWith(inputs.getSnapshot().accepted?.input_id, expect.any(AbortSignal))
    expect(analysis.getSnapshot().analysis).not.toBeNull()
    expect(analysis.getSnapshot().measurements).toEqual({ width_m: '5', length_m: '6', height_m: '2.8' })
  })
  it('retries failed analysis using the current upload without uploading again', async () => {
    const { workflow, inputClient, analysisClient, analysis } = await setup()
    vi.mocked(analysisClient.analyze).mockRejectedValueOnce(new RoomAnalysisError('Provider busy', 'provider_rate_limited', true, 429))
    await workflow.analyze()
    expect(analysis.getSnapshot().analysis).toBeNull()
    expect(analysis.getSnapshot().notice?.text).toBe('Provider busy')
    await workflow.analyze()
    expect(inputClient.upload).toHaveBeenCalledTimes(1)
    expect(analysisClient.analyze).toHaveBeenCalledTimes(2)
    expect(analysis.getSnapshot().analysis).not.toBeNull()
  })
  it('does not repeat a completed analysis; changed inputs start a new batch', async () => {
    const { workflow, inputs, inputClient, analysisClient, analysis } = await setup()
    await workflow.analyze()
    const first = analysis.getSnapshot().analysis?.input_id
    await workflow.analyze()
    expect(inputClient.upload).toHaveBeenCalledTimes(1)
    expect(analysisClient.analyze).toHaveBeenCalledTimes(1)
    inputs.change({ description: 'Updated room description' })
    expect(analysis.getSnapshot().analysis).toBeNull()
    await workflow.analyze()
    expect(inputClient.upload).toHaveBeenCalledTimes(2)
    expect(analysisClient.analyze).toHaveBeenCalledTimes(2)
    expect(analysis.getSnapshot().analysis?.input_id).not.toBe(first)
  })
  it('never analyzes after an upload failure or invalid photo selection', async () => {
    const { workflow, inputs, inputClient, analysisClient } = await setup()
    inputs.removePhoto(inputs.getSnapshot().photos[0].id)
    inputs.removePhoto(inputs.getSnapshot().photos[0].id)
    await workflow.analyze()
    expect(inputClient.upload).not.toHaveBeenCalled()
    expect(analysisClient.analyze).not.toHaveBeenCalled()
    inputs.addPhotos([photo(), photo('other.png')])
    vi.mocked(inputClient.upload).mockRejectedValueOnce(new Error('offline'))
    await workflow.analyze()
    expect(analysisClient.analyze).not.toHaveBeenCalled()
    await workflow.analyze()
    expect(analysisClient.analyze).toHaveBeenCalledTimes(1)
  })
  it('prevents duplicate uploads and ignores late upload completion after cancellation', async () => {
    const pending = deferred<ReturnType<typeof roomInput>>()
    const { workflow, inputClient, analysisClient, analysis } = await setup({ upload: vi.fn(() => pending.promise) })
    const running = workflow.analyze()
    await workflow.analyze()
    expect(inputClient.upload).toHaveBeenCalledTimes(1)
    workflow.cancel()
    expect(vi.mocked(inputClient.upload).mock.calls[0][2].aborted).toBe(true)
    pending.resolve(roomInput())
    await running
    expect(analysisClient.analyze).not.toHaveBeenCalled()
    expect(analysis.getSnapshot().analysis).toBeNull()
  })
  it('can retry immediately after cancellation while ignoring an older upload response', async () => {
    const pending = deferred<ReturnType<typeof roomInput>>()
    const { workflow, inputClient, analysisClient, analysis } = await setup()
    vi.mocked(inputClient.upload).mockReturnValueOnce(pending.promise)
    const old = workflow.analyze()
    workflow.cancel()
    await workflow.analyze()
    const current = analysis.getSnapshot().analysis
    expect(current).not.toBeNull()
    pending.resolve(roomInput())
    await old
    expect(analysis.getSnapshot().analysis).toBe(current)
    expect(analysisClient.analyze).toHaveBeenCalledTimes(1)
  })
  it('cancels analysis and reuses the uploaded photos when retrying', async () => {
    const pending = deferred<ReturnType<typeof analyzedRoom>>()
    const { workflow, inputClient, analysisClient, analysis } = await setup()
    vi.mocked(analysisClient.analyze).mockReturnValueOnce(pending.promise)
    const first = workflow.analyze()
    // Observe the stage transition without timers or starting another request.
    await vi.waitFor(() => expect(analysis.getSnapshot().busy).toBe('analysis'))
    workflow.cancel()
    expect(vi.mocked(analysisClient.analyze).mock.calls[0][1].aborted).toBe(true)
    await workflow.analyze()
    const current = analysis.getSnapshot().analysis
    pending.resolve(analyzedRoom())
    await first
    expect(inputClient.upload).toHaveBeenCalledTimes(1)
    expect(analysisClient.analyze).toHaveBeenCalledTimes(2)
    expect(analysis.getSnapshot().analysis).toBe(current)
  })
  it('automatically replaces expired or backend-revoked uploads on the next action', async () => {
    const { workflow, advance, inputClient, analysisClient, analysis } = await setup()
    vi.mocked(analysisClient.analyze).mockRejectedValueOnce(new RoomAnalysisError('Gone', 'input_not_found', true, 404))
    await workflow.analyze()
    await workflow.analyze()
    expect(inputClient.upload).toHaveBeenCalledTimes(2)
    expect(analysis.getSnapshot().analysis).not.toBeNull()
    advance(3600001)
    await workflow.analyze()
    expect(inputClient.upload).toHaveBeenCalledTimes(3)
    expect(analysisClient.analyze).toHaveBeenCalledTimes(3)
  })
  it('disconnects safely during preparation without starting analysis afterward', async () => {
    const pending = deferred<ReturnType<typeof roomInput>>()
    const { workflow, disconnect, analysisClient, analysis } = await setup({ upload: vi.fn(() => pending.promise) })
    const running = workflow.analyze()
    disconnect()
    pending.resolve(roomInput())
    await running
    expect(analysisClient.analyze).not.toHaveBeenCalled()
    expect(analysis.getSnapshot().input).toBeNull()
  })
})
