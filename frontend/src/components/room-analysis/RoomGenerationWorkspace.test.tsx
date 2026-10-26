import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import RoomGenerationWorkspace from './RoomGenerationWorkspace'
import { RoomInputController } from '../../lib/room-input-controller'
import { RoomGenerationController } from '../../lib/room-generation-controller'
import type { GenerationState } from '../../lib/room-generation-controller'
import { agentRun, finishedRun, waitingRun } from '../../lib/test-support/agent-run'
import { prepared, photo } from '../../lib/test-support/room-input'

const controller = new RoomGenerationController(new RoomInputController())
const input = prepared([photo()])
function render(patch: Partial<GenerationState>) {
  const state: GenerationState = { phase: 'idle', run: null, cancelling: false, answering: false, recovery: false, notice: null, ...patch }
  return renderToStaticMarkup(<RoomGenerationWorkspace state={state} controller={controller} onConnectionChange={vi.fn()} />)
}

describe('guided generation workspace', () => {
  it('shows real pending work without percentages or a fabricated scene', () => {
    const html = render({ phase: 'running', run: agentRun(input) })
    expect(html).toContain('Analyzing your room photos together')
    expect(html).toContain('aria-label="Room generation progress"')
    expect(html).not.toContain('value="100"')
    expect(html).not.toContain('3D room viewer')
  })
  it('shows exactly three answer choices and holds the viewer until work finishes', () => {
    const html = render({ phase: 'awaiting_answer', run: waitingRun(input) })
    expect(html.match(/type="radio"/g)).toHaveLength(3)
    expect(html).toContain('What are the room dimensions?')
    expect(html).not.toContain('type="checkbox"')
    expect(html).not.toContain('Build base scene')
    expect(html).not.toContain('3D room viewer')
  })
  it('opens partial scenes with named unresolved observations and editable controls', () => {
    const html = render({ phase: 'partial', run: finishedRun(input, true) })
    expect(html).toContain('Your partial reconstruction')
    expect(html).toContain('Partial reconstruction · 1 observation unresolved')
    expect(html).toContain('Bed')
    expect(html).toContain('unresolved and absent from the 3D scene')
    expect(html).toContain('3D room viewer')
    expect(html).toContain('Edit layout')
    expect(html).not.toContain('Example bedroom')
  })
  it('keeps recovered analysis warnings visible alongside the usable scene', () => {
    const run = finishedRun(input, true)
    run.analysis!.observations.warnings = ['Invalid wall-span evidence was ignored; other observations were retained.']
    const html = render({ phase: 'partial', run })
    expect(html).toContain('Invalid wall-span evidence was ignored')
    expect(html).toContain('3D room viewer')
  })
  it('asks about the measured wall with three choices and no redundant measurement inputs', () => {
    const run = waitingRun(input)
    run.pending_question = { id: 'q1', kind: 'room_wall_axis', question: 'Which wall measures 4 m?', dimension_axes: ['width_m'], entity_ids: ['window_wall', 'tv_wall'],
      choices: [{ id: 'wall_first', label: 'Window wall' }, { id: 'wall_second', label: 'TV wall' }, { id: 'not_sure', label: 'Not sure — use an approximate orientation' }] }
    run.questions = [run.pending_question]
    const html = render({ phase: 'awaiting_answer', run })
    expect(html).toContain('Which wall measures 4 m?')
    expect(html).toContain('Window wall')
    expect(html).toContain('TV wall')
    expect(html.match(/type="radio"/g)).toHaveLength(3)
    expect(html).not.toContain('type="number"')
    expect(html).not.toContain('3D room viewer')
  })
  it('labels an unanswered wall assignment as approximate in the preview', () => {
    const run = finishedRun(input, true)
    run.proposal!.wall_axes = { status: 'estimated', reference_axis: 'width_m', reference_span_m: 4, candidate_wall_ids: [], bindings: [], warnings: [] }
    const html = render({ phase: 'partial', run })
    expect(html).toContain('<summary>Wall dimensions</summary>')
    expect(html).toContain('wall-to-measurement assignment is unconfirmed')
    expect(html).toContain('3D room viewer')
  })
  it('formats identifiers in checkpointed question text without altering choice values', () => {
    const run = waitingRun(input)
    run.pending_question = { id: 'q1', kind: 'room_wall_axis', question: 'Which wall measures 3 m?', dimension_axes: ['width_m'], entity_ids: ['window_wall', 'desk_side_marble_wall'],
      choices: [{ id: 'wall_first', label: 'window_wall' }, { id: 'wall_second', label: 'desk_side_marble_wall' }, { id: 'not_sure', label: 'Not sure — use an approximate orientation' }] }
    run.questions = [run.pending_question]
    const html = render({ phase: 'awaiting_answer', run })
    expect(html).toContain('Window wall')
    expect(html).toContain('Desk side marble wall')
    expect(html).not.toContain('window_wall')
    expect(html).not.toContain('desk_side_marble_wall')
    expect(html).toContain('value="wall_first"')
    expect(html).toContain('value="wall_second"')
    run.pending_question.question = 'Which length belongs to window_wall?'
    expect(render({ phase: 'awaiting_answer', run })).toContain('Which length belongs to window wall?')
  })
  it('explains a failed analysis without exposing raw provider diagnostics', () => {
    const run = agentRun(input, { status: 'failed', last_error: 'tool_failed',
      tool_feedback: [{ name: 'analyze_photos', outcome: 'error', message: 'PRIVATE_PROVIDER_DETAIL', details: { code: 'invalid_analysis' } }] })
    const html = render({ phase: 'failed', run })
    expect(html).toContain('Photo analysis returned inconsistent observations')
    expect(html).toContain('Please try generating again')
    expect(html).toContain('selected photos and entered details are kept')
    expect(html).not.toContain('PRIVATE_PROVIDER_DETAIL')
    expect(html).not.toContain('3D room viewer')
  })
  it('explains missing backend configuration without suggesting repeated model retries', () => {
    const run = agentRun(input, { status: 'failed', last_error: 'decision_failed',
      tool_feedback: [{ name: null, outcome: 'error', message: 'PRIVATE_CONFIG_DETAIL', details: { code: 'analysis_unavailable' } }] })
    const html = render({ phase: 'failed', run })
    expect(html).toContain('Room generation is not configured')
    expect(html).toContain('analysis enabled and an API key configured')
    expect(html).not.toContain('Please try generating again')
    expect(html).not.toContain('PRIVATE_CONFIG_DETAIL')
  })
  it('discloses fallback dimensions and relabels old checkpointed unknown choices', () => {
    const run = waitingRun(input)
    run.pending_question!.kind = 'room_dimensions'
    run.pending_question!.choices[1] = { id: 'leave_unknown', label: "I don't know — finish without a complete scene" }
    const html = render({ phase: 'awaiting_answer', run })
    expect(html).toContain('Use estimated dimensions')
    expect(html).toContain('Unclear sizes start at 4 × 4 × 2.6 m')
    expect(html).toContain('Enter what you know, or use photo estimates')
    expect(html).not.toContain('finish without a complete scene')
    expect(html.match(/type="radio"/g)).toHaveLength(3)
  })
  it('shows estimated and assumed sizes next to the viewer rather than only inside details', () => {
    const run = finishedRun(input)
    run.proposal!.estimated_axes = ['length_m', 'height_m']
    run.proposal!.assumed_axes = ['height_m']
    const html = render({ phase: 'completed', run })
    expect(html).toContain('Approximate dimensions')
    expect(html).toContain('Includes assumed sizes')
    expect(html).toContain('photo scale was unclear')
  })
  it('shows a usable partial scene and an honest limit notice after free finalization', () => {
    const run = { ...finishedRun(input, true), stop_reason: 'budget_limit' as const }
    const html = render({ phase: 'partial', run })
    expect(html).toContain('Your partial reconstruction')
    expect(html).toContain('Additional checks stopped at the run limit')
    expect(html).toContain('3D room viewer')
    expect(html).not.toContain('This run did not finish successfully')
  })
  it('shows fidelity limitations separately from collision validity', () => {
    const run = finishedRun(input, true)
    run.proposal!.fidelity = {
      policy_version: 'reconstruction_v1', major_object_coverage: .5,
      relationship_score: .5, proportion_score: null, color_score: 1, recognizable: false,
      checks: [{ kind: 'coverage', entity_ids: ['bed_observed'], passed: false, required: true, message: 'Retain the observed major furniture.' }],
      unassessed: ['Sizes remain approximate.'],
    }
    const html = render({ phase: 'partial', run })
    expect(html).toContain('The major arrangement is not yet established')
    expect(html).toContain('Major furniture and openings retained: 50%')
    expect(html).toContain('Sizes remain approximate')
    expect(html).toContain('3D room viewer')
  })
  it('shows a bounded visual correction without claiming a second photo review', () => {
    const run = finishedRun(input, true)
    run.proposal!.visual_review = { status: 'reviewed', repair: 'accepted', message: 'One correction passed deterministic checks.',
      reviewed_scene_hash: 'a'.repeat(64), result_scene_hash: 'b'.repeat(64), revised_scene_visually_reviewed: false, applied_findings: [0],
      judgment: { verdict: 'needs_correction', summary: 'The facing direction differed.', limitations: ['Exact sizes remain uncertain.'], findings: [{
        kind: 'orientation', entity_ids: ['bed_observed'], confidence: 'high', severity: 'major',
        source_image_ids: [run.analysis!.observations.image_ids[0]], view_ids: ['top'],
        description: 'The functional front points away from its observed direction.', correction: 'Turn toward its observed target.',
      }] } }
    const html = render({ phase: 'partial', run })
    expect(html).toContain('<summary>Photo comparison</summary>')
    expect(html).toContain('has not received a second visual comparison')
    expect(html).toContain('The functional front points away')
    expect(html).toContain('Later edits are not visually rechecked')
    expect(html).toContain('Adjusted toward the review target; photo likeness still needs checking.')
    expect(html).toContain('3D room viewer')
  })
  it('keeps the viewer available when visual review is unavailable', () => {
    const run = finishedRun(input)
    run.proposal!.visual_review = { status: 'unavailable', repair: 'not_attempted', judgment: null,
      message: 'The visual check could not finish. The previously validated scene is retained.',
      reviewed_scene_hash: 'a'.repeat(64), result_scene_hash: 'a'.repeat(64), revised_scene_visually_reviewed: false }
    const html = render({ phase: 'completed', run })
    expect(html).toContain('The visual check could not finish')
    expect(html).toContain('3D room viewer')
  })
  it('discloses evidence-backed interpretation corrections while keeping the viewer available', () => {
    const run = finishedRun(input)
    run.working_scene!.interpretation_corrections = [{ entity_id: 'bed_observed', field: 'placement', previous_value: 'wall', revised_value: 'floor',
      source_image_ids: [run.analysis!.observations.image_ids[0]], reason: 'Visible feet meet the floor beneath this item.' }]
    const html = render({ phase: 'completed', run })
    expect(html).toContain('<summary>Revised photo interpretations</summary>')
    expect(html).toContain('Visible feet meet the floor beneath this item.')
    expect(html).toContain('3D room viewer')
  })
  it('opens a provisional scene even when every object is retained but fidelity needs correction', () => {
    const run = finishedRun(input)
    run.status = 'partial'
    run.proposal!.status = 'partial'
    run.proposal!.issues = [{ code: 'fidelity', entity_ids: ['bed_observed'], message: 'Facing direction needs correction.' }]
    run.proposal!.unresolved_entities = []
    run.proposal!.fidelity = {
      policy_version: 'reconstruction_v1', major_object_coverage: 1, relationship_score: 0,
      proportion_score: null, color_score: 1, recognizable: false,
      checks: [{ kind: 'facing', entity_ids: ['bed_observed'], passed: false, required: true, message: 'Facing direction needs correction.' }], unassessed: [],
    }
    const html = render({ phase: 'partial', run })
    expect(html).toContain('Your reconstruction preview')
    expect(html).toContain('provisional reconstruction')
    expect(html).toContain('Facing direction needs correction')
    expect(html).toContain('3D room viewer')
    expect(html).not.toContain('0 observations are unresolved')
    expect(html).not.toContain('More information is needed')
  })
  it('describes construction failure accurately and keeps technical details collapsed', () => {
    const run = finishedRun(input)
    run.status = 'incomplete'
    run.working_scene = run.base_scene = null
    run.proposal!.status = 'incomplete'
    run.proposal!.scene = null
    run.proposal!.issues = [{ code: 'geometry', entity_ids: ['bed_observed'], message: 'object_outside_room: bounds exceed room.' }]
    const html = render({ phase: 'incomplete', run })
    expect(html).toContain('Reconstruction could not finish')
    expect(html).toContain('couldn’t build a usable arrangement')
    expect(html).toContain('<summary>Construction details</summary>')
    expect(html).not.toContain('More information is needed')
    expect(html).not.toContain('There isn’t enough information')
  })
  it('shows failed and cancelled results honestly and offers safe network recovery', () => {
    expect(render({ phase: 'failed' })).toContain('Generation stopped')
    expect(render({ phase: 'cancelled' })).toContain('Generation cancelled')
    const html = render({ phase: 'awaiting_answer', run: waitingRun(input), recovery: true, answering: true })
    expect(html).toContain('Retry the same answer')
    expect(html).toContain('fieldset disabled')
  })
})

it('keeps the reconstructed viewer open and discloses unavailable catalog checks', () => {
  const run = finishedRun(input)
  run.catalog_reads = [{ name: 'search_furniture', arguments: {}, outcome: 'error' }]
  const html = render({ phase: 'completed', run })
  expect(html).toContain('Furniture catalog checks were unavailable')
  expect(html).toContain('3D room viewer')
  expect(html).toContain('Your reconstructed room')
})
