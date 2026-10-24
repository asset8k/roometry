import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import RoomAssessmentCards from './RoomAssessmentCards'
import { parseAgentRun } from '../../lib/agent-run'
import type { AgentState } from '../../lib/agent-run'
import { finishedRun } from '../../lib/test-support/agent-run'
import { prepared } from '../../lib/test-support/room-input'

function assessment(): NonNullable<AgentState['room_assessment']> {
  const card: NonNullable<AgentState['room_assessment']>['openness'] = { status: 'rated', score: 7., confidence: 'medium',
    source: 'geometry' as const, explanation: 'A clear front approach is available.', entity_ids: [], metrics: [] }
  return { policy_version: 'room-assessment-v1', scene_hash: 'a'.repeat(64),
    openness: { ...card, score: 5.2, explanation: 'The main usable space is divided by furniture.' },
    storage: card, workspace: { ...card, status: 'not_applicable', score: null, confidence: 'low', explanation: 'No dedicated workspace was identified.' },
    aesthetics: { ...card, score: 8., source: 'photos', explanation: 'The materials and colors work well together.' },
    recommended: 'openness', notes: ['Ratings describe the original reconstruction.'] }
}

describe('room assessment cards', () => {
  it('shows all four categories, estimates, explanations and one recommended improvement', () => {
    const html = renderToStaticMarkup(<RoomAssessmentCards assessment={assessment()} />)
    for (const label of ['Space &amp; movement', 'Storage &amp; organization', 'Workspace', 'Aesthetics &amp; cohesion',
      '5.2 out of 10', 'Recommended', 'The main usable space is divided by furniture.', 'Photo-based estimate', 'Manual edits are not included']) expect(html).toContain(label)
    expect(html.match(/class="assessment-recommendation"/g)).toHaveLength(1)
    expect(html.match(/disabled=""/g)).toHaveLength(4)
    expect(html).toContain('Improvement actions are unavailable for this run')
  })

  it('does not rate an absent workspace zero and still offers its future action', () => {
    const html = renderToStaticMarkup(<RoomAssessmentCards assessment={assessment()} />)
    expect(html).toContain('No dedicated workspace')
    expect(html).toContain('Create a workspace')
    expect(html).not.toContain('Workspace: 0 out of 10')
  })

  it('keeps the cards useful when one category lacks sufficient evidence', () => {
    const data = assessment()
    data.aesthetics = { ...data.aesthetics, status: 'unassessed', score: null, confidence: 'low', explanation: 'The visual rating could not finish.' }
    const html = renderToStaticMarkup(<RoomAssessmentCards assessment={data} />)
    expect(html).toContain('Not assessed')
    expect(html).toContain('5.2 out of 10')
    expect(html).toContain('The visual rating could not finish.')
  })

  it.each(['invalid_score', 'contradiction', 'wrong_recommendation', 'missing_cards'])('isolates %s assessment metadata while retaining a readable scene', defect => {
    const run = finishedRun(prepared())
    const data = assessment()
    if (defect === 'invalid_score') data.openness.score = 99
    if (defect === 'contradiction') data.openness.status = 'unassessed'
    if (defect === 'wrong_recommendation') data.recommended = 'workspace'
    const raw = { ...run, room_assessment: defect === 'missing_cards' ? { scene_hash: data.scene_hash } : data }
    const parsed = parseAgentRun(raw)
    expect(parsed.room_assessment).toBeNull()
    expect(parsed.working_scene).toEqual(run.working_scene)
    expect(parsed.status).toBe('completed')
  })

  it('reads valid cards and historical responses without assessments', () => {
    const run = finishedRun(prepared())
    expect(parseAgentRun({ ...run, room_assessment: assessment() }).room_assessment).toEqual(assessment())
    const { room_assessment: _, ...historical } = run
    expect(parseAgentRun(historical).working_scene).toEqual(run.working_scene)
    expect(renderToStaticMarkup(<RoomAssessmentCards assessment={null} />)).toContain('Your reconstructed room is still available below')
  })

  it('shows the five-part photo rubric and keeps recommendations category-specific', () => {
    const data = assessment()
    data.policy_version = 'room-assessment-v2'
    data.aesthetics = { ...data.aesthetics, score: 7, metrics: [
      { key: 'color_harmony', value: 2, unit: 'count' }, { key: 'material_cohesion', value: 2, unit: 'count' },
      { key: 'visual_balance', value: 1, unit: 'count' }, { key: 'visual_order', value: 1, unit: 'count' },
      { key: 'lighting_composition', value: 1, unit: 'count' },
    ] }
    data.storage = { ...data.storage, score: 2, confidence: 'low' }
    const html = renderToStaticMarkup(<RoomAssessmentCards assessment={data} onImprove={() => {}} />)
    for (const label of ['How aesthetics was rated', 'Color harmony: 2 / 2', 'Material cohesion: 2 / 2',
      'Visual balance: 1 / 2', 'Visual order: 1 / 2', 'Lighting composition: 1 / 2']) expect(html).toContain(label)
    expect(html.match(/class="assessment-recommendation"/g)).toHaveLength(1)
    expect(html).toContain('Limited evidence')
    expect(html.match(/disabled=""/g)).toBeNull()
  })
})
