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
  it('shows failed and cancelled results honestly and offers safe network recovery', () => {
    expect(render({ phase: 'failed' })).toContain('Generation stopped')
    expect(render({ phase: 'cancelled' })).toContain('Generation cancelled')
    const html = render({ phase: 'awaiting_answer', run: waitingRun(input), recovery: true, answering: true })
    expect(html).toContain('Retry the same answer')
    expect(html).toContain('fieldset disabled')
  })
})
