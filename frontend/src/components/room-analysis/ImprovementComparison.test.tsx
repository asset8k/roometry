import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import ImprovementComparison, { sceneChanges } from './ImprovementComparison'
import { ComparisonControls } from '../room-viewer/SceneWorkspace'
import { SceneComparison } from '../../lib/scene-comparison'
import { OptimizationController } from '../../lib/optimization-controller'
import type { OptimizationRun, OptimizationClient } from '../../lib/optimization'
import { finishedRun } from '../../lib/test-support/agent-run'
import { prepared } from '../../lib/test-support/room-input'
import { preparedImprovement } from '../../lib/test-support/optimization'

function result(): OptimizationRun {
  const source = finishedRun(prepared())
  const run = preparedImprovement(source, { request_id: '77777777-7777-4777-8777-777777777777', focus: 'workspace' })
  const card: NonNullable<OptimizationRun['baseline_assessment']>['workspace'] = {
    status: 'rated', score: 7, confidence: 'medium', source: 'geometry', explanation: 'Estimated from reconstructed geometry.', entity_ids: [], metrics: [],
  }
  run.baseline_assessment = { policy_version: 'room-assessment-v1', scene_hash: run.source_scene_hash, openness: card, storage: card,
    workspace: { ...card, score: 6 }, aesthetics: { ...card, score: 8, source: 'photos' }, recommended: 'workspace', notes: [] }
  run.result_assessment = { ...structuredClone(run.baseline_assessment), scene_hash: 'b'.repeat(64), workspace: { ...card, score: 8 },
    aesthetics: { ...card, status: 'unassessed', score: null, confidence: 'low', source: 'photos' }, recommended: 'openness' }
  run.status = 'completed'; run.stage = 'finished'; run.result_scene = structuredClone(run.source_scene)
  const desk = run.result_scene.objects.find(obj => obj.id === 'desk_observed')!
  desk.position.x -= .4; desk.rotation_y_rad = Math.PI / 2; desk.color = '#AA8877'
  run.result_scene.objects.find(obj => obj.id === 'computer_observed')!.position.x -= .4
  run.attempts = [{ number: 1, outcome: 'accepted', before_score: 6, after_score: 8, changed_object_ids: ['desk_observed', 'computer_observed'],
    issues: [], summary: 'Reposition the desk.', proposal: null, review: { preferred: 'candidate', confidence: 'medium', constraints_satisfied: true, explanation: 'The desk has more usable approach space.', aesthetics: null } }]
  return run
}

const labels = { desk_observed: 'Work desk', computer_observed: 'Desk monitor' }
describe('saved improvement comparison', () => {
  it('compares the same four categories, selected focus and measured gain without inventing an aesthetic rating', () => {
    const run = result()
    run.focus = 'aesthetics'
    const html = renderToStaticMarkup(<ImprovementComparison run={run} labels={labels} />)
    for (const text of ['Space &amp; movement', 'Storage &amp; organization', 'Workspace', 'Aesthetics &amp; cohesion',
      '6 / 10', '8 / 10', '+2', 'Selected focus', 'Reviewed qualitatively', 'No new numerical taste rating', 'Later manual edits are not included']) expect(html).toContain(text)
    expect(html).not.toContain('NaN')
    expect(html.match(/scope="row"/g)).toHaveLength(4)
  })

  it('describes actual position, orientation, support-child and color differences with human names', () => {
    const run = result()
    const before = structuredClone(run)
    const html = renderToStaticMarkup(<ImprovementComparison run={run} labels={labels} />)
    for (const text of ['2 objects changed', 'Work desk', 'Desk monitor', 'Moved 0.4 m', 'Turned 90°', '#AA8877', 'with its supporting furniture']) expect(html).toContain(text)
    expect(html).not.toContain('desk_observed')
    expect(html).not.toContain('computer_observed')
    expect(run).toEqual(before)
  })

  it('marks unavailable and non-applicable estimates without treating them as zero', () => {
    const run = result()
    run.baseline_assessment!.workspace = { ...run.baseline_assessment!.workspace, score: null, status: 'not_applicable', confidence: 'low' }
    run.result_assessment!.workspace = { ...run.result_assessment!.workspace, score: null, status: 'unassessed', confidence: 'low' }
    const html = renderToStaticMarkup(<ImprovementComparison run={run} />)
    expect(html).toContain('Not applicable')
    expect(html).toContain('Not assessed')
    expect(html).not.toContain('0 / 10')
    expect(html).not.toContain('+2')
    expect(html).not.toContain('Reviewed qualitatively')
  })

  it('compares rendered rubric aspects without subtracting them from the original photo rating', () => {
    const run = result(); run.focus = 'aesthetics'
    run.attempts[0]!.review!.aesthetics = {
      retained: { color_harmony: 1, material_cohesion: 1, visual_balance: 1, visual_order: 1, lighting_composition: 2 },
      candidate: { color_harmony: 2, material_cohesion: 2, visual_balance: 1, visual_order: 1, lighting_composition: 2 },
      confidence: 'medium', explanation: 'The candidate palette is more cohesive.',
    }
    const html = renderToStaticMarkup(<ImprovementComparison run={run} />)
    expect(html).toContain('Rendered aesthetics comparison')
    expect(html).toContain('separate from the original photo rating')
    expect(html).toContain('Previously retained')
    expect(html).toContain('Rendered total')
    expect(html).toContain('The candidate palette is more cohesive.')
    const row = html.match(/<tr><th scope="row">Aesthetics[\s\S]*?<\/tr>/)?.[0] ?? ''
    for (const text of ['Design review', '6 / 10', '8 / 10', '+2']) expect(row).toContain(text)
    run.attempts[0]!.review!.aesthetics!.confidence = 'low'
    expect(renderToStaticMarkup(<ImprovementComparison run={run} />)).toContain('no numerical improvement is claimed')
  })

  it('does not compare scores across assessment policy versions', () => {
    const run = result(); run.result_assessment!.policy_version = 'room-assessment-v2'
    const html = renderToStaticMarkup(<ImprovementComparison run={run} />)
    expect(html).toContain('rating policies differ')
    expect(html).not.toContain('+2')
  })

  it('leaves provisional scores and stopped-run limitations visible', () => {
    const run = result()
    run.status = 'budget_exhausted'; run.source_status = 'partial'
    run.result_assessment!.workspace.confidence = 'low'
    const html = renderToStaticMarkup(<ImprovementComparison run={run} />)
    expect(html).toContain('run stopped early')
    expect(html).toContain('same partial reconstruction')
    expect(html).toContain('Limited evidence')
  })

  it('returns no comparison for a run that retained no candidate and detects wraparound rotation correctly', () => {
    expect(renderToStaticMarkup(<ImprovementComparison run={{ ...result(), result_scene: null, status: 'no_improvement' }} />)).toBe('')
    const run = result()
    const a = run.source_scene.objects.find(obj => obj.id === 'desk_observed')!, b = run.result_scene!.objects.find(obj => obj.id === a.id)!
    a.rotation_y_rad = Math.PI * 359 / 180; b.rotation_y_rad = Math.PI / 180
    expect(sceneChanges(run.source_scene, run.result_scene!).find(change => change.id === a.id)!.details).toContain('Turned 2°')
  })

  it('shows only available layouts and offers editing without duplicate copy buttons', () => {
    const run = result(), comparison = new SceneComparison(run.source_scene)
    const html = () => renderToStaticMarkup(<ComparisonControls comparison={comparison} views={comparison.getSnapshot()} />)
    expect(html()).not.toMatch(/>Improved<\/button>/)
    expect(html()).not.toMatch(/>Your layout<\/button>/)
    comparison.offer(run)
    expect(html()).toContain('Edit layout')
    expect(html()).toMatch(/<button[^>]*aria-pressed="true"[^>]*>Improved/)
    expect(html()).not.toMatch(/>Reset layout<\/button>/)
    comparison.editCopy()
    expect(html()).toMatch(/>Your layout<\/button>/)
    expect(html()).not.toMatch(/<button[^>]*disabled=""[^>]*>Reset layout/)
    comparison.show('original')
    expect(html()).toContain('Edit layout')
    expect(html()).not.toContain('Edit original copy')
  })

  it('keeps version selection and early-stop labels for earlier retained candidates', () => {
    const first = result(), second = result()
    second.run_id = '99999999-9999-4999-8999-999999999999'; second.focus = 'storage'; second.status = 'failed'
    const comparison = new SceneComparison(first.source_scene)
    comparison.offer(first); comparison.offer(second)
    const html = renderToStaticMarkup(<ComparisonControls comparison={comparison} views={comparison.getSnapshot()} />)
    expect(html).toContain('role="combobox"')
    expect(html).not.toContain('<select')
    expect(html).toContain('2. Storage &amp; organization (partial)')
  })

  it('connects a completed optimization to the comparison and editable candidate without another model request', async () => {
    const source = finishedRun(prepared()), saved = result()
    saved.source_run_id = source.run_id; saved.input_id = source.input_id; saved.expires_at = source.expires_at
    const original = structuredClone(source.working_scene!)
    let prepares = 0, executes = 0
    const client: OptimizationClient = {
      prepare: async () => { prepares++; return { ...saved, status: 'prepared' as const, result_scene: null, result_assessment: null, attempts: [] } },
      execute: async () => { executes++; return saved }, read: async () => saved, cancel: async () => ({ ...saved, status: 'cancelled' as const }),
    }
    const controller = new OptimizationController(source, client)
    const comparison = new SceneComparison(original)
    controller.select('workspace'); await controller.start()
    expect(comparison.offer(controller.getSnapshot().result)).toBe(true)
    comparison.show('original')
    expect(comparison.getSnapshot().editor.getSnapshot().scene).toEqual(original)
    comparison.show('improved')
    expect(comparison.getSnapshot().editor.getSnapshot().scene).toEqual(saved.result_scene)
    comparison.editCopy()
    expect(comparison.getSnapshot().editor.getSnapshot().view).toBe('after')
    expect(source.working_scene).toEqual(original)
    expect(prepares).toBe(1); expect(executes).toBe(1)
  })
})

it('explains added, removed and replaced furniture without leaking asset IDs', () => {
  const run = result(), improved = run.result_scene!
  const removed = improved.objects.pop()!
  improved.objects[0].asset_id = 'sofa_standard_01'
  improved.objects.push({ ...structuredClone(removed), id: 'new_storage', type: 'wardrobe' })
  const changes = sceneChanges(run.source_scene, improved)
  expect(changes.some(item => item.details.includes('Added catalog furniture'))).toBe(true)
  expect(changes.some(item => item.details.includes('Replaced with a catalog model'))).toBe(true)
  expect(changes.some(item => item.id === removed.id && item.details.includes('Removed from proposed layout'))).toBe(true)
})
