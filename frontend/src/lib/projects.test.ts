import { afterEach, describe, expect, it, vi } from 'vitest'
import { ProjectSaveController, ProjectError, parseSavedProject, saveProject } from './projects'
import type { SaveProjectRequest, SavedProject } from './projects'
import { SceneComparison } from './scene-comparison'
import { loadExampleScene } from './test-support/scenes'
import { acceptedMove } from './test-support/edit-responses'

const owner = 'first-user'
const saved: SavedProject = { id: '12345678-1234-4234-8234-123456789abc', title: 'Bedroom', revision: 1, image_count: 5, version_count: 2, saved_at: new Date().toISOString(), visibility: 'private' }
const source = '11111111-1111-4111-8111-111111111111'
const comparison = () => new SceneComparison(loadExampleScene(), async scene => acceptedMove(scene, .9))
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (reason: unknown) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no }); return { resolve, reject, promise } }
afterEach(() => vi.unstubAllGlobals())

describe('private project saves', () => {
  it('captures accepted manual geometry, colors and locks even while Original is visible', async () => {
    const views = comparison(); views.editCopy()
    const editor = views.getSnapshot().editor
    editor.select('desk_1')
    await editor.submit({ operation: 'move', object_id: 'desk_1', position: { x: .9, z: 2 } })
    views.setColor('rug_1', '#aabbcc'); views.toggleLock('rug_1')
    views.show('original')
    const snapshot = views.snapshotForSave()
    expect(snapshot.view).toBe('original')
    const copy = snapshot.manual_copies[0].scene
    expect(copy.objects.find(object => object.id === 'desk_1')!.position.x).toBe(.9)
    expect(copy.objects.find(object => object.id === 'rug_1')).toMatchObject({ color: '#AABBCC', locked: true })
    expect(views.currentContext().scene).toEqual(loadExampleScene())
    copy.objects[0].color = '#111111'
    expect(views.snapshotForSave().manual_copies[0].scene.objects[0].color).not.toBe('#111111')
  })
  it('requires an explicit signed-in save and keeps updates linked to their revision', async () => {
    const api = vi.fn(async (_body: SaveProjectRequest) => saved), controller = new ProjectSaveController(api), views = comparison()
    expect(await controller.save(source, 'Bedroom', views.snapshotForSave, [])).toBe(false)
    expect(api).not.toHaveBeenCalled()
    controller.setOwner(owner)
    await controller.save(source, 'Bedroom', views.snapshotForSave, [])
    expect(api.mock.calls[0][0]).toMatchObject({ project_id: null, expected_revision: 0, source_run_id: source })
    await controller.save(source, 'Bedroom', views.snapshotForSave, [])
    expect(api.mock.calls[1][0]).toMatchObject({ project_id: saved.id, expected_revision: 1 })
    controller.release()
  })
  it('deduplicates clicks and retries the exact frozen payload after a lost response', async () => {
    const first = deferred<SavedProject>(), bodies: SaveProjectRequest[] = []
    let calls = 0
    const api = vi.fn(async (body: SaveProjectRequest) => { bodies.push(structuredClone(body)); if (++calls === 1) return first.promise; return saved })
    const controller = new ProjectSaveController(api), views = comparison()
    controller.setOwner(owner)
    const attempt = controller.save(source, 'Bedroom', views.snapshotForSave, [{ id: 'one', role: 'user', text: 'Private chat' }])
    expect(await controller.save(source, 'Other title', views.snapshotForSave, [])).toBe(false)
    first.reject(new ProjectError('Connection lost'))
    expect(await attempt).toBe(false)
    views.setColor('rug_1', '#123456')
    await controller.save(source, 'Changed title', views.snapshotForSave, [])
    expect(bodies[1]).toEqual(bodies[0])
    expect(controller.getSnapshot()).toMatchObject({ saved, pending: false, retry: false, error: null })
  })
  it('isolates receipts and stale responses when the signed-in account changes', async () => {
    const first = deferred<SavedProject>(), api = vi.fn(() => first.promise), controller = new ProjectSaveController(api)
    controller.setOwner(owner)
    const attempt = controller.save(source, 'Bedroom', comparison().snapshotForSave, [])
    controller.setOwner('another-user')
    first.resolve(saved); await attempt
    expect(controller.getSnapshot()).toMatchObject({ owner: 'another-user', saved: null, pending: false })
    controller.setOwner(owner)
    expect(controller.getSnapshot().retry).toBe(true)
    expect(controller.getSnapshot().saved).toBeNull()
  })
  it('releases rejected requests rather than silently retrying invalid edits', async () => {
    const api = vi.fn(async () => { throw new ProjectError('Scene needs valid placement.', 422) })
    const controller = new ProjectSaveController(api); controller.setOwner(owner)
    await controller.save(source, 'Bedroom', comparison().snapshotForSave, [])
    expect(controller.getSnapshot()).toMatchObject({ pending: false, retry: false, error: 'Scene needs valid placement.' })
  })
  it('sends private credentialed JSON and rejects incomplete save confirmations', async () => {
    const api = vi.fn(async () => new Response(JSON.stringify(saved), { status: 200 }))
    vi.stubGlobal('fetch', api)
    const body: SaveProjectRequest = { ...comparison().snapshotForSave(), request_id: source, source_run_id: source, title: 'Bedroom', project_id: null, expected_revision: 0, messages: [] }
    expect(await saveProject(body, new AbortController().signal)).toEqual(saved)
    expect(api).toHaveBeenCalledWith('/api/projects', expect.objectContaining({ method: 'POST', credentials: 'include', cache: 'no-store', body: JSON.stringify(body) }))
    expect(() => parseSavedProject({ ...saved, visibility: 'public' })).toThrow()
    expect(() => parseSavedProject({ ...saved, id: 'invalid' })).toThrow()
    expect(() => parseSavedProject({ ...saved, image_count: 0 })).toThrow()
  })
})
