import { describe, expect, it } from 'vitest'
import { checkPhotoFiles, draftOptions, emptyDraft, parseInputLimits, parsePreparedInput } from './room-input'
import { limits, photo, prepared } from './test-support/room-input'

describe('photo input boundaries', () => {
  it('uses complete server policy without guessing missing fields', () => {
    expect(parseInputLimits(limits)).toEqual(limits)
    for (const invalid of [{ ...limits, allowed_extensions: undefined }, { ...limits, allowed_media_types: [] }, { ...limits, max_images: 11 },
      { ...limits, max_body_bytes: 1 }, { ...limits, max_room_height_m: -1 }]) expect(() => parseInputLimits(invalid)).toThrow()
  })
  it('preserves unknown dimensions, zero budgets and explicit confirmation', () => {
    const options = draftOptions({ ...emptyDraft(), width: '4', budget: '0', currency: 'kzt', goalText: ' keep desk ' }, limits)
    expect(options.dimensions).toEqual({ width_m: 4, length_m: null, height_m: null, measurements_source: 'estimated' })
    expect(options.budget).toEqual({ amount: 0, currency: 'KZT' })
    expect(options.goals).toEqual(['general_analysis'])
    expect(options.goal_text).toBe('keep desk')
    expect(draftOptions({ ...emptyDraft(), goals: ['better_aesthetics'], goalText: 'Keep the bed; make the room cozier.' }, limits)).toMatchObject({
      goals: ['better_aesthetics'], goal_text: 'Keep the bed; make the room cozier.',
    })
    expect(draftOptions({ ...emptyDraft(), width: '4', length: '4', confirmed: true }, limits).dimensions?.measurements_source).toBe('user_confirmed')
    expect(draftOptions(emptyDraft(), limits).dimensions).toBeNull()
  })
  it.each(['0', '-1', 'NaN', 'Infinity', '0x10', '31'])('rejects invalid width %s', width => {
    expect(() => draftOptions({ ...emptyDraft(), width }, limits)).toThrow()
  })
  it('checks combined UTF-8 metadata bytes as well as character lengths', () => {
    expect(() => draftOptions({ ...emptyDraft(), description: '😀'.repeat(2000), goalText: '😀'.repeat(2000), style: '😀'.repeat(100) }, limits)).toThrow()
    expect(() => draftOptions({ ...emptyDraft(), goals: ['workspace', 'workspace'] }, limits)).toThrow()
    expect(() => draftOptions({ ...emptyDraft(), budget: '1', currency: 'US' }, limits)).toThrow()
  })
  it('accepts ten files and the common iPhone/modern photo formats', () => {
    expect(checkPhotoFiles(Array.from({ length: 10 }, () => photo()), limits)).toEqual([])
    expect(checkPhotoFiles([photo('room.HEIC', ''), photo('room.heif', 'image/heif'), photo('room.avif', 'image/avif')], limits)).toEqual([])
    expect(checkPhotoFiles(Array.from({ length: 11 }, () => photo()), limits)[0].code).toBe('invalid_image_count')
    expect(checkPhotoFiles([], limits)[0].code).toBe('invalid_image_count')
  })
  it('checks format, empty, per-file and combined sizes before creating previews', () => {
    expect(checkPhotoFiles([photo('room.svg', 'image/svg+xml')], limits)[0].code).toBe('unsupported_image_type')
    expect(checkPhotoFiles([new File([], 'room.png')], limits)[0].code).toBe('invalid_image')
    expect(checkPhotoFiles([photo()], { ...limits, max_file_bytes: 1 })[0].code).toBe('file_too_large')
    expect(checkPhotoFiles([photo(), photo()], { ...limits, max_total_file_bytes: 12 })[0].code).toBe('total_files_too_large')
  })
  it('validates expiry syntax and image identity in returned metadata', () => {
    expect(parsePreparedInput(prepared()).status).toBe('ready')
    const source = prepared()
    for (const invalid of [{ ...source, expires_at: '2026-02-30T00:00:00Z' }, { ...source, expires_at: '2026-10-18T00:00:00' },
      { ...source, images: [source.images[0], source.images[0]] }, { ...source, storage_key: 'private' }]) expect(() => parsePreparedInput(invalid)).toThrow()
  })
})
