import type { HealthResponse } from './types'
import type { RoomScene } from './scene.generated'
import type { PreviewEditResponse } from './edit-response.generated'
import { parseEditPreview } from './scene-edits'
import type { SceneEdit } from './scene-edits'
import type { RoomInputOptions } from './room-input-options.generated'
import { parseInputLimits, parsePreparedInput, parseUploadIssues, RoomInputError } from './room-input'

const baseUrl = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/+$/, '')

export async function fetchHealth(signal: AbortSignal): Promise<HealthResponse> {
  const response = await fetch(`${baseUrl}/health`, {
    signal: AbortSignal.any([signal, AbortSignal.timeout(5000)]),
    cache: 'no-store',
  })

  if (!response.ok) throw new Error(`Health check failed: ${response.status}`)

  const data: unknown = await response.json()
  if (
    typeof data !== 'object' || data === null ||
    !('status' in data) || data.status !== 'ok' ||
    !('service' in data) || data.service !== 'roometry'
  ) {
    throw new Error('Unexpected health response')
  }

  return { status: 'ok', service: 'roometry' }
}

/** Submit absolute transforms; preserve caller state on HTTP/network failures. */
export async function requestEditPreview(scene: RoomScene, edit: SceneEdit, signal: AbortSignal): Promise<PreviewEditResponse> {
  const response = await fetch(`${baseUrl}/scenes/preview-edit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scene, edit }),
    signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]),
    cache: 'no-store',
  })
  if (!response.ok) throw new Error(`Edit preview failed: ${response.status}`)
  return parseEditPreview(await response.json())
}

async function uploadResponse(response: Response): Promise<unknown> {
  let data: unknown
  try { data = await response.json() } catch {
    throw new RoomInputError('The service returned an unreadable response. Please try again.', [], true, true)
  }
  if (!response.ok) {
    const issues = parseUploadIssues(data)
    throw new RoomInputError(issues[0]?.message ?? 'The upload service could not complete this request. Please try again.', issues, true, !issues.length)
  }
  return data
}

export async function fetchUploadLimits(signal: AbortSignal) {
  const response = await fetch(`${baseUrl}/rooms/input-limits`, {
    credentials: 'include', cache: 'no-store', signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]),
  })
  return parseInputLimits(await uploadResponse(response))
}

export async function uploadRoomInput(files: readonly File[], options: RoomInputOptions, signal: AbortSignal) {
  const body = new FormData()
  for (const file of files) body.append('images', file, file.name)
  body.append('options', JSON.stringify(options))
  const response = await fetch(`${baseUrl}/rooms/inputs`, {
    method: 'POST', body, credentials: 'include', cache: 'no-store',
    signal: AbortSignal.any([signal, AbortSignal.timeout(120000)]),
  })
  const result = parsePreparedInput(await uploadResponse(response))
  if (result.images.length !== files.length || result.images.some((image, index) => image.original_bytes !== files[index].size)) {
    throw new RoomInputError('The upload response does not match your selected photos. Please try again.', [], true, true)
  }
  return result
}

export async function discardRoomInput(inputId: string, signal: AbortSignal): Promise<void> {
  const response = await fetch(`${baseUrl}/rooms/inputs/${encodeURIComponent(inputId)}`, {
    method: 'DELETE', credentials: 'include', cache: 'no-store',
    signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]),
  })
  if (response.status === 204 || response.status === 404) return
  if (response.ok) throw new RoomInputError('The service did not confirm deletion. Please try again.', [], true)
  await uploadResponse(response)
}
