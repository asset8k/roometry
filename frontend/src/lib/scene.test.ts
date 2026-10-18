import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { loadExampleScene, loadAlternativeScene, parseRoomScene } from './scene'

describe('scene loading', () => {
  it('loads the full scene without relying on a backend or model call', () => {
    const scene = loadExampleScene()
    expect(scene.schema_version).toBe(1)
    expect(scene.objects).toHaveLength(9)
    expect(scene.openings).toHaveLength(2)
  })

  it('uses a generated copy of the authoritative development fixture', () => {
    const source = JSON.parse(readFileSync(new URL('../../../data/fixtures/bedroom_scene.json', import.meta.url), 'utf8'))
    expect(loadExampleScene()).toEqual(source)
  })

  it('loads the authored alternative with the same room and stable object identities', () => {
    const source = JSON.parse(readFileSync(new URL('../../../data/fixtures/bedroom_after_scene.json', import.meta.url), 'utf8'))
    const after = loadAlternativeScene(), before = loadExampleScene()
    expect(after).toEqual(source)
    expect(after.room).toEqual(before.room)
    expect(after.openings).toEqual(before.openings)
    expect(after.objects.map(object => object.id)).toEqual(before.objects.map(object => object.id))
    expect(after.objects).not.toEqual(before.objects)
  })

  it('returns independent fixture copies on each load', () => {
    for (const load of [loadExampleScene, loadAlternativeScene]) {
      const scene = load(), original = structuredClone(scene)
      scene.objects[2].position.x = 99
      expect(load()).toEqual(original)
    }
  })

  it.each([
    ['unsupported version', (data: ReturnType<typeof loadExampleScene>) => { Object.assign(data, { schema_version: 2 }) }],
    ['numeric string', (data: ReturnType<typeof loadExampleScene>) => { Object.assign(data.room, { width_m: '4' }) }],
    ['nonfinite coordinate', (data: ReturnType<typeof loadExampleScene>) => { data.objects[0].position.x = Infinity }],
    ['unapproved asset', (data: ReturnType<typeof loadExampleScene>) => { Object.assign(data.objects[0], { asset_id: 'unknown_asset' }) }],
    ['unknown property', (data: ReturnType<typeof loadExampleScene>) => { Object.assign(data.room, { unexpected: true }) }],
    ['malformed color', (data: ReturnType<typeof loadExampleScene>) => { data.objects[0].color = 'green' }],
  ])('rejects %s before rendering', (_, mutate) => {
    const scene = structuredClone(loadExampleScene())
    mutate(scene)
    expect(() => parseRoomScene(scene)).toThrow('Invalid scene')
  })

  it('rejects incomplete scenes', () => {
    expect(() => parseRoomScene({ schema_version: 1 })).toThrow('Invalid scene')
  })
})
