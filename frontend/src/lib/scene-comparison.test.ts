import { describe, expect, it, vi } from 'vitest'
import { SceneComparison, retainedImprovement } from './scene-comparison'
import { applyColorPreviews } from './color-previews'
import { loadExampleScene, loadAlternativeScene } from './test-support/scenes'
import { acceptedMove } from './test-support/edit-responses'
import { preparedImprovement } from './test-support/optimization'
import { finishedRun } from './test-support/agent-run'
import { prepared } from './test-support/room-input'
import type { OptimizationRun } from './optimization'
import type { PreviewEditResponse } from './edit-response.generated'

function candidate(id = '88888888-8888-4888-8888-888888888888'): OptimizationRun {
  const result = preparedImprovement(finishedRun(prepared()), { request_id: id, focus: 'workspace' })
  return { ...result, run_id: id, source_scene: loadExampleScene(), result_scene: loadAlternativeScene(), status: 'completed', stage: 'finished',
    attempts: [{ number: 1, outcome: 'accepted', before_score: 6, after_score: 8, changed_object_ids: ['desk_1'], issues: [], summary: 'Place the desk with more usable space.', review: null }] }
}
const move = { operation: 'move' as const, object_id: 'desk_1', position: { x: .85, z: 2 } }
const deskX = (comparison: SceneComparison) => comparison.getSnapshot().editor.getSnapshot().scene.objects.find(obj => obj.id === 'desk_1')!.position.x

describe('original/improved comparison and independent editable copies', () => {
  it('retains manual edits and color previews when an improvement arrives, without changing saved scenes', async () => {
    const original = loadExampleScene(), improved = candidate(), before = structuredClone(improved)
    const comparison = new SceneComparison(original, async scene => acceptedMove(scene))
    comparison.editCopy()
    const originalEditor = comparison.getSnapshot().editor
    originalEditor.select('desk_1'); await originalEditor.submit(move)
    comparison.setColor('rug_1', '#DD8855')
    expect(comparison.offer(improved)).toBe(true)
    expect(comparison.getSnapshot().view).toBe('improved')
    expect(comparison.getSnapshot().editor.getSnapshot().view).toBe('before')
    expect(comparison.getSnapshot().editor.getSnapshot().scene).toEqual(improved.result_scene)
    comparison.show('original')
    expect(comparison.getSnapshot().editor.getSnapshot().scene).toEqual(original)
    comparison.show('editing')
    expect(deskX(comparison)).toBe(.85)
    expect(comparison.getSnapshot().colors).toEqual({ rug_1: '#DD8855' })
    expect(original).toEqual(loadExampleScene())
    expect(improved).toEqual(before)
  })

  it('keeps the improved snapshot read-only and restores its baseline when resetting only its manual copy', async () => {
    const improved = candidate()
    const client = vi.fn(async scene => acceptedMove(scene, .9))
    const comparison = new SceneComparison(loadExampleScene(), client)
    comparison.offer(improved)
    const editor = comparison.getSnapshot().editor
    editor.select('desk_1')
    expect(await editor.submit(move)).toBe(false)
    comparison.setColor('rug_1', '#112233')
    expect(comparison.getSnapshot().colors).toEqual({})
    expect(client).not.toHaveBeenCalled()
    comparison.editCopy()
    editor.select('desk_1'); await editor.submit(move)
    comparison.setColor('rug_1', '#112233')
    expect(deskX(comparison)).toBe(.9)
    editor.resetLayout()
    expect(editor.getSnapshot().scene).toEqual(improved.result_scene)
    expect(comparison.getSnapshot().colors).toEqual({ rug_1: '#112233' })
    comparison.show('original'); comparison.editCopy()
    expect(comparison.getSnapshot().editor.getSnapshot().scene).toEqual(loadExampleScene())
    expect(comparison.getSnapshot().colors).toEqual({})
    comparison.show('improved')
    expect(comparison.getSnapshot().editor.getSnapshot().scene).toEqual(improved.result_scene)
    comparison.editCopy()
    expect(applyColorPreviews(editor.getSnapshot().scene, comparison.getSnapshot().colors).objects.find(obj => obj.id === 'rug_1')!.color).toBe('#112233')
  })

  it('retains earlier candidates and their manual copies when another focus produces a result', async () => {
    const first = candidate(), second = candidate('99999999-9999-4999-8999-999999999999')
    second.focus = 'aesthetics'
    second.result_scene!.objects.find(obj => obj.id === 'rug_1')!.color = '#CCDDBB'
    const comparison = new SceneComparison(loadExampleScene(), async scene => acceptedMove(scene, .8))
    comparison.offer(first); comparison.editCopy()
    comparison.getSnapshot().editor.select('desk_1'); await comparison.getSnapshot().editor.submit(move)
    comparison.setColor('rug_1', '#335577')
    comparison.offer(second)
    expect(comparison.getSnapshot().improvements).toHaveLength(2)
    comparison.selectImprovement(first.run_id); comparison.editCopy()
    expect(deskX(comparison)).toBe(.8)
    expect(comparison.getSnapshot().colors).toEqual({ rug_1: '#335577' })
    comparison.selectImprovement(second.run_id); comparison.editCopy()
    expect(comparison.getSnapshot().colors).toEqual({})
    expect(comparison.getSnapshot().editor.getSnapshot().scene).toEqual(second.result_scene)
    expect(comparison.offer(structuredClone(first))).toBe(false)
    expect(comparison.getSnapshot().improvements).toHaveLength(2)
  })

  it('does not open a fabricated improvement for an unchanged, unaccepted, active, or foreign result', () => {
    const comparison = new SceneComparison(loadExampleScene())
    expect(comparison.offer(null)).toBe(false)
    expect(comparison.offer({ ...candidate(), status: 'running' })).toBe(false)
    expect(comparison.offer({ ...candidate(), status: 'no_improvement', result_scene: null })).toBe(false)
    expect(comparison.offer({ ...candidate(), attempts: [] })).toBe(false)
    expect(comparison.offer({ ...candidate(), result_scene: loadExampleScene() })).toBe(false)
    const foreign = candidate(); foreign.source_scene.room.width_m += 1
    expect(comparison.offer(foreign)).toBe(false)
    expect(comparison.getSnapshot().improvements).toHaveLength(0)
    expect(comparison.getSnapshot().view).toBe('original')
  })

  it.each(['failed', 'budget_exhausted', 'cancelled'] as const)('offers the best retained candidate after %s, with that outcome intact', status => {
    const comparison = new SceneComparison(loadExampleScene())
    const result = { ...candidate(), status }
    expect(retainedImprovement(result)).toBe(true)
    expect(comparison.offer(result)).toBe(true)
    expect(comparison.getSnapshot().improvements[0].status).toBe(status)
  })

  it('defers automatic switching during a manual request and ignores a stale edit after a user switches views', async () => {
    let release!: (result: PreviewEditResponse) => void
    const client = vi.fn(() => new Promise<PreviewEditResponse>(resolve => { release = resolve }))
    const comparison = new SceneComparison(loadExampleScene(), client)
    comparison.editCopy()
    const editor = comparison.getSnapshot().editor
    editor.select('desk_1')
    const pending = editor.submit(move)
    comparison.offer(candidate())
    expect(comparison.getSnapshot().editor).toBe(editor)
    expect(comparison.getSnapshot().view).toBe('editing')
    expect(editor.getSnapshot().busy).toBe(true)
    comparison.show('improved')
    expect(editor.getSnapshot().busy).toBe(false)
    release(acceptedMove(loadExampleScene())); expect(await pending).toBe(false)
    comparison.show('original'); comparison.editCopy()
    expect(editor.getSnapshot().scene).toEqual(loadExampleScene())
  })
})

it('opens Original read-only and creates a separate copy only after explicit editing', async () => {
  const original = loadExampleScene(), client = vi.fn(async scene => acceptedMove(scene))
  const comparison = new SceneComparison(original, client)
  const editor = comparison.getSnapshot().editor
  expect(comparison.getSnapshot()).toMatchObject({ view: 'original', hasEditableCopy: false })
  expect(editor.getSnapshot().view).toBe('before')
  editor.select('desk_1')
  expect(await editor.submit(move)).toBe(false)
  comparison.setColor('rug_1', '#123456')
  expect(client).not.toHaveBeenCalled()
  expect(comparison.currentContext().scene).toEqual(original)
  comparison.show('editing')
  expect(comparison.getSnapshot().view).toBe('original')
  comparison.editCopy()
  expect(comparison.getSnapshot()).toMatchObject({ view: 'editing', hasEditableCopy: true })
  editor.select('desk_1'); expect(await editor.submit(move)).toBe(true)
  comparison.show('original')
  expect(comparison.currentContext().scene).toEqual(original)
  expect(comparison.snapshotForSave().has_editable_copy).toBe(true)
  comparison.show('editing')
  expect(deskX(comparison)).toBe(.85)
})
