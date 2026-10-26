import { describe, expect, it, vi } from 'vitest'
import { WorkspaceNavigation, isRoomRoute, protectPageUnload } from './workspace-navigation'
import { ProjectSaveController, savedWorkspaceSignature, ProjectError } from './projects'
import { RoomGenerationController } from './room-generation-controller'
import { RoomInputController } from './room-input-controller'
import { OptimizationController } from './optimization-controller'
import { SceneComparison } from './scene-comparison'
import { AgentRunError } from './agent-run'
import type { AgentClient } from './agent-api'
import type { OptimizationClient, OptimizationRun } from './optimization'
import type { SavedProject } from './projects'
import { preparedImprovement } from './test-support/optimization'
import { finishedRun, waitingRun } from './test-support/agent-run'
import { limits, prepared } from './test-support/room-input'

const receipt: SavedProject = { id: '12345678-1234-4234-8234-123456789abc', title: 'Saved room', revision: 1, image_count: 1, version_count: 2, visibility: 'private', saved_at: new Date().toISOString() }
const next = { hash: '#', fresh: true }
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(yes => { resolve = yes }); return { resolve, promise } }

describe('workspace departure', () => {
  it('keeps unsaved work when staying and commits a fresh room only after confirmation', async () => {
    const commit = vi.fn(), leave = vi.fn(async () => {}), navigation = new WorkspaceNavigation(commit)
    navigation.register(Symbol(), () => ({ dirty: true, hasWork: true, leave }))
    navigation.request(next)
    expect(navigation.getSnapshot().pending).toEqual(next)
    expect(commit).not.toHaveBeenCalled(); expect(leave).not.toHaveBeenCalled()
    navigation.stay(); expect(navigation.getSnapshot().pending).toBeNull()
    navigation.request(next); expect(await navigation.confirm()).toBe(true)
    expect(commit).toHaveBeenCalledExactlyOnceWith(next); expect(leave).toHaveBeenCalledTimes(1)
  })
  it('does not prompt for a clean saved room or a fresh empty workspace', async () => {
    const commit = vi.fn(), navigation = new WorkspaceNavigation(commit)
    const release = navigation.register(Symbol(), () => ({ dirty: false, hasWork: true }))
    navigation.request({ hash: '#my-rooms' }); await vi.waitFor(() => expect(commit).toHaveBeenCalledTimes(1))
    expect(navigation.getSnapshot().pending).toBeNull()
    release(); expect(navigation.getSnapshot().hasWork).toBe(false)
    navigation.request(next); await vi.waitFor(() => expect(commit).toHaveBeenCalledTimes(2))
  })
  it('waits for saves and prevents duplicate cancellation or departure clicks', async () => {
    let saving = true
    const stop = deferred<void>(), leave = vi.fn(() => stop.promise), commit = vi.fn()
    const navigation = new WorkspaceNavigation(commit)
    navigation.register(Symbol(), () => ({ dirty: true, saving, leave }))
    navigation.request(next); expect(await navigation.confirm()).toBe(false)
    expect(leave).not.toHaveBeenCalled()
    saving = false
    const departure = navigation.confirm()
    expect(await navigation.confirm()).toBe(false)
    navigation.stay(); expect(navigation.getSnapshot().pending).toEqual(next)
    stop.resolve(); expect(await departure).toBe(true)
    expect(leave).toHaveBeenCalledTimes(1); expect(commit).toHaveBeenCalledTimes(1)
  })
  it('stops improvements before their source and keeps the workspace on uncertain cancellation', async () => {
    const events: string[] = [], commit = vi.fn(), navigation = new WorkspaceNavigation(commit)
    navigation.register(Symbol(), () => ({ dirty: true, priority: 20, leave: async () => { events.push('source') } }))
    let fail = true
    navigation.register(Symbol(), () => ({ dirty: true, active: true, priority: 10, leave: async () => { events.push('improvement'); if (fail) throw new Error('Cancellation unconfirmed') } }))
    navigation.request(next); expect(await navigation.confirm()).toBe(false)
    expect(events).toEqual(['improvement']); expect(commit).not.toHaveBeenCalled()
    expect(navigation.getSnapshot().error).toBe('Cancellation unconfirmed')
    fail = false; expect(await navigation.confirm()).toBe(true)
    expect(events).toEqual(['improvement', 'improvement', 'source'])
  })
  it('protects browser refresh/close only while unsaved work or active requests exist', () => {
    let dirty = false, active = false
    const navigation = new WorkspaceNavigation(vi.fn())
    navigation.register(Symbol(), () => ({ dirty, active }))
    const event = { preventDefault: vi.fn(), returnValue: undefined } as unknown as BeforeUnloadEvent
    protectPageUnload(navigation, event); expect(event.preventDefault).not.toHaveBeenCalled()
    dirty = true; protectPageUnload(navigation, event); expect(event.preventDefault).toHaveBeenCalledTimes(1)
    expect(event.returnValue).toBe('')
    dirty = false; active = true; protectPageUnload(navigation, event); expect(event.preventDefault).toHaveBeenCalledTimes(2)
  })
  it('only intercepts room routes, preserving skip links, outside links and separate browser tabs', () => {
    for (const hash of ['#', '#explore', '#my-rooms', '#room/12345678-1234-4234-8234-123456789abc', '#run/12345678-1234-4234-8234-123456789abc']) expect(isRoomRoute(hash)).toBe(true)
    for (const hash of ['#main-content', 'https://example.com/', '#room/invalid', 'javascript:alert(1)']) expect(isRoomRoute(hash)).toBe(false)
  })
})

describe('saved versus unsaved workspace content', () => {
  it('clears the dirty baseline only for the exact confirmed save, preserving edits made before a frozen retry', async () => {
    const source = finishedRun(prepared()), comparison = new SceneComparison(source.working_scene!)
    let requests = 0
    const saves = new ProjectSaveController(async () => { if (++requests === 1) throw new ProjectError('Response lost'); return receipt })
    saves.setOwner('owner')
    const initial = savedWorkspaceSignature(source.run_id, comparison.snapshotForSave(), [])
    expect(saves.confirmedSignature()).toBeUndefined()
    await saves.save(source.run_id, 'Room', comparison.snapshotForSave, [])
    expect(saves.confirmedSignature()).toBeUndefined()
    const object = source.working_scene!.objects.find(o => !o.locked)!
    comparison.editCopy()
    comparison.setColor(object.id, '#123456'); comparison.toggleLock(object.id)
    const edited = savedWorkspaceSignature(source.run_id, comparison.snapshotForSave(), [])
    expect(edited).not.toBe(initial)
    await saves.save(source.run_id, 'Room', comparison.snapshotForSave, [])
    expect(saves.confirmedSignature()).toBe(initial) // Retry saved the old frozen request.
    expect(saves.confirmedSignature()).not.toBe(edited)
    await saves.save(source.run_id, 'Room', comparison.snapshotForSave, [])
    expect(saves.confirmedSignature()).toBe(edited)
    expect(savedWorkspaceSignature(source.run_id, comparison.snapshotForSave(), [{ id: 'one', role: 'user', text: 'New request' }])).not.toBe(edited)
  })
})

describe('departure never replays paid requests', () => {
  it.each([false, true])('cancels a %s-account reconstruction explicitly without uploading, restarting or answering', async account => {
    const input = prepared(), waiting = { ...waitingRun(input), persistence: account ? 'account' as const : 'temporary' as const }
    const client: AgentClient = { start: vi.fn(), find: vi.fn(), read: vi.fn(), answer: vi.fn(), cancel: vi.fn(async () => ({ ...waiting, status: 'cancelled' as const, pending_question: null })) }
    const inputs = new RoomInputController({ limits: async () => limits, upload: vi.fn(), discard: vi.fn() })
    const generation = new RoomGenerationController(inputs, client)
    generation.restore(input, waiting)
    await generation.leaveWorkspace()
    expect(generation.getSnapshot().phase).toBe('cancelled')
    expect(client.cancel).toHaveBeenCalledTimes(1)
    expect(client.start).not.toHaveBeenCalled(); expect(client.answer).not.toHaveBeenCalled()
  })
  it('retains an uncertain active reconstruction when stopping fails', async () => {
    const input = prepared(), waiting = { ...waitingRun(input), persistence: 'account' as const }
    const client: AgentClient = { start: vi.fn(), find: vi.fn(), read: vi.fn(), answer: vi.fn(), cancel: vi.fn(async () => { throw new AgentRunError('Offline') }) }
    const generation = new RoomGenerationController(new RoomInputController(), client)
    generation.restore(input, waiting)
    await expect(generation.leaveWorkspace()).rejects.toThrow('Couldn’t confirm')
    expect(generation.getSnapshot().run?.run_id).toBe(waiting.run_id)
    expect(client.start).not.toHaveBeenCalled()
  })
  it.each(['openness', 'storage', 'workspace', 'aesthetics'] as const)('cancels a prepared %s improvement without executing it', async focus => {
    const source = { ...finishedRun(prepared()), persistence: 'account' as const }
    let run: OptimizationRun
    const client: OptimizationClient = { prepare: vi.fn(async (_id, request) => { run = preparedImprovement(source, request); return run }),
      read: vi.fn(), execute: vi.fn(), cancel: vi.fn(async () => ({ ...run!, status: 'cancelled' as const, stage: 'finished' as const })) }
    const optimization = new OptimizationController(source, client)
    optimization.select(focus); await optimization.prepare(); await optimization.leaveWorkspace()
    expect(optimization.getSnapshot().phase).toBe('cancelled')
    expect(client.cancel).toHaveBeenCalledTimes(1); expect(client.execute).not.toHaveBeenCalled()
    expect(client.prepare).toHaveBeenCalledTimes(1)
  })
  it('recovers a lost preparation using the same request ID and cancels without any paid execution', async () => {
    const source = { ...finishedRun(prepared()), persistence: 'account' as const }
    const first = deferred<OptimizationRun>()
    let run: OptimizationRun
    const client: OptimizationClient = {
      prepare: vi.fn(async (_id, request) => { run = preparedImprovement(source, request); return vi.mocked(client.prepare).mock.calls.length === 1 ? first.promise : run }),
      execute: vi.fn(), read: vi.fn(), cancel: vi.fn(async () => ({ ...run!, status: 'cancelled' as const, stage: 'finished' as const })),
    }
    const controller = new OptimizationController(source, client)
    controller.select('storage')
    const starting = controller.start()
    await controller.leaveWorkspace()
    first.resolve(run!); await starting
    expect(controller.getSnapshot().phase).toBe('cancelled')
    expect(client.prepare).toHaveBeenCalledTimes(2)
    expect(vi.mocked(client.prepare).mock.calls[0][1].request_id).toBe(vi.mocked(client.prepare).mock.calls[1][1].request_id)
    expect(client.execute).not.toHaveBeenCalled(); expect(client.cancel).toHaveBeenCalledTimes(1)
  })
  it.each(['openness', 'storage', 'workspace', 'aesthetics'] as const)('stops an in-flight %s request without replaying execution', async focus => {
    const source = { ...finishedRun(prepared()), persistence: 'account' as const }
    const execution = deferred<OptimizationRun>()
    let run: OptimizationRun
    const client: OptimizationClient = { prepare: vi.fn(async (_id, request) => { run = preparedImprovement(source, request); return run }),
      execute: vi.fn(() => execution.promise), read: vi.fn(), cancel: vi.fn(async () => ({ ...run!, status: 'cancelled' as const, stage: 'finished' as const })) }
    const controller = new OptimizationController(source, client)
    controller.select(focus)
    const starting = controller.start()
    await vi.waitFor(() => expect(client.execute).toHaveBeenCalledTimes(1))
    await controller.leaveWorkspace()
    const stopped = controller.getSnapshot().result!
    execution.resolve(stopped); await starting
    expect(controller.getSnapshot().phase).toBe('cancelled')
    expect(client.execute).toHaveBeenCalledTimes(1); expect(client.cancel).toHaveBeenCalledTimes(1)
  })
  it('keeps retrying cancellation rather than abandoning a prepared result after a rejected stop', async () => {
    const source = { ...finishedRun(prepared()), persistence: 'account' as const }
    let run: OptimizationRun, fail = true
    const client: OptimizationClient = { prepare: vi.fn(async (_id, request) => { run = preparedImprovement(source, request); return run }),
      execute: vi.fn(), read: vi.fn(), cancel: vi.fn(async () => { if (fail) throw new AgentRunError('Busy', 409); return { ...run!, status: 'cancelled' as const, stage: 'finished' as const } }) }
    const controller = new OptimizationController(source, client)
    controller.select('storage'); await controller.prepare()
    await expect(controller.leaveWorkspace()).rejects.toThrow('Couldn’t confirm')
    fail = false; await controller.leaveWorkspace()
    expect(controller.getSnapshot().phase).toBe('cancelled')
    expect(client.cancel).toHaveBeenCalledTimes(2); expect(client.execute).not.toHaveBeenCalled()
  })
})
