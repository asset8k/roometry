import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import App from './App'

describe('application entry', () => {
  it('opens one room generation flow without demo navigation or separate analysis/build actions', () => {
    const html = renderToStaticMarkup(<App />)
    expect(html).toContain('aria-label="Roometry home"')
    expect(html).not.toContain('Check again')
    expect(html).toContain('aria-label="Choose room photos"')
    expect(html.match(/>Create room<svg/g)).toHaveLength(1)
    for (const label of ['Explore example', 'Object catalog', 'Example bedroom', 'Analyze photos', 'Build base scene', 'Upload current inputs']) {
      expect(html).not.toContain(label)
    }
    expect(html).not.toContain('aria-label="3D room viewer"')
  })
})
