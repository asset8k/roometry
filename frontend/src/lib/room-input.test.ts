import { describe, expect, it } from 'vitest'
import { checkPhotoFiles, draftOptions, emptyDraft, parseInputLimits, parsePreparedInput } from './room-input'
import { limits, photo, prepared } from './test-support/room-input'

describe('photo input boundaries', () => {
  it('uses complete server policy without guessing missing fields', () => {
    expect(parseInputLimits(limits)).toEqual(limits)
    for (const invalid of [{ ...limits, allowed_extensions: undefined }, { ...limits, allowed_media_types: [] }, { ...limits, max_images: 11 },
      { ...limits, max_body_bytes: 1 }, { ...limits, max_room_height_m: -1 }]) expect(() => parseInputLimits(invalid)).toThrow()
  })
  it('submits the current room for reconstruction with optional, user-entered measurements', () => {
    const options = draftOptions({ ...emptyDraft(), roomType: 'bedroom', width: '4', description: ' My bedroom ' }, limits)
    expect(options.dimensions).toEqual({ width_m: 4, length_m: null, height_m: null, measurements_source: 'user_confirmed' })
    expect(options.goals).toEqual(['general_analysis'])
    expect(options.description).toBe('My bedroom')
    expect(options.room_type).toBe('bedroom')
    expect(draftOptions({ ...emptyDraft(), width: '4', length: '4' }, limits).dimensions?.measurements_source).toBe('user_confirmed')
    expect(draftOptions(emptyDraft(), limits).dimensions).toBeNull()
  })
  it('does not forward legacy improvement preferences hidden in a draft', () => {
    const legacyDraft = { ...emptyDraft(), goals: ['workspace'], goalText: 'Move my desk',
      budget: '500', currency: 'USD', style: 'Minimalist', constraints: 'Replace the chair' }
    const options = draftOptions(legacyDraft, limits)
    expect(options).toEqual({ room_type: null, description: null, dimensions: null, goals: ['general_analysis'] })
  })
  it.each(['0', '-1', 'NaN', 'Infinity', '0x10', '31'])('rejects invalid width %s', width => {
    expect(() => draftOptions({ ...emptyDraft(), width }, limits)).toThrow()
  })
  it('keeps description limits and unknown room details without asking for preferences', () => {
    expect(draftOptions({ ...emptyDraft(), description: '😀'.repeat(2000) }, limits).description).toBe('😀'.repeat(2000))
    expect(() => draftOptions({ ...emptyDraft(), description: '😀'.repeat(2001) }, limits)).toThrow()
    expect(draftOptions(emptyDraft(), limits)).toEqual({ room_type: null, description: null, dimensions: null, goals: ['general_analysis'] })
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
