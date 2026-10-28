import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import RoomAssessmentCards from '../components/room-analysis/RoomAssessmentCards'
import { assessmentForLayout, improvementRatings, ratingChange } from './assessment-display'
import type { Assessment } from './assessment-display'
import { SceneComparison } from './scene-comparison'
import { finishedRun } from './test-support/agent-run'
import { prepared } from './test-support/room-input'
import { preparedImprovement } from './test-support/optimization'
import type { OptimizationRun } from './optimization'

function assessment(storage = 5.7): Assessment {
  const card: Assessment['storage'] = { status: 'rated', score: storage, confidence: 'medium', source: 'geometry', explanation: 'Checked usable storage.', entity_ids: [], metrics: [] }
  return { policy_version: 'room-assessment-v2', scene_hash: 'a'.repeat(64), openness: { ...card, score: 8.8 }, storage: card,
    workspace: { ...card, score: 7 }, aesthetics: { ...card, score: 9, source: 'photos' }, recommended: 'storage', notes: [] }
}
function setup() {
  const source = finishedRun(prepared())
  source.room_assessment = assessment()
  const run = preparedImprovement(source, { request_id: crypto.randomUUID(), focus: 'storage' })
  run.result_scene = structuredClone(run.source_scene)
  run.result_scene.objects[0].color = '#112233'
  run.status = 'completed'; run.stage = 'finished'
  run.result_assessment = assessment(7.1)
  run.result_assessment.openness.score = 7.8
  run.result_assessment.aesthetics = { ...run.result_assessment.aesthetics, status: 'unassessed', score: null }
  run.attempts = [{ number: 1, outcome: 'accepted', before_score: 5.7, after_score: 7.1, changed_object_ids: [], issues: [], summary: 'More accessible storage.', proposal: null, review: null }]
  return { source, run, comparison: new SceneComparison(source.working_scene!) }
}
const render = (source: ReturnType<typeof setup>['source'], comparison: SceneComparison) => renderToStaticMarkup(
  <RoomAssessmentCards {...assessmentForLayout(source.room_assessment ?? null, comparison.getSnapshot())} />)

describe('ratings for the visible saved layout', () => {
  it('replaces the original score after acceptance and shows real gains and trade-offs', () => {
    const { source, run, comparison } = setup()
    const original = structuredClone(source.room_assessment)
    expect(render(source, comparison)).toContain('Storage &amp; organization: 5.7 out of 10')
    comparison.offer(run, run.source_scene)
    const html = render(source, comparison)
    expect(html).toContain('Storage &amp; organization: 7.1 out of 10')
    expect(html).toContain('5.7 before, 7.1 now, +1.4 points')
    expect(html).toContain('8.8 before, 7.8 now, -1 points')
    expect(html).not.toContain('Unchanged')
    expect(html).not.toContain('Workspace: 7 before')
    expect(html).not.toContain('5.7 → 7.1')
    expect(html).toContain('>+1.4</strong>')
    expect(html).toContain('Estimates of this improved layout')
    expect(html).toContain('Aesthetics has not been re-rated')
    expect(source.room_assessment).toEqual(original)
    comparison.show('original')
    expect(render(source, comparison)).toContain('Storage &amp; organization: 5.7 out of 10')
    expect(render(source, comparison)).not.toContain('assessment-change--up')
  })

  it('follows version selection and compares a second improvement against its own starting layout', () => {
    const { source, run, comparison } = setup()
    comparison.offer(run, run.source_scene)
    const second: OptimizationRun = { ...structuredClone(run), run_id: crypto.randomUUID(), parent_run_id: run.run_id,
      source_scene: structuredClone(run.result_scene!), baseline_assessment: structuredClone(run.result_assessment), result_assessment: assessment(8) }
    second.result_scene!.objects[0].color = '#223344'
    comparison.offer(second, second.source_scene)
    expect(render(source, comparison)).toContain('7.1 before, 8 now, +0.9 points')
    comparison.selectImprovement(run.run_id)
    expect(render(source, comparison)).toContain('5.7 before, 7.1 now, +1.4 points')
    comparison.selectImprovement(second.run_id)
    expect(render(source, comparison)).toContain('Storage &amp; organization: 8 out of 10')
    comparison.editCopy()
    expect(render(source, comparison)).toContain('manual edits are not re-rated')
    expect(render(source, comparison)).not.toContain('assessment-change--up')
  })

  it('retains scores when a later request is rejected and does not fabricate missing ratings', () => {
    const { source, run, comparison } = setup()
    comparison.offer(run, run.source_scene)
    expect(comparison.offer({ ...run, run_id: crypto.randomUUID(), status: 'no_improvement', result_scene: null })).toBe(false)
    expect(render(source, comparison)).toContain('Storage &amp; organization: 7.1 out of 10')
    const historical = { ...run, run_id: crypto.randomUUID(), result_assessment: null }
    comparison.show('original'); comparison.offer(historical, historical.source_scene)
    expect(render(source, comparison)).toContain('Room ratings are unavailable for this layout')
    expect(ratingChange(null, 7)).toBeNull()
    expect(ratingChange(0, 1)).toEqual({ before: 0, after: 1, delta: 1 })
  })

  it('compares rendered aesthetic scores with their paired baseline, never the original photo score', () => {
    const { run } = setup()
    run.focus = 'aesthetics'
    const retained = { color_harmony: 1, material_cohesion: 1, visual_balance: 1, visual_order: 1, lighting_composition: 2 }
    const candidate = { ...retained, color_harmony: 2, material_cohesion: 2 }
    run.attempts[0]!.review = { preferred: 'candidate', confidence: 'medium', constraints_satisfied: true, explanation: 'Coherent palette.',
      aesthetics: { retained, candidate, confidence: 'medium', explanation: 'Coherent palette.' } }
    const ratings = improvementRatings(run)
    expect(ratings.designReview).toBe(true)
    expect(ratings.baseline?.aesthetics.score).toBe(6)
    expect(ratings.assessment?.aesthetics.score).toBe(8)
    expect(run.baseline_assessment?.aesthetics.score).toBe(9)
    const html = renderToStaticMarkup(<RoomAssessmentCards {...ratings} layout="improved" />)
    expect(html).toContain('6 before, 8 now, +2 points')
    expect(html).not.toContain('Design review · compared with the previous rendered layout')
    expect(html).toContain('Design review estimate.')
    run.attempts[0]!.review!.aesthetics!.confidence = 'low'
    expect(improvementRatings(run).designReview).toBe(false)
    expect(improvementRatings(run).assessment?.aesthetics.score).toBeNull()
  })

  it('does not calculate numerical gains across different assessment policies', () => {
    const { run } = setup()
    run.baseline_assessment!.policy_version = 'room-assessment-v1'
    expect(improvementRatings(run).baseline).toBeNull()
  })
})
