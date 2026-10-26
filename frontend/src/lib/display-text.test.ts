import { expect, it } from 'vitest'
import { conciseText, readableText } from './display-text'

it('keeps machine identifiers out of recommendations and bounds long previews', () => {
  expect(readableText('Compare storage_reference_m3 and accessible_storage_fraction.')).toBe('Compare typical storage size and accessible storage fraction.')
  const text = conciseText('Consider moving the desk near the window. '.repeat(20))
  expect(text.length).toBeLessThanOrEqual(220)
  expect(text.endsWith('…')).toBe(true)
  expect(conciseText('Keep the desk near the window.')).toBe('Keep the desk near the window.')
})
