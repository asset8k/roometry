import type { AgentState } from './agent-run'
import type { ComparisonState } from './scene-comparison'
import { retainedImprovement } from './scene-comparison'
import type { OptimizationRun } from './optimization'

export type Assessment = NonNullable<AgentState['room_assessment']>
export type RatingLayout = 'original' | 'improved' | 'editing'

/** Display only ratings belonging to the selected saved version, never the latest request. */
export function improvementRatings(run: OptimizationRun) {
  if (!retainedImprovement(run)) return { assessment: null, baseline: null, designReview: false }
  const assessment = run.result_assessment ? structuredClone(run.result_assessment) : null
  const baseline = run.baseline_assessment && run.baseline_assessment.policy_version === run.result_assessment?.policy_version
    ? structuredClone(run.baseline_assessment) : null
  const accepted = run.attempts.filter(attempt => attempt.outcome === 'accepted')
  const pair = accepted.at(-1)?.review?.aesthetics
  // Rendered design reviews and the original photo rating are different evidence.
  // Compare the paired render scores with each other, never with the photo score.
  const designReview = !!assessment && !!pair && pair.confidence !== 'low'
  if (assessment && pair && designReview) {
    const card = (scores: typeof pair.candidate): Assessment['aesthetics'] => ({
      status: 'rated', score: Object.values(scores).reduce((sum, value) => sum + value, 0),
      confidence: pair.confidence, source: 'photos', explanation: pair.explanation,
      entity_ids: [], metrics: [
        { key: 'color_harmony', value: scores.color_harmony, unit: 'count' },
        { key: 'material_cohesion', value: scores.material_cohesion, unit: 'count' },
        { key: 'visual_balance', value: scores.visual_balance, unit: 'count' },
        { key: 'visual_order', value: scores.visual_order, unit: 'count' },
        { key: 'lighting_composition', value: scores.lighting_composition, unit: 'count' },
      ],
    })
    assessment.aesthetics = card(pair.candidate)
    const firstPair = accepted[0]?.review?.aesthetics
    if (baseline) baseline.aesthetics = card(firstPair && firstPair.confidence !== 'low' ? firstPair.retained : pair.retained)
  }
  return { assessment, baseline, designReview }
}

export function assessmentForLayout(original: Assessment | null, views: ComparisonState) {
  const layout: RatingLayout = views.view
  const id = layout === 'editing' ? views.editingId : views.selectedImprovementId
  const selected = layout === 'original' || (layout === 'editing' && id === 'original')
    ? null : views.improvements.find(run => run.run_id === id)
  const ratings = selected ? improvementRatings(selected) : { assessment: original, baseline: null, designReview: false }
  return { ...ratings, layout, versionKey: layout === 'original' ? 'original' : `${layout}:${id}` }
}

export function ratingChange(before: number | null | undefined, after: number | null | undefined) {
  if (typeof before !== 'number' || typeof after !== 'number') return null
  const delta = Math.round((after - before) * 10) / 10
  return { before, after, delta }
}

export const formatRating = (score: number) => score.toFixed(1).replace(/\.0$/, '')
