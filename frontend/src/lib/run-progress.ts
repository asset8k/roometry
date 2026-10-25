/** Small public SSE contract. Full snapshots still pass the existing validators. */
export const progressStages = ['starting', 'planning', 'analyzing', 'clarifying', 'constructing', 'checking', 'reviewing', 'assessing', 'prepared', 'proposing', 'cancelling', 'finished', 'expired'] as const
export type ProgressStage = typeof progressStages[number]
export type StreamMode = 'connecting' | 'streaming' | 'reconnecting' | 'polling'
export interface RunProgress {
  version: 1; run_id: string; sequence: number; kind: 'reconstruction' | 'optimization'
  stage: ProgressStage; status: string; step: number; candidate: number | null; closed: boolean
}
const statuses = ['queued', 'running', 'awaiting_answer', 'completed', 'partial', 'incomplete', 'budget_exhausted', 'cancelled', 'expired', 'failed', 'prepared', 'no_improvement']
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
export function parseProgress(data: string, runId: string, cursor: string): RunProgress {
  if (data.length > 4096) throw new Error('Oversized progress event')
  const value = JSON.parse(data) as RunProgress
  if (!value || typeof value !== 'object' || Object.keys(value).sort().join() !== ['version','run_id','sequence','kind','stage','status','step','candidate','closed'].sort().join()
    || value.version !== 1 || value.run_id !== runId || !uuid.test(value.run_id)
    || !Number.isSafeInteger(value.sequence) || value.sequence < 1 || cursor !== `${runId}:${value.sequence}`
    || !['reconstruction', 'optimization'].includes(value.kind) || !progressStages.includes(value.stage)
    || !statuses.includes(value.status) || !Number.isSafeInteger(value.step) || value.step < 0 || value.step > 1000
    || (value.candidate !== null && (!Number.isInteger(value.candidate) || value.candidate < 1 || value.candidate > 5))
    || typeof value.closed !== 'boolean' || value.closed !== !['queued','running','awaiting_answer','prepared'].includes(value.status)) throw new Error('Invalid progress event')
  return value
}
export interface ProgressHooks {
  event: (event: RunProgress) => void
  mode: (mode: StreamMode) => void
  unavailable: () => void
}
export type ProgressObserver = (path: string, runId: string, kind: RunProgress['kind'], hooks: ProgressHooks) => () => void
const baseUrl = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/+$/, '')
export const reconstructionEvents = (id: string) => `/agent-runs/${encodeURIComponent(id)}/events`
export const optimizationEvents = (source: string, id: string) => `/agent-runs/${encodeURIComponent(source)}/optimizations/${encodeURIComponent(id)}/events`

/** Native EventSource sends Last-Event-ID on reconnection. Never invokes a mutation. */
export function createProgressObserver(factory?: (url: string, options: EventSourceInit) => EventSource): ProgressObserver {
  return (path, runId, kind, hooks) => {
    const create = factory ?? (typeof EventSource === 'undefined' ? null : (url: string, options: EventSourceInit) => new EventSource(url, options))
    if (!create) { hooks.mode('polling'); return () => {} }
    let source: EventSource
    let stopped = false, errors = 0, sequence = 0
    let watchdog: ReturnType<typeof setTimeout> | undefined
    const clear = () => { if (watchdog) clearTimeout(watchdog); watchdog = undefined }
    const stop = () => { stopped = true; clear(); source?.close() }
    const fallback = () => { if (!stopped) { stop(); hooks.mode('polling') } }
    const alive = () => { clear(); watchdog = setTimeout(fallback, 25_000) }
    hooks.mode('connecting')
    try { source = create(`${baseUrl}${path}`, { withCredentials: true }) }
    catch { fallback(); return stop }
    const receive = (snapshot: boolean) => (message: MessageEvent<string>) => {
      if (stopped) return
      try {
        const value = parseProgress(message.data, runId, message.lastEventId)
        if (value.kind !== kind) throw new Error('Wrong progress scope')
        if (!snapshot && value.sequence <= sequence) return
        sequence = value.sequence
        alive(); hooks.mode('streaming'); hooks.event(value)
        // A final authorized snapshot is required even if the original POST was lost.
        if (value.closed) fallback()
      } catch { fallback() }
    }
    source.addEventListener('progress', receive(false) as EventListener)
    source.addEventListener('snapshot', receive(true) as EventListener)
    source.addEventListener('heartbeat', () => { if (!stopped) { alive(); hooks.mode('streaming') } })
    source.addEventListener('unavailable', () => { if (!stopped) { stop(); hooks.unavailable() } })
    source.onopen = () => { if (!stopped) { alive(); hooks.mode('streaming') } }
    source.onerror = () => {
      if (stopped) return
      if (++errors >= 3) fallback()
      else { hooks.mode('reconnecting'); alive() }
    }
    alive()
    return stop
  }
}
export const observeProgress = createProgressObserver()

export const progressLabels: Record<ProgressStage, string> = {
  starting: 'Starting your room…', planning: 'Planning the next step…', analyzing: 'Reading your room photos…',
  clarifying: 'Waiting for your answer.', constructing: 'Building your room…', checking: 'Checking placement and clearances…',
  reviewing: 'Reviewing the result…', assessing: 'Assessing your room…', prepared: 'Ready to improve your room.',
  proposing: 'Exploring a better arrangement…', cancelling: 'Stopping further work…', finished: 'Finishing this run…', expired: 'This temporary run has expired.',
}
