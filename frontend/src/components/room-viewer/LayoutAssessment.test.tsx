import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import { LayoutAssessmentDetails } from './LayoutAssessment'
import { parseLayoutScore } from '../../lib/layout-scoring'
import fixture from '../../lib/test-support/fixtures/layout_score.json'
import { loadExampleScene } from '../../lib/test-support/scenes'

describe('layout check explanations', () => {
  it('keeps technical scoring formulas out of the concise clearance disclosure', () => {
    const report = parseLayoutScore(fixture)
    report.geometry.door_clearance!.doors[0].swing_status = 'unknown'
    const html = renderToStaticMarkup(<LayoutAssessmentDetails state={{ status: 'ready', report }} scene={loadExampleScene()} retry={vi.fn()} />)
    expect(html).toContain('Door swing unknown')
    expect(html).toContain('Minimum width 0.6 m; 0.8 m preferred')
    expect(html).not.toContain('storage_reference_m3')
    expect(html).not.toContain('/ 100')
    expect(html).not.toContain('No combined score')
    expect(html).not.toContain('Walking grid')
    expect(html).toContain('<details class="layout-assessment"')
    expect(html).not.toContain('<details class="layout-assessment" open')
  })
  it('labels existing blocked clearance with named furniture, leaving the room usable', () => {
    const report = parseLayoutScore(fixture)
    report.geometry.door_clearance!.doors[0].access_status = 'blocked'
    report.geometry.door_clearance!.doors[0].access_object_ids = ['desk_1']
    report.geometry.walking_space!.status = 'blocked'
    const html = renderToStaticMarkup(<LayoutAssessmentDetails state={{ status: 'ready', report }} scene={loadExampleScene()} retry={vi.fn()} />)
    expect(html).toContain('Access blocked by Desk')
    expect(html).toContain('North door')
    expect(html).toContain('<strong>Desk</strong>')
    expect(html).toContain('Walking routes blocked')
    expect(html).toContain('Existing problems stay visible')
  })
  it('provides retry on failed assessment without implying scene generation failed', () => {
    const html = renderToStaticMarkup(<LayoutAssessmentDetails state={{ status: 'error', report: null }} scene={loadExampleScene()} retry={vi.fn()} />)
    expect(html).toContain('Your room is still available')
    expect(html).toContain('Retry checks')
  })
})
