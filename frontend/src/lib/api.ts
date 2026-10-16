import type { HealthResponse } from './types'

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
