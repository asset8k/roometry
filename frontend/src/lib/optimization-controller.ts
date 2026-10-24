import { AgentRunError } from './agent-run'
import type { AgentState } from './agent-run'
import { checkOptimization, improvementPreferences, optimizationClient } from './optimization'
import type { Focus, ImprovementDraft, OptimizationClient, OptimizationRun, StartOptimizationRequest } from './optimization'

export interface ImprovementState {
  focus: Focus | null
  draft: ImprovementDraft
  phase: 'idle' | 'editing' | 'preparing' | 'prepared' | 'running' | 'completed' | 'no_improvement' | 'failed' | 'budget_exhausted' | 'cancelling' | 'cancelled' | 'error' | 'expired'
  result: OptimizationRun | null
  error: string | null
  recovery: boolean
}

/** A second user action prepares a linked request; it never reruns reconstruction. */
export class OptimizationController {
  private state: ImprovementState = { focus: null, draft: { budget: '', currency: 'USD', style: '', constraints: '' }, phase: 'idle', result: null, error: null, recovery: false }
  private listeners = new Set<() => void>()
  private pending: StartOptimizationRequest | null = null
  private active: AbortController | null = null
  private cancelRequested = false
  private executionRequested = false
  private connected = false
  private pollTimer: ReturnType<typeof setTimeout> | null = null
  private reading: AbortController | null = null
  private source: AgentState
  constructor(source: AgentState, private client: OptimizationClient = optimizationClient, private now = Date.now) { this.source = structuredClone(source) }
  getSnapshot = () => this.state
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener) } }
  private update(patch: Partial<ImprovementState>) { this.state = { ...this.state, ...patch }; this.listeners.forEach(listener => listener()) }
  available = () => ['completed', 'partial'].includes(this.source.status) && !!this.source.working_scene && Date.parse(this.source.expires_at) > this.now()
  locked = () => ['preparing', 'running', 'cancelling', 'prepared', 'expired'].includes(this.state.phase) || this.state.recovery
  select = (focus: Focus) => {
    if (this.locked()) return
    if (!this.available()) { this.update({ phase: 'expired', error: 'This reconstruction is no longer available for improvements.' }); return }
    this.stopPolling(); this.reading?.abort(); this.reading = null; this.active?.abort(); this.active = null
    this.pending = null; this.executionRequested = false; this.cancelRequested = false
    this.update({ focus, phase: 'editing', error: null, result: null })
  }
  change = (draft: Partial<ImprovementDraft>) => { if (!this.locked()) this.update({ draft: { ...this.state.draft, ...draft }, error: null }) }
  close = () => { if (!this.locked()) this.update({ focus: null, phase: 'idle', error: null }) }
  connect = () => {
    this.connected = true
    const timer = setTimeout(() => { this.stopPolling(); this.reading?.abort(); this.reading = null; this.active?.abort(); this.active = null; this.update({ phase: 'expired', recovery: false, error: 'This temporary reconstruction has expired. Generate again to request improvements.' }) }, Math.max(0, Date.parse(this.source.expires_at) - this.now()))
    return () => {
      clearTimeout(timer); this.connected = false; this.stopPolling(); this.reading?.abort(); this.reading = null
      this.active?.abort(); this.active = null
      if (this.state.result && (this.state.result.status === 'running' || (this.executionRequested && this.state.result.status === 'prepared'))) {
        void this.client.cancel(this.source.run_id, this.state.result.run_id, AbortSignal.timeout(10000)).catch(() => {})
      }
    }
  }
  prepare = async () => {
    if (this.locked() || !this.state.focus) return
    if (!this.available()) { this.update({ phase: 'expired', error: 'This temporary reconstruction has expired.' }); return }
    try {
      this.pending = { request_id: crypto.randomUUID(), focus: this.state.focus, preferences: improvementPreferences(this.state.draft) }
    } catch (error) { this.update({ error: (error as Error).message }); return }
    await this.submit()
  }
  private async submit() {
    if (!this.pending || this.active) return
    const body = this.pending, abort = new AbortController()
    this.active = abort
    this.update({ phase: this.cancelRequested ? 'cancelling' : 'preparing', recovery: false, error: null })
    try {
      const result = this.cancelRequested && this.state.result
        ? await this.client.cancel(this.source.run_id, this.state.result.run_id, abort.signal)
        : await this.client.prepare(this.source.run_id, body, abort.signal)
      if (abort.signal.aborted) return
      checkOptimization(result, this.source, body)
      if (this.state.result && this.state.result.run_id !== result.run_id) throw new AgentRunError('The service returned a different improvement request.')
      if (Date.parse(result.expires_at) <= this.now()) throw new AgentRunError('This temporary improvement request has expired.', 404)
      this.accept(result)
    } catch (error) {
      if (abort.signal.aborted) return
      const expired = error instanceof AgentRunError && error.status === 404
      const rejected = error instanceof AgentRunError && [409, 422, 429].includes(error.status)
      if (rejected && !this.state.result) this.pending = null
      this.update({ phase: expired ? 'expired' : 'error', recovery: !expired && !rejected,
        error: error instanceof AgentRunError ? error.message : 'The connection was interrupted. Retry to recover the same improvement request.' })
    } finally { if (this.active === abort) this.active = null }
  }
  private stopPolling() { if (this.pollTimer) clearTimeout(this.pollTimer); this.pollTimer = null }
  private schedule() {
    this.stopPolling()
    if (this.connected && this.state.result && (this.state.result.status === 'running' || this.state.phase === 'cancelling')) this.pollTimer = setTimeout(() => { void this.poll() }, 1200)
  }
  private accept(result: OptimizationRun) {
    if (!this.pending) return
    checkOptimization(result, this.source, this.pending)
    if (Date.parse(result.expires_at) <= this.now()) throw new AgentRunError('This temporary improvement request has expired.', 404)
    const previous = this.state.result
    const terminal = !['prepared', 'running'].includes(result.status)
    if (previous && previous.run_id !== result.run_id) throw new AgentRunError('The service returned a different improvement request.')
    if (previous && (previous.usage.model_calls > result.usage.model_calls
      || previous.attempts.length > result.attempts.length || (!['prepared', 'running'].includes(previous.status) && !terminal))) return
    if (previous && previous.status !== 'prepared' && result.status === 'prepared') return
    if (terminal) { this.cancelRequested = false; this.executionRequested = false; this.stopPolling() }
    this.update({ result, recovery: false, error: null, phase: this.cancelRequested && !terminal ? 'cancelling' : this.executionRequested && result.status === 'prepared' ? 'running' : result.status })
    this.schedule()
  }
  start = async () => {
    if (this.locked()) return
    this.executionRequested = true
    await this.prepare()
    if (this.state.result?.status === 'prepared' && !this.state.recovery) await this.execute()
  }
  execute = async () => {
    const result = this.state.result
    if (!result || this.active || result.status !== 'prepared') return
    this.executionRequested = true
    await this.dispatchExecution()
  }
  private async dispatchExecution() {
    if (!this.state.result || this.active) return
    const abort = new AbortController(); this.active = abort
    this.update({ phase: 'running', recovery: false, error: null })
    // A read can display progress while the execute request awaits completion.
    if (this.connected) this.pollTimer = setTimeout(() => { void this.poll() }, 1200)
    try {
      const result = await this.client.execute(this.source.run_id, this.state.result.run_id, abort.signal)
      if (!abort.signal.aborted) this.accept(result)
    } catch (error) {
      if (!abort.signal.aborted && this.state.result && ['prepared', 'running'].includes(this.state.result.status)) this.executionFailure(error)
    } finally { if (this.active === abort) this.active = null }
  }
  private executionFailure(error: unknown) {
    if (this.state.result && !['prepared', 'running'].includes(this.state.result.status)) return
    this.stopPolling()
    const expired = error instanceof AgentRunError && error.status === 404
    this.update({ phase: expired ? 'expired' : 'error', recovery: !expired,
      error: error instanceof AgentRunError ? error.message : 'The connection was interrupted. Check the same improvement run to recover it.' })
  }
  poll = async () => {
    if (!this.state.result || this.reading) return
    const abort = this.reading = new AbortController()
    try {
      const result = await this.client.read(this.source.run_id, this.state.result.run_id, abort.signal)
      if (!abort.signal.aborted) this.accept(result)
    } catch (error) { if (!abort.signal.aborted) this.executionFailure(error) }
    finally { if (this.reading === abort) this.reading = null }
  }
  retry = async () => {
    if (this.cancelRequested) { await this.submit(); return }
    if (this.executionRequested && this.state.result) { await this.dispatchExecution(); return }
    await this.submit()
    if (this.executionRequested && this.state.result?.status === 'prepared' && !this.state.recovery) await this.dispatchExecution()
  }
  cancel = async () => {
    if (!this.state.result || this.state.phase === 'expired' || !['prepared', 'running'].includes(this.state.result.status)) return
    this.active?.abort(); this.active = null; this.stopPolling()
    this.cancelRequested = true
    await this.submit()
  }
}
