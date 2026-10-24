import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import { RoomInputForm } from './RoomInputWorkspace'
import { RoomInputController } from '../../lib/room-input-controller'
import { limits, photo, prepared } from '../../lib/test-support/room-input'

async function setup() {
  const controller = new RoomInputController({ limits: async () => limits, upload: async (files, options) => ({ ...prepared(files), options }), discard: async () => {} },
    { create: () => 'blob:preview', revoke: vi.fn() })
  await controller.loadLimits()
  const render = () => renderToStaticMarkup(<RoomInputForm state={controller.getSnapshot()} controller={controller} onGenerate={vi.fn(async () => {})} onCancel={vi.fn()} />)
  return { controller, render }
}

describe('room input form', () => {
  it('offers photos and current-room details without improvement preferences', async () => {
    const { render } = await setup()
    const html = render()
    expect(html).toContain('aria-label="Choose room photos"')
    expect(html).toContain('multiple=""')
    expect(html).toContain('.heic')
    for (const label of ['Kitchen', 'Bathroom', 'Other', 'Width (m)', 'Length (m)', 'Height (m)', 'Describe the room', '2. About your room']) expect(html).toContain(label)
    for (const label of ['Dining room', 'Laundry room', 'Budget', 'Currency', 'Style preference', 'More storage', 'A better workspace', 'More open space', 'Better aesthetics', 'Anything you want to improve?', 'Room improvement goals', 'Room details &amp; preferences']) expect(html).not.toContain(label)
    expect(html).toContain('reconstruct your current space')
    expect(html).toContain('Measurements are optional')
    expect(html).toContain('Generate room')
    expect(html).not.toContain('Upload current inputs')
    expect(html).not.toContain('>Upload photos</button>')
    expect(html).not.toContain('I have checked the dimensions I entered.')
  })
  it('keeps an unsupported local preview eligible for server upload', async () => {
    const { controller, render } = await setup()
    controller.addPhotos([photo('phone.heic', 'image/heic')])
    controller.previewFailed(controller.getSnapshot().photos[0].id)
    const html = render()
    expect(html).toContain('Preview unavailable in this browser. You can still upload it.')
    expect(html).not.toContain('src="blob:preview"')
    expect(html).toContain('Remove phone.heic')
  })
  it('shows acceptance and subsequent dirty state without inventing a reconstruction', async () => {
    const { controller, render } = await setup()
    controller.addPhotos([photo()])
    await controller.submit()
    expect(render()).toContain('Photos received')
    expect(render()).toContain('View submitted details')
    expect(render()).toContain('Generation progress and your room model appear below.')
    for (const label of ['<dt>Goals</dt>', '<dt>Budget</dt>', '<dt>Style</dt>', '<dt>Keep or avoid</dt>']) expect(render()).not.toContain(label)
    controller.change({ description: 'My bedroom with a desk by the window' })
    expect(render()).toContain('Changes not generated')
    expect(render()).toContain('Clear inputs')
  })
  it('summarizes submitted descriptions and measurements', async () => {
    const { controller, render } = await setup()
    controller.addPhotos([photo()])
    controller.change({ roomType: 'bedroom', description: 'Desk by the window', width: '3', length: '6', height: '3' })
    await controller.submit()
    expect(render()).toContain('Desk by the window')
    expect(render()).toContain('3 × 6 × 3 m (provided by you)')
    expect(controller.getSnapshot().accepted?.options.goals).toEqual(['general_analysis'])
  })
  it('uses an indeterminate progress state and holds form edits during submission', async () => {
    const { controller } = await setup()
    const state = { ...controller.getSnapshot(), busy: 'upload' as const }
    const html = renderToStaticMarkup(<RoomInputForm state={state} controller={controller} onGenerate={vi.fn(async () => {})} onCancel={vi.fn()} />)
    expect(html).toContain('<progress aria-label="Preparing room photos"')
    expect(html).toContain('<fieldset class="room-preferences" disabled=""')
    expect(html).toContain('>Cancel</button>')
    expect(html).not.toContain('value="100"')
  })
})
