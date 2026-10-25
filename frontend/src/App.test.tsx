import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import App from './App'

describe('application entry', () => {
  it('opens one room generation flow without demo navigation or separate analysis/build actions', () => {
    const html = renderToStaticMarkup(<App />)
    expect(html).toContain('aria-label="Roometry home"')
    for (const text of ['Connected', 'Connecting', 'SETUP', 'Made for your space', 'Create a room', '<footer']) expect(html).not.toContain(text)
    expect(html).toContain('Main navigation')
    expect(html).toContain('>Explore</a>')
    expect(html).toContain('>My rooms</a>')
    expect(html).toContain('>Sign in</button>')
    expect(html).toContain('aria-label="Choose room photos"')
    expect(html.match(/>Create room<svg/g)).toHaveLength(1)
    for (const label of ['Explore example', 'Object catalog', 'Example bedroom', 'Analyze photos', 'Build base scene', 'Upload current inputs']) {
      expect(html).not.toContain(label)
    }
    expect(html).not.toContain('aria-label="3D room viewer"')
  })
})
