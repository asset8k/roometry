import { agentFailure, parseAgentRun, AgentRunError } from './agent-run'
import type { ClarificationAnswer, StartRunRequest } from './agent-run'

const baseUrl = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/+$/, '')
async function request(path: string, signal: AbortSignal, body?: unknown) {
  const response = await fetch(`${baseUrl}/agent-runs${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
    credentials: 'include', cache: 'no-store',
    signal: AbortSignal.any([signal, AbortSignal.timeout(body === undefined || path.endsWith('/cancel') ? 10000 : 1800000)]),
  })
  let data: unknown
  try { data = await response.json() } catch { throw new AgentRunError('The service response was interrupted. Check status to recover the same generation.') }
  if (!response.ok) throw agentFailure(data, response.status)
  return parseAgentRun(data)
}

export const agentClient = {
  start: (body: StartRunRequest, signal: AbortSignal) => request('', signal, body),
  find: (requestId: string, signal: AbortSignal) => request(`?request_id=${encodeURIComponent(requestId)}`, signal),
  read: (runId: string, signal: AbortSignal) => request(`/${encodeURIComponent(runId)}`, signal),
  answer: (runId: string, body: ClarificationAnswer, signal: AbortSignal) => request(`/${encodeURIComponent(runId)}/answer`, signal, body),
  cancel: (runId: string, signal: AbortSignal) => request(`/${encodeURIComponent(runId)}/cancel`, signal, {}),
}
export type AgentClient = typeof agentClient
