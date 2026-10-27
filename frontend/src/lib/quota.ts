import { useEffect, useState } from 'react'

export type Allowance = { enabled: boolean; sessions_limit: number; sessions_remaining: number | null; actions_limit: number; actions_remaining: number | null; resets_at: string }
const eventName = 'roometry:usage'
export function refreshAllowance() { if (typeof window !== 'undefined') window.dispatchEvent(new Event(eventName)) }
export function parseAllowance(value: unknown): Allowance {
  if (!value || typeof value !== 'object') throw new Error('Invalid allowance')
  const data = value as Allowance
  if (typeof data.enabled !== 'boolean' || !Number.isInteger(data.sessions_limit) || data.sessions_limit < 1
    || !Number.isInteger(data.actions_limit) || data.actions_limit < 1 || !Number.isFinite(Date.parse(data.resets_at))
    || [data.sessions_remaining, data.actions_remaining].some(v => v !== null && (!Number.isInteger(v) || v < 0))
    || (data.sessions_remaining !== null && data.sessions_remaining > data.sessions_limit)
    || (data.actions_remaining !== null && data.actions_remaining > data.actions_limit)) throw new Error('Invalid allowance')
  return data
}
export function useAllowance(runId?: string, revision?: unknown) {
  const [state, setState] = useState<{ id?: string; value: Allowance } | null>(null)
  useEffect(() => {
    let active = true, sequence = 0, resetTimer: ReturnType<typeof setTimeout> | undefined
    const abort = new AbortController()
    const load = async () => {
      const request = ++sequence
      try {
        const base = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/+$/, '')
        const response = await fetch(`${base}/usage${runId ? `?run_id=${encodeURIComponent(runId)}` : ''}`, { credentials: 'include', cache: 'no-store', signal: AbortSignal.any([abort.signal, AbortSignal.timeout(10000)]) })
        if (!response.ok) return
        const value = parseAllowance(await response.json())
        if (active && request === sequence) {
          setState({ id: runId, value })
          clearTimeout(resetTimer)
          if (value.enabled) resetTimer = setTimeout(load, Math.max(1000, Math.min(86400000, Date.parse(value.resets_at) - Date.now() + 100)))
        }
      } catch { /* The backend remains authoritative when the read is unavailable. */ }
    }
    void load()
    window.addEventListener(eventName, load); window.addEventListener('focus', load)
    return () => { active = false; abort.abort(); clearTimeout(resetTimer); window.removeEventListener(eventName, load); window.removeEventListener('focus', load) }
  }, [runId, revision])
  return state?.id === runId ? state?.value ?? null : null
}
