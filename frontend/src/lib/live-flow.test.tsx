/** Opt-in contract/UI verification of saved live output; never calls a provider.
 * ROOMETRY_LIVE_ARTIFACTS=/absolute/path/to/private/artifacts npm test -- src/lib/live-flow.test.tsx
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { expect, it, vi } from 'vitest'
import { parseAgentRun } from './agent-run'
import { checkOptimization, parseOptimization } from './optimization'
import type { StartOptimizationRequest } from './optimization'
import { SceneComparison, retainedImprovement } from './scene-comparison'
import RoomAssessmentCards from '../components/room-analysis/RoomAssessmentCards'
import ImprovementComparison from '../components/room-analysis/ImprovementComparison'

const directory = process.env.ROOMETRY_LIVE_ARTIFACTS
it.skipIf(!directory)('opens saved live results and preserves independent original/improved/editable views without network calls', () => {
  const read = (name: string) => JSON.parse(readFileSync(join(directory!, name), 'utf8'))
  const source = parseAgentRun(read('run.json'))
  const run = parseOptimization(read('optimization-run.json'))
  const request = read('optimization-request.json') as StartOptimizationRequest
  checkOptimization(run, source, request)
  expect(['completed', 'partial']).toContain(source.status)
  expect(source.working_scene).not.toBeNull()
  expect(source.room_assessment).not.toBeNull()
  const ratings = renderToStaticMarkup(createElement(RoomAssessmentCards, { assessment: source.room_assessment! }))
  expect(ratings).toContain('Space &amp; movement')
  expect(ratings).toContain('Storage &amp; organization')
  expect(ratings).toContain('Workspace')
  expect(ratings).toContain('Aesthetics &amp; cohesion')
  const network = vi.fn()
  const comparison = new SceneComparison(source.working_scene!, network)
  const snapshots = JSON.stringify({ source, run })
  expect(comparison.offer(run)).toBe(retainedImprovement(run))
  if (retainedImprovement(run)) {
    expect(comparison.getSnapshot().view).toBe('improved')
    expect(comparison.getSnapshot().editor.getSnapshot().scene).toEqual(run.result_scene)
    expect(renderToStaticMarkup(createElement(ImprovementComparison, { run }))).toContain('What changed')
    comparison.editCopy()
    const id = run.result_scene.objects[0].id
    comparison.setColor(id, '#123456')
    comparison.show('original')
    expect(comparison.getSnapshot().editor.getSnapshot().scene).toEqual(source.working_scene)
    comparison.show('improved')
    expect(comparison.getSnapshot().editor.getSnapshot().scene).toEqual(run.result_scene)
    comparison.show('editing')
    expect(comparison.getSnapshot().colors[id]).toBe('#123456')
  } else {
    expect(comparison.getSnapshot().improvements).toHaveLength(0)
    expect(comparison.getSnapshot().editor.getSnapshot().scene).toEqual(source.working_scene)
  }
  expect(JSON.stringify({ source, run })).toBe(snapshots)
  expect(network).not.toHaveBeenCalled()
})
