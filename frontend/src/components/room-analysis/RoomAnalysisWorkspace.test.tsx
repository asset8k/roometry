import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import RoomAnalysisWorkspace from './RoomAnalysisWorkspace'
import { RoomAnalysisController } from '../../lib/room-analysis-controller'
import { analyzedRoom, proposedRoom, roomInput, needsDimensions } from '../../lib/test-support/room-analysis'

function setup() {
  const controller = new RoomAnalysisController({ analyze: vi.fn(async () => analyzedRoom()), propose: vi.fn(async () => proposedRoom()) })
  controller.setInput(roomInput())
  const render = () => renderToStaticMarkup(<RoomAnalysisWorkspace controller={controller} state={controller.getSnapshot()} onConnectionChange={vi.fn()} />)
  return { controller, render }
}

describe('room analysis workspace', () => {
  it('does not add a second analysis action after upload', () => {
    const { render } = setup()
    const html = render()
    expect(html).toBe('')
    expect(html).not.toContain('3D room viewer')
  })
  it('presents objects, uncertain identities, evidence counts, and direct measurement entry', async () => {
    const { controller, render } = setup()
    await controller.analyze()
    const html = render()
    expect(html).toContain('Detected objects and openings')
    expect(html).toContain('Possible duplicate observations')
    expect(html).toContain('medium confidence')
    expect(html).toContain('What we couldn’t establish')
    expect(html).not.toContain('type="checkbox"')
    expect(html).toContain('Provided by you')
    expect(html).toContain('Your entries are used directly')
    expect(html).toContain('Build base scene')
    expect(html).not.toContain('Example bedroom')
  })
  it('renders pending and incomplete results honestly, without a viewer', () => {
    const { controller } = setup()
    const state = { ...controller.getSnapshot(), analysis: analyzedRoom(), proposal: needsDimensions() }
    const html = renderToStaticMarkup(<RoomAnalysisWorkspace state={state} controller={controller} onConnectionChange={vi.fn()} />)
    expect(html).toContain('A few measurements are still needed')
    expect(html).toContain('Enter: height')
    expect(html).toContain('don’t need to analyze again')
    expect(html).not.toContain('3D room viewer')
    const incomplete = { ...state, proposal: { ...state.proposal, status: 'incomplete' as const, issues: [{ code: 'geometry' as const, entity_ids: [], message: 'Object extends outside the room.' }] } }
    const rejected = renderToStaticMarkup(<RoomAnalysisWorkspace state={incomplete} controller={controller} onConnectionChange={vi.fn()} />)
    expect(rejected).toContain('role="alert"')
    expect(rejected).toContain('couldn’t validate a complete base scene')
    expect(rejected).not.toContain('3D room viewer')
  })
  it('opens the validated user scene with base/manual labels and its uncertainty notes', async () => {
    const { controller, render } = setup()
    await controller.analyze()
    controller.changeMeasurements({ height_m: '2.6' })
    await controller.build()
    const html = render()
    expect(html).toContain('Your reconstructed room')
    expect(html).toContain('Your bedroom')
    expect(html).toContain('Base scene')
    expect(html).toContain('Your layout')
    expect(html).toContain('Scene estimates, assumptions, and cautions')
    expect(html).toContain('approximate photo estimates')
    expect(html).not.toContain('Two authored example layouts')
    expect(html).not.toContain('Example bedroom')
  })
  it('names the affected item and keeps scene failures separate from photo quality', () => {
    const { controller } = setup()
    const analysis = analyzedRoom()
    const affected = analysis.observations.objects[0]
    affected.label = 'Curtains at glazed patio door'
    const state = { ...controller.getSnapshot(), analysis, proposal: {
      ...proposedRoom(), status: 'incomplete' as const, scene: null, validation: null,
      issues: [{ code: 'scene_schema' as const, entity_ids: [affected.id], message: 'This covering must reference an observed window or door using opening_id.' }],
    } }
    const html = renderToStaticMarkup(<RoomAnalysisWorkspace state={state} controller={controller} onConnectionChange={vi.fn()} />)
    expect(html).toContain('(Curtains at glazed patio door)')
    expect(html).toContain('Your photo analysis is still available until it expires.')
    expect(html).not.toContain('Clearer photos or corrected measurements')
    expect(html).not.toContain('3D room viewer')
  })
  it('escapes observation text instead of interpreting model-provided markup', async () => {
    const { controller } = setup()
    await controller.analyze()
    const state = structuredClone(controller.getSnapshot())
    state.analysis!.observations.objects[0].label = '<img src=x onerror=alert(1)>'
    const html = renderToStaticMarkup(<RoomAnalysisWorkspace state={state} controller={controller} onConnectionChange={vi.fn()} />)
    expect(html).toContain('&lt;img src=x')
    expect(html).not.toContain('<img src=x')
  })
})
