import { describe, expect, it } from 'vitest'
import Ajv2020 from 'ajv/dist/2020.js'
import requestSchema from './layout-score-request.schema.json'
import fixture from './test-support/fixtures/layout_score.json'
import { loadExampleScene } from './test-support/scenes'
import { parseLayoutScore } from './layout-scoring'

describe('shared soft layout scoring contracts', () => {
  it('accepts the generated score fixture with unassessed aesthetics and no invented average', () => {
    const report = parseLayoutScore(fixture)
    expect(report.geometry.valid).toBe(true)
    expect(report.aesthetics.score).toBeNull()
    expect(report.overall_score).toBeNull()
    expect(report.workspace.score).toBeGreaterThan(0)
    expect(report.workstations[0].object_id).toBe('desk_1')
  })
  it('accepts a selected measurable goal even when unrelated criteria remain unassessed', () => {
    const report = parseLayoutScore({ ...fixture, requested_goals: ['workspace'], selected_criteria: ['workspace'], overall_score: fixture.workspace.score })
    expect(report.overall_score).toBe(report.workspace.score)
  })
  it('returns independent report copies', () => {
    const report = parseLayoutScore(fixture)
    report.warnings.push('Local preview')
    expect(parseLayoutScore(fixture).warnings).not.toContain('Local preview')
  })
  it.each([
    { overall_score: 100 },
    { requested_goals: [] },
    { requested_goals: ['workspace', 'workspace'] },
    { selected_criteria: ['workspace'] },
    { aesthetics: { ...fixture.aesthetics, score: 100 } },
    { workspace: { ...fixture.workspace, score: null } },
    { geometry: { ...fixture.geometry, valid: false } },
    { geometry: { ...fixture.geometry, valid: false, errors: [{ code: 'object_outside_room', entity_ids: ['desk_1'], message: 'Outside room.' }] } },
    { storage: { ...fixture.storage, score: Infinity } },
    { policy_version: 'unknown-policy' },
  ])('rejects contradictory or malformed score reports %j', patch => {
    expect(() => parseLayoutScore({ ...fixture, ...patch })).toThrow()
  })
  it('exports the stateless score request contract with existing scene and goal types', () => {
    const valid = new Ajv2020({ strict: true }).compile(requestSchema)
    expect(valid({ scene: loadExampleScene(), goals: ['more_open_space'] })).toBe(true)
    expect(valid({ scene: loadExampleScene(), goals: ['ignore_collisions'] })).toBe(false)
    expect(valid({ scene: loadExampleScene(), policy: { storage_reference_m3: 0 } })).toBe(false)
  })
})
