import Ajv2020 from 'ajv/dist/2020.js'
import { describe, expect, it } from 'vitest'
import schema from './room-input-options.schema.json'
import fixture from '../data/room_input_options.json'
import type { RoomInputOptions } from './room-input-options.generated'

const validate = new Ajv2020({ strict: true, allErrors: true }).compile<RoomInputOptions>(schema)

describe('shared room-input contract', () => {
  it('accepts optional and partial inputs without filling unknown measurements', () => {
    const minimal: RoomInputOptions = {}
    const partial: RoomInputOptions = { dimensions: { width_m: 4, height_m: null }, budget: { amount: 0, currency: 'KZT' } }
    expect(validate(minimal)).toBe(true)
    expect(validate(partial)).toBe(true)
    expect(partial.dimensions?.length_m).toBeUndefined()
  })

  it('accepts the normalized Python-exported fixture', () => {
    expect(validate(fixture)).toBe(true)
    expect(fixture.dimensions.height_m).toBeNull()
    expect(fixture.dimensions.measurements_source).toBe('estimated')
  })

  it.each([
    { dimensions: { width_m: '4' } }, { dimensions: { width_m: true } },
    { dimensions: { height_m: Infinity } }, { dimensions: { width_m: 0 } },
    { dimensions: { depth_m: 4 } }, { budget: { amount: 10 } },
    { budget: { amount: -1, currency: 'USD' } }, { goals: ['shopping'] },
    { room_type: 'garage' }, { description: 'x'.repeat(2001) },
    { images: ['https://example.com/private-photo.jpg'] },
  ])('rejects invalid JSON shape before submission: %j', data => {
    expect(validate(data)).toBe(false)
  })

  // Normalization, uniqueness, and cross-field rules are Python's authority;
  // this schema check intentionally does not pretend to implement them.
})
