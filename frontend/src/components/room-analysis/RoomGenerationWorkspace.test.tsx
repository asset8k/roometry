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
    expect(html).toContain('What is the room height')
    expect(html).not.toContain('type="checkbox"')
    expect(html).not.toContain('Build base scene')
    expect(html).not.toContain('3D room viewer')
  })
  it('opens partial scenes with named unresolved observations and editable controls', () => {
    const html = render({ phase: 'partial', run: finishedRun(input, true) })
    expect(html).toContain('Your partial reconstruction')
    expect(html).toContain('Bed')
    expect(html).toContain('unresolved and absent from the 3D scene')
    expect(html).toContain('3D room viewer')
    expect(html).toContain('Reset layout')
    expect(html).not.toContain('Example bedroom')
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
    expect(html).toContain('Major furniture retained: 50%')
    expect(html).toContain('Sizes remain approximate')
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
