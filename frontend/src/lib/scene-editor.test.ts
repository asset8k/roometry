import { describe, expect, it, vi } from 'vitest'
import { SceneEditor } from './scene-editor'
import { loadExampleScene, loadAlternativeScene } from './scene'
import type { PreviewEditResponse } from './edit-response.generated'
import { acceptedMove, rejectedMove } from './test-support/edit-responses'
import { applyColorPreviews } from './color-previews'

const move = { operation: 'move' as const, object_id: 'desk_1', position: { x: 0.85, z: 2 } }

function deferred() {
  let resolve!: (result: PreviewEditResponse) => void
  let reject!: (error: Error) => void
  const promise = new Promise<PreviewEditResponse>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

describe('working scene editor', () => {
  it('commits only a validated response and preserves the bundled source', async () => {
    const source = loadExampleScene()
    const before = structuredClone(source)
    const request = deferred()
    const client = vi.fn(() => request.promise)
    const editor = new SceneEditor(source, client)
    editor.select('desk_1')
    const completion = editor.submit(move)
    expect(editor.getSnapshot().busy).toBe(true)
    expect(editor.getSnapshot().draft).not.toBeNull()
    expect(editor.getSnapshot().scene).toEqual(before)
    request.resolve(acceptedMove(source))
    expect(await completion).toBe(true)
    expect(editor.getSnapshot().scene.objects.find(object => object.id === 'desk_1')!.position.x).toBe(0.85)
    expect(editor.getSnapshot().draft).toBeNull()
    expect(editor.getSnapshot().busy).toBe(false)
    expect(source).toEqual(before)
  })

  it('rolls back an invalid candidate and shows affected object names', async () => {
    const source = loadExampleScene()
    const editor = new SceneEditor(source, async () => rejectedMove(source))
    editor.select('desk_1')
    expect(await editor.submit(move)).toBe(false)
    expect(editor.getSnapshot().scene).toEqual(source)
    expect(editor.getSnapshot().draft).toBeNull()
    expect(editor.getSnapshot().notice?.kind).toBe('rejected')
    expect(editor.getSnapshot().notice?.details?.[0]).toContain('bed / desk')
  })

  it('restores the accepted scene on network/HTTP/response validation errors', async () => {
    const source = loadExampleScene()
    const editor = new SceneEditor(source, async () => { throw new Error('Offline') })
    editor.select('desk_1')
    expect(await editor.submit(move)).toBe(false)
    expect(editor.getSnapshot().scene).toEqual(source)
    expect(editor.getSnapshot().notice?.kind).toBe('error')
    expect(editor.getSnapshot().notice?.connection).toBe(false)
    expect(editor.getSnapshot().busy).toBe(false)
  })

  it('prevents overlapping requests and selection/mode changes while checking', async () => {
    const source = loadExampleScene()
    const request = deferred()
    const client = vi.fn(() => request.promise)
    const editor = new SceneEditor(source, client)
    editor.select('desk_1')
    const first = editor.submit(move)
    expect(await editor.submit(move)).toBe(false)
    editor.select('rug_1')
    editor.setMode('rotate')
    expect(editor.getSnapshot().selectedId).toBe('desk_1')
    expect(editor.getSnapshot().mode).toBe('move')
    expect(client).toHaveBeenCalledTimes(1)
    request.resolve(acceptedMove(source))
    await first
  })

  it.each(['late-success', 'late-error'])('cancellation ignores %s after a newer accepted edit', async late => {
    const source = loadExampleScene()
    const old = deferred(), next = deferred()
    const client = vi.fn().mockReturnValueOnce(old.promise).mockReturnValueOnce(next.promise)
    const editor = new SceneEditor(source, client)
    editor.select('desk_1')
    const first = editor.submit(move)
    const signal = client.mock.calls[0][2] as AbortSignal
    editor.cancel()
    expect(signal.aborted).toBe(true)
    expect(editor.getSnapshot().scene).toEqual(source)
    const second = editor.submit({ ...move, position: { x: 0.9, z: 2 } })
    next.resolve(acceptedMove(source, 0.9))
    expect(await second).toBe(true)
    if (late === 'late-success') old.resolve(acceptedMove(source, 0.85))
    else old.reject(new Error('Old failed request'))
    expect(await first).toBe(false)
    expect(editor.getSnapshot().scene.objects.find(object => object.id === 'desk_1')!.position.x).toBe(0.9)
    expect(editor.getSnapshot().notice?.kind).toBe('success')
  })

  it('uses temporary drag candidates, supports cancellation, and ignores stale release events', async () => {
    const source = loadExampleScene()
    const client = vi.fn()
    const editor = new SceneEditor(source, client)
    editor.select('desk_1')
    expect(editor.beginDrag()).toBe(true)
    editor.previewDrag(move)
    expect(editor.getSnapshot().draft?.objects.find(object => object.id === 'desk_1')!.position.x).toBe(0.85)
    expect(editor.getSnapshot().scene).toEqual(source)
    editor.select('rug_1')
    expect(editor.getSnapshot().selectedId).toBe('desk_1')
    editor.cancel()
    expect(await editor.finishDrag(move)).toBe(false)
    expect(editor.getSnapshot().draft).toBeNull()
    expect(client).not.toHaveBeenCalled()
  })

  it('submits a released drag exactly once', async () => {
    const source = loadExampleScene()
    const client = vi.fn(async () => acceptedMove(source))
    const editor = new SceneEditor(source, client)
    editor.select('desk_1')
    editor.beginDrag()
    editor.previewDrag(move)
    expect(await editor.finishDrag(move)).toBe(true)
    expect(await editor.finishDrag(move)).toBe(false)
    expect(client).toHaveBeenCalledTimes(1)
  })

  it('allows inspection of protected objects but never submits their edits', async () => {
    const client = vi.fn()
    const editor = new SceneEditor(loadExampleScene(), client)
    editor.select('bed_1')
    expect(editor.getSnapshot().selectedId).toBe('bed_1')
    expect(editor.beginDrag()).toBe(false)
    expect(await editor.submit({ ...move, object_id: 'bed_1' })).toBe(false)
    expect(client).not.toHaveBeenCalled()
  })

  it('keeps independent color previews across accepted and rejected layout edits', async () => {
    const source = loadExampleScene()
    const client = vi.fn().mockResolvedValueOnce(acceptedMove(source)).mockResolvedValueOnce(rejectedMove(source))
    const editor = new SceneEditor(source, client)
    const colors = { rug_1: '#327E57' }
    editor.select('desk_1')
    await editor.submit(move)
    expect(applyColorPreviews(editor.getSnapshot().scene, colors).objects.find(object => object.id === 'rug_1')!.color).toBe('#327E57')
    await editor.submit({ ...move, position: { x: 2.7, z: 2 } })
    expect(applyColorPreviews(editor.getSnapshot().scene, colors).objects.find(object => object.id === 'rug_1')!.color).toBe('#327E57')
  })

  it('treats a normalized no-op as local and cleans up without disabling future edits', async () => {
    const source = loadExampleScene()
    const client = vi.fn(async () => acceptedMove(source))
    const editor = new SceneEditor(source, client)
    editor.select('desk_1')
    expect(await editor.submit({ operation: 'rotate', object_id: 'desk_1', rotation_y_rad: Math.PI * 2 })).toBe(true)
    expect(client).not.toHaveBeenCalled()
    editor.cancel(false) // Also safe during React StrictMode's cleanup/remount.
    expect(await editor.submit(move)).toBe(true)
  })

  it('rejects nonfinite input rather than misreporting it as a no-op', async () => {
    const client = vi.fn()
    const editor = new SceneEditor(loadExampleScene(), client)
    editor.select('desk_1')
    expect(await editor.submit({ ...move, position: { x: NaN, z: 2 } })).toBe(false)
    expect(editor.getSnapshot().notice?.kind).toBe('error')
    expect(client).not.toHaveBeenCalled()
  })
})

describe('before/after comparison', () => {
  it('starts in After, preserves accepted edits across switches, and leaves both sources untouched', async () => {
    const before = loadExampleScene(), after = loadAlternativeScene()
    const originalBefore = structuredClone(before), originalAfter = structuredClone(after)
    const client = vi.fn(async () => acceptedMove(after, 0.7))
    const editor = new SceneEditor(after, client, before)
    expect(editor.getSnapshot().view).toBe('after')
    editor.select('desk_1')
    await editor.submit({ ...move, position: { x: 0.7, z: 1.35 } })
    const edited = editor.getSnapshot().scene
    editor.switchView('before')
    expect(editor.getSnapshot()).toMatchObject({ view: 'before', scene: before, selectedId: null, notice: null, draft: null })
    editor.select('desk_1')
    editor.switchView('after')
    expect(editor.getSnapshot()).toMatchObject({ view: 'after', scene: edited, selectedId: null, notice: null })
    expect(before).toEqual(originalBefore)
    expect(after).toEqual(originalAfter)
  })

  it('allows Before inspection but blocks drag, numeric edits, modes, and layout reset', async () => {
    const before = loadExampleScene(), after = loadAlternativeScene(), client = vi.fn()
    const editor = new SceneEditor(after, client, before)
    editor.switchView('before')
    editor.select('desk_1')
    expect(editor.getSnapshot().selectedId).toBe('desk_1')
    expect(editor.beginDrag()).toBe(false)
    editor.previewDrag(move)
    expect(await editor.finishDrag(move)).toBe(false)
    expect(await editor.submit(move)).toBe(false)
    editor.setMode('rotate')
    editor.resetLayout()
    expect(editor.getSnapshot()).toMatchObject({ scene: before, mode: 'move', draft: null, selectedId: 'desk_1' })
    expect(client).not.toHaveBeenCalled()
  })

  it.each(['switch', 'reset'])('cancels a drag on %s and ignores its release', async action => {
    const after = loadAlternativeScene(), client = vi.fn()
    const editor = new SceneEditor(after, client, loadExampleScene())
    editor.select('desk_1')
    editor.beginDrag()
    editor.previewDrag(move)
    if (action === 'switch') editor.switchView('before')
    else editor.resetLayout()
    expect(editor.getSnapshot()).toMatchObject({ draft: null, selectedId: null, busy: false, dragging: false })
    expect(await editor.finishDrag(move)).toBe(false)
    expect(client).not.toHaveBeenCalled()
    editor.switchView('after')
    expect(editor.getSnapshot().scene).toEqual(after)
  })

  it.each(['switch-success', 'switch-error', 'reset-success', 'reset-error'])('ignores a stale response after %s', async action => {
    const after = loadAlternativeScene(), before = loadExampleScene(), pending = deferred()
    const client = vi.fn<NonNullable<ConstructorParameters<typeof SceneEditor>[1]>>(() => pending.promise)
    const editor = new SceneEditor(after, client, before)
    editor.select('desk_1')
    const completion = editor.submit(move)
    const signal = client.mock.calls[0][2] as AbortSignal
    if (action.startsWith('switch')) {
      editor.switchView('before')
      editor.switchView('after')
    } else editor.resetLayout()
    expect(signal.aborted).toBe(true)
    const snapshot = editor.getSnapshot()
    if (action.endsWith('success')) pending.resolve(acceptedMove(after))
    else pending.reject(new Error('Late request failure'))
    expect(await completion).toBe(false)
    expect(editor.getSnapshot()).toBe(snapshot)
    expect(editor.getSnapshot().scene).toEqual(after)
  })

  it('resets to the authored After poses while keeping color previews independent', async () => {
    const after = loadAlternativeScene(), before = loadExampleScene()
    const editor = new SceneEditor(after, async () => acceptedMove(after), before)
    const colors = { desk_1: '#204080', rug_1: '#DD8855' }
    editor.select('desk_1')
    await editor.submit(move)
    editor.resetLayout()
    expect(editor.getSnapshot()).toMatchObject({ scene: after, selectedId: null, draft: null, busy: false })
    expect(applyColorPreviews(editor.getSnapshot().scene, colors).objects.find(object => object.id === 'desk_1')!.color).toBe('#204080')
    editor.switchView('before')
    expect(editor.getSnapshot().scene).toEqual(before)
    editor.switchView('after')
    expect(applyColorPreviews(editor.getSnapshot().scene, colors).objects.find(object => object.id === 'rug_1')!.color).toBe('#DD8855')
    expect(applyColorPreviews(editor.getSnapshot().scene, {})).toEqual(after)
  })
})
