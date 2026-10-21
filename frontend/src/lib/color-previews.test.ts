import { describe, expect, it } from 'vitest'
import { applyColorPreviews } from './color-previews'
import { loadExampleScene } from './test-support/scenes'

describe('local color previews', () => {
  it('changes only the requested object and preserves the source fixture', () => {
    const source = loadExampleScene()
    const original = structuredClone(source)
    const preview = applyColorPreviews(source, { rug_1: '#abcdef' })
    expect(preview.objects.find(object => object.id === 'rug_1')?.color).toBe('#ABCDEF')
    expect(source).toEqual(original)
    expect(preview.objects.filter(object => object.id !== 'rug_1')).toEqual(source.objects.filter(object => object.id !== 'rug_1'))
  })

  it('protects locked colors while allowing fixed objects to be recolored', () => {
    const source = loadExampleScene()
    const preview = applyColorPreviews(source, { bed_1: '#FF0000', tv_1: '#112233' })
    expect(preview.objects.find(object => object.id === 'bed_1')?.color).toBe('#808080')
    expect(preview.objects.find(object => object.id === 'tv_1')?.color).toBe('#112233')
  })

  it.each(['green', '#FFF', '#GGGGGG', '#123456\n'])('ignores malformed preview color %s', color => {
    expect(applyColorPreviews(loadExampleScene(), { rug_1: color })).toEqual(loadExampleScene())
  })

  it('ignores unknown object IDs and resets without changing source dimensions', () => {
    const source = loadExampleScene()
    expect(applyColorPreviews(source, { missing_1: '#112233' })).toEqual(source)
    expect(applyColorPreviews(source, {})).toEqual(source)
  })
})
