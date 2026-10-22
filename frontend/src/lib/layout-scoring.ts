import Ajv2020 from 'ajv/dist/2020.js'
import responseSchema from './layout-score-response.schema.json'
import type { LayoutScoreReport } from './layout-score-response.generated'

export type { LayoutScoreReport } from './layout-score-response.generated'
export type { ScoreLayoutRequest } from './layout-score-request.generated'

const ajv = new Ajv2020({ strict: true, allErrors: true })
const valid = ajv.compile<LayoutScoreReport>(responseSchema)
const criteria = ['openness', 'storage', 'workspace', 'aesthetics'] as const
const goalCriteria = {
  general_analysis: criteria, more_open_space: ['openness'], more_storage: ['storage'],
  workspace: ['workspace'], better_aesthetics: ['aesthetics'],
} as const

/** These scores describe soft goals; they cannot authorize a candidate edit. */
export function parseLayoutScore(data: unknown): LayoutScoreReport {
  if (!valid(data)) throw new Error('Unreadable layout scores')
  const expected = [...new Set(data.requested_goals.flatMap(goal => [...goalCriteria[goal]]))]
  const values = data.selected_criteria.map(key => data[key].score)
  const average = values.every(value => value !== null) && values.length
    ? values.reduce<number>((sum, value) => sum + (value ?? 0), 0) / values.length : null
  if (!data.requested_goals.length || new Set(data.requested_goals).size !== data.requested_goals.length
    || JSON.stringify(expected) !== JSON.stringify(data.selected_criteria)
    || data.geometry.valid !== (data.geometry.errors.length === 0)
    || criteria.some(key => (data[key].status === 'scored') !== (data[key].score !== null))
    || (!data.geometry.valid && (criteria.some(key => data[key].score !== null) || data.workstations.length > 0))
    || (average === null) !== (data.overall_score === null)
    || (average !== null && data.overall_score !== null && Math.abs(average - data.overall_score) > 1e-8)) {
    throw new Error('Inconsistent layout scores')
  }
  return structuredClone(data)
}
