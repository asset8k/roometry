import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import { LayoutAssessmentDetails } from './LayoutAssessment'
import { parseLayoutScore } from '../../lib/layout-scoring'
import fixture from '../../lib/test-support/fixtures/layout_score.json'
import { loadExampleScene } from '../../lib/test-support/scenes'

describe('layout check explanations', () => {
  it('shows unknown swings and unassessed style without a fake overall score', () => {
    const report = parseLayoutScore(fixture)
    report.geometry.door_clearance!.doors[0].swing_status = 'unknown'
    const html = renderToStaticMarkup(<LayoutAssessmentDetails state={{ status: 'ready', report }} scene={loadExampleScene()} retry={vi.fn()} />)
    expect(html).toContain('Swing not assessed: hinge or swing direction unknown')
    expect(html).toContain('Style</dt><dd>Not assessed')
    expect(html).toContain('Storage size proxy')
    expect(html).toContain('No combined score')
    expect(html).toContain('do not measure photo accuracy')
    expect(html).toContain('Walking grid:')
    expect(html).not.toContain('Style</dt><dd>0')
  })
  it('labels existing blocked clearance with named furniture, leaving the room usable', () => {
    const report = parseLayoutScore(fixture)
    report.geometry.door_clearance!.doors[0].access_status = 'blocked'
    report.geometry.door_clearance!.doors[0].access_object_ids = ['desk_1']
    report.geometry.walking_space!.status = 'blocked'
    const html = renderToStaticMarkup(<LayoutAssessmentDetails state={{ status: 'ready', report }} scene={loadExampleScene()} retry={vi.fn()} />)
    expect(html).toContain('Access zone blocked (desk)')
    expect(html).toContain('Routes blocked')
    expect(html).toContain('Existing clearance problems remain visible')
  })
  it('provides retry on failed assessment without implying scene generation failed', () => {
    const html = renderToStaticMarkup(<LayoutAssessmentDetails state={{ status: 'error', report: null }} scene={loadExampleScene()} retry={vi.fn()} />)
    expect(html).toContain('Your room is still available')
    expect(html).toContain('Retry checks')
  })
})
