import Ajv2020 from 'ajv/dist/2020.js'
import addFormats from 'ajv-formats'
import optionsSchema from './room-input-options.schema.json'
import limitsSchema from './room-input-limits.schema.json'
import responseSchema from './room-input-response.schema.json'
import errorSchema from './room-input-error.schema.json'
import type { RoomInputOptions, RoomType, Goals } from './room-input-options.generated'
import type { UploadLimits } from './room-input-limits.generated'
import type { PreparedRoomInput } from './room-input-response.generated'
import type { UploadErrorResponse, UploadIssue } from './room-input-error.generated'

export type InputLimits = Required<UploadLimits>
export type GoalChoice = Goals[number]
export interface InputDraft {
  roomType: RoomType | ''; description: string; goals: GoalChoice[]; goalText: string
  width: string; length: string; height: string
  style: string; budget: string; currency: string
}
export const emptyDraft = (): InputDraft => ({
  roomType: '', description: '', goals: [], goalText: '', width: '', length: '', height: '',
  style: '', budget: '', currency: 'USD',
})
const ajv = new Ajv2020({ allErrors: true, strict: true })
addFormats(ajv)
const validOptions = ajv.compile<RoomInputOptions>(optionsSchema)
const validLimits = ajv.compile<UploadLimits>(limitsSchema)
const validResponse = ajv.compile<PreparedRoomInput>(responseSchema)
const validError = ajv.compile<UploadErrorResponse>(errorSchema)

export class RoomInputError extends Error {
  constructor(message: string, readonly issues: UploadIssue[] = [], readonly connected?: boolean, readonly uncertain = false) {
    super(message)
    this.name = 'RoomInputError'
  }
}

export function parseInputLimits(data: unknown): InputLimits {
  if (!validLimits(data)) throw new RoomInputError('The upload limits could not be read.', [], true)
  // The backend serializes defaults too. Require them rather than guessing a
  // format/count/measurement policy when a response is incomplete.
  const required: (keyof InputLimits)[] = [
    'min_images', 'allowed_media_types', 'allowed_extensions', 'options_max_bytes', 'max_description_chars',
    'max_goal_text_chars', 'max_constraints_chars', 'max_style_chars', 'max_budget_amount', 'max_room_side_m', 'max_room_height_m',
  ]
  if (required.some(key => data[key] === undefined)) throw new RoomInputError('The upload limits are incomplete.', [], true)
  const limits = data as InputLimits
  if (!limits.allowed_extensions.length || !limits.allowed_media_types.length || limits.max_file_bytes > limits.max_total_file_bytes
    || limits.max_body_bytes <= limits.max_total_file_bytes + limits.options_max_bytes || limits.min_edge_px > limits.max_edge_px
    || limits.max_pixels < limits.min_edge_px ** 2 || limits.prepared_max_edge_px > limits.max_edge_px
    || limits.max_room_side_m <= 0 || limits.max_room_height_m <= 0) {
    throw new RoomInputError('The upload limits are inconsistent.', [], true)
  }
  return structuredClone(limits)
}

export function parsePreparedInput(data: unknown): PreparedRoomInput {
  if (!validResponse(data) || new Set(data.images.map(image => image.image_id)).size !== data.images.length) {
    throw new RoomInputError('The service returned an unexpected upload response.', [], true, true)
  }
  return structuredClone(data)
}

export function parseUploadIssues(data: unknown): UploadIssue[] {
  return validError(data) ? data.detail : []
}

function numberInput(value: string, label: string, max: number, allowZero = false): number | null {
  const trimmed = value.trim()
  if (!trimmed) return null
  const number = Number(trimmed)
  if (!/^[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(trimmed) || !Number.isFinite(number)
    || number > max || (allowZero ? number < 0 : number <= 0)) {
    throw new RoomInputError(`Enter ${label} ${allowZero ? 'from 0' : 'greater than 0'} and no more than ${max}.`)
  }
  return number
}

export function draftOptions(draft: InputDraft, limits: InputLimits): RoomInputOptions {
  const width = numberInput(draft.width, 'width in meters', limits.max_room_side_m)
  const length = numberInput(draft.length, 'length in meters', limits.max_room_side_m)
  const height = numberInput(draft.height, 'height in meters', limits.max_room_height_m)
  const budget = numberInput(draft.budget, 'a budget', limits.max_budget_amount, true)
  const dimensions = [width, length, height].some(value => value !== null)
    ? { width_m: width, length_m: length, height_m: height, measurements_source: 'user_confirmed' as const } : null
  const options = {
    room_type: draft.roomType || null, description: draft.description.trim() || null,
    goals: draft.goals.length ? draft.goals : ['general_analysis'], goal_text: draft.goalText.trim() || null,
    dimensions, style: draft.style.trim() || null,
    budget: budget === null ? null : { amount: budget, currency: draft.currency.trim().toUpperCase() },
  }
  if (!validOptions(options) || new Set(options.goals).size !== options.goals.length) {
    throw new RoomInputError('Check the room details, note lengths, and three-letter currency code.')
  }
  if (new TextEncoder().encode(JSON.stringify(options)).byteLength > limits.options_max_bytes) {
    throw new RoomInputError('Your room notes are too long together. Shorten them and try again.')
  }
  return options
}

export function checkPhotoFiles(files: readonly File[], limits: InputLimits, requireMinimum = true): UploadIssue[] {
  const issues: UploadIssue[] = []
  if (files.length > limits.max_images || (requireMinimum && files.length < limits.min_images)) {
    issues.push({ code: 'invalid_image_count', message: `Choose ${limits.min_images}–${limits.max_images} photos of one room.` })
  }
  let total = 0
  files.forEach((file, file_index) => {
    total += file.size
    const extension = file.name.slice(file.name.lastIndexOf('.')).toLowerCase()
    if (!(limits.allowed_extensions as readonly string[]).includes(extension)
      || (file.type && file.type !== 'application/octet-stream' && !(limits.allowed_media_types as readonly string[]).includes(file.type.toLowerCase()))) {
      issues.push({ code: 'unsupported_image_type', message: 'Choose a JPEG, PNG, WebP, HEIC, HEIF, or AVIF photo.', file_index })
    } else if (file.size === 0) issues.push({ code: 'invalid_image', message: 'This file is empty.', file_index })
    else if (file.size > limits.max_file_bytes) issues.push({ code: 'file_too_large', message: `This photo exceeds ${formatBytes(limits.max_file_bytes)}.`, file_index })
  })
  if (total > limits.max_total_file_bytes) issues.push({ code: 'total_files_too_large', message: `Photos must total no more than ${formatBytes(limits.max_total_file_bytes)}.` })
  return issues
}

export function formatBytes(bytes: number): string {
  return bytes >= 1048576 ? `${Number((bytes / 1048576).toFixed(1))} MiB` : `${Math.max(1, Math.ceil(bytes / 1024))} KiB`
}
