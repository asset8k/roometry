import { describe, expect, it, vi, afterEach } from 'vitest'
import { SceneComparison } from './scene-comparison'
import { parseOpenedProject, projectsClient } from './projects'
import type { OpenedProject } from './projects'
import { prepared } from './test-support/room-input'
import { finishedRun } from './test-support/agent-run'
import { preparedImprovement } from './test-support/optimization'
import { RoomGenerationController } from './room-generation-controller'
import { RoomInputController } from './room-input-controller'
import { checkOptimization } from './optimization'
import type { OptimizationRun } from './optimization'
import type { AgentClient } from './agent-api'

const id = '12345678-1234-4234-8234-123456789abc'
function savedWorkspace(): OpenedProject {
  const run = { ...finishedRun(prepared()), persistence: 'account' as const }
  const original = structuredClone(run.working_scene!), comparison = new SceneComparison(original)
  const baseline = preparedImprovement(run, { request_id: id, focus: 'aesthetics' })
  const candidate = structuredClone(original); candidate.objects[0].color = '#AABBCC'
  const improved: OptimizationRun = { ...baseline, status: 'completed' as const, result_scene: candidate,
    attempts: [{ number: 1, outcome: 'accepted' as const, before_score: 4, after_score: 8, changed_object_ids: [candidate.objects[0].id], issues: [], summary: 'Warmer colors.', proposal: null, review: null }] }
  comparison.offer(improved, improved.source_scene); comparison.editCopy()
  const object = candidate.objects.find(item => !item.fixed && !item.locked)!
  comparison.setColor(object.id, '#123456'); comparison.toggleLock(object.id)
  const snapshot = comparison.snapshotForSave()
  const versions = [{ id: 'original', scene: original }, { id: improved.run_id, scene: candidate }, ...snapshot.manual_copies.map(copy => ({ id: `manual:${copy.parent_run_id ?? 'original'}`, scene: copy.scene }))]
  return { run, project: { id, title: 'My workspace', revision: 4, visibility: 'private', images: [{ id, url: '/private-photo' }], versions,
    snapshot: { improvements: { [improved.run_id]: improved }, messages: [{ id: 'private', role: 'user', text: 'Keep my desk here.' }],
      versions: versions.map(version => ({ key: version.id, version_id: version.id, parent_key: version.id.startsWith('manual:') ? version.id.slice(7) : null })),
      comparison: { view: snapshot.view, editing_parent_run_id: snapshot.editing_parent_run_id, selected_improvement_id: snapshot.selected_improvement_id } } } }
}
afterEach(() => vi.unstubAllGlobals())
describe('reopening a private workspace', () => {
  it('restores comparison selection, chat, edited colors and locks without changing Original or Improved', () => {
    const saved = parseOpenedProject(savedWorkspace()), comparison = SceneComparison.restore(saved)
    const expected = saved.project.versions.find(version => version.id === `manual:${saved.project.snapshot.comparison.editing_parent_run_id}`)!.scene
    expect(comparison.getSnapshot().view).toBe('editing')
    expect(comparison.currentContext().scene).toEqual(expected)
    expect(saved.project.snapshot.messages[0].text).toBe('Keep my desk here.')
    comparison.show('original'); expect(comparison.currentContext().scene).toEqual(saved.run.working_scene)
    comparison.show('improved'); expect(comparison.currentContext().scene).toEqual(Object.values(saved.project.snapshot.improvements)[0].result_scene)
    comparison.show('editing'); expect(comparison.currentContext().scene).toEqual(expected)
    comparison.getSnapshot().editor.resetLayout()
    expect(Object.keys(comparison.getSnapshot().colors)).not.toHaveLength(0)
  })
  it('rejects missing version links instead of opening a different layout', () => {
    const saved = savedWorkspace(); saved.project.versions = []
    expect(() => parseOpenedProject(saved)).toThrow('scene version')
  })
  it('uses explicit credentialed owner-only open, rename and delete requests', async () => {
    const value = savedWorkspace(), fetcher = vi.fn(async (_url: unknown, options?: RequestInit) => options?.method === 'DELETE' ? new Response(null, { status: 204 }) : new Response(JSON.stringify(value)))
    vi.stubGlobal('fetch', fetcher)
    await projectsClient.open(id, new AbortController().signal)
    expect(fetcher).toHaveBeenLastCalledWith(`/api/projects/${id}/open`, expect.objectContaining({ method: 'POST', credentials: 'include', body: '{}' }))
    const summary = { id, title: 'Room', revision: 4, room_type: 'bedroom', updated_at: new Date().toISOString(), visibility: 'private' as const }
    await projectsClient.rename(summary, 'Renamed', new AbortController().signal)
    expect(fetcher).toHaveBeenLastCalledWith(`/api/projects/${id}`, expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ title: 'Renamed', expected_revision: 4 }) }))
    await projectsClient.delete(summary, new AbortController().signal)
    expect(fetcher).toHaveBeenLastCalledWith(`/api/projects/${id}`, expect.objectContaining({ method: 'DELETE', body: JSON.stringify({ expected_revision: 4 }) }))
  })
  it('restores a registered run without starting generation or cancelling it on unmount', () => {
    const run = { ...finishedRun(prepared()), persistence: 'account' as const }
    const client: AgentClient = { start: vi.fn(), find: vi.fn(), read: vi.fn(), answer: vi.fn(), cancel: vi.fn() }
    const controller = new RoomGenerationController(new RoomInputController(), client)
    controller.restore(prepared(), run)
    const disconnect = controller.connect(); disconnect()
    expect(controller.getSnapshot().run?.run_id).toBe(run.run_id)
    expect(client.start).not.toHaveBeenCalled(); expect(client.cancel).not.toHaveBeenCalled()
  })
})

it('accepts equal JSONB scenes with reordered keys, while rejecting changed positions', () => {
  const saved = savedWorkspace(), comparison = SceneComparison.restore(saved)
  const context = comparison.currentContext()
  const reorder = (value: unknown): unknown => Array.isArray(value) ? value.map(reorder) : value && typeof value === 'object'
    ? Object.fromEntries(Object.entries(value).reverse().map(([key, item]) => [key, reorder(item)])) : value
  const source = saved.run
  const request = { request_id: id, focus: 'aesthetics' as const, context }
  const response = { ...preparedImprovement(source, request), source_scene: reorder(context.scene) as typeof context.scene,
    parent_run_id: context.parent_run_id }
  expect(() => checkOptimization(response, source, request)).not.toThrow()
  const changed = structuredClone(response)
  changed.source_scene.objects[0].position.x += .5
  expect(() => checkOptimization(changed, source, request)).toThrow('does not match')
})

it('remembers explicit copy creation even when saved on Original with unchanged furniture', () => {
  const saved = savedWorkspace()
  saved.project.snapshot.comparison.view = 'original'
  saved.project.snapshot.comparison.editing_parent_run_id = null
  saved.project.snapshot.comparison.has_editable_copy = true
  for (const entry of saved.project.snapshot.versions.filter(v => v.key.startsWith('manual:'))) {
    const version = saved.project.versions.find(v => v.id === entry.version_id)!
    version.scene = structuredClone(entry.parent_key === 'original' ? saved.run.working_scene! : saved.project.snapshot.improvements[entry.parent_key!].result_scene!)
  }
  const comparison = SceneComparison.restore(parseOpenedProject(saved))
  expect(comparison.getSnapshot()).toMatchObject({ view: 'original', hasEditableCopy: true })
  comparison.show('editing')
  expect(comparison.getSnapshot().view).toBe('editing')
})
