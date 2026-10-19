import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import { RoomInputForm } from './RoomInputWorkspace'
import { RoomInputController } from '../../lib/room-input-controller'
import { limits, photo, prepared } from '../../lib/test-support/room-input'

async function setup() {
  const controller = new RoomInputController({ limits: async () => limits, upload: async files => prepared(files), discard: async () => {} },
    { create: () => 'blob:preview', revoke: vi.fn() })
  await controller.loadLimits()
  const render = () => renderToStaticMarkup(<RoomInputForm state={controller.getSnapshot()} controller={controller} />)
  return { controller, render }
}

describe('room input form', () => {
  it('offers common room categories, aesthetics, and one improvement note with an accessible file picker', async () => {
    const { render } = await setup()
    const html = render()
    expect(html).toContain('aria-label="Choose room photos"')
    expect(html).toContain('multiple=""')
    expect(html).toContain('.heic')
    for (const label of ['Kitchen', 'Bathroom', 'Other', 'Budget', 'Currency', 'Width (m)', 'Height (m)', 'Better aesthetics', 'Anything you want to improve?', 'Tell us what to keep or avoid.']) expect(html).toContain(label)
    for (const label of ['Dining room', 'Laundry room', 'Anything to keep or avoid?', 'Better furniture placement']) expect(html).not.toContain(label)
    expect(html).toContain('Room details &amp; preferences')
    expect(html).toContain('Upload photos')
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
    expect(render()).toContain('not a reconstructed 3D room')
    controller.change({ goalText: 'Keep the desk' })
    expect(render()).toContain('Changes not uploaded')
    expect(render()).toContain('Discard upload &amp; clear')
  })
  it('uses an indeterminate progress state and holds form edits during submission', async () => {
    const { controller } = await setup()
    const state = { ...controller.getSnapshot(), busy: 'upload' as const }
    const html = renderToStaticMarkup(<RoomInputForm state={state} controller={controller} />)
    expect(html).toContain('<progress aria-label="Uploading photos"')
    expect(html).toContain('<fieldset class="room-preferences" disabled=""')
    expect(html).toContain('>Cancel</button>')
    expect(html).not.toContain('value="100"')
  })
})
