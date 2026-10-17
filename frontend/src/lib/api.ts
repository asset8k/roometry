import type { HealthResponse } from './types'
import type { RoomScene } from './scene.generated'
import type { PreviewEditResponse } from './edit-response.generated'
import { parseEditPreview } from './scene-edits'
import type { SceneEdit } from './scene-edits'

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
