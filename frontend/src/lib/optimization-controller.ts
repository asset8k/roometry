import { observeProgress, optimizationEvents } from './run-progress'
import type { ProgressObserver, RunProgress, StreamMode } from './run-progress'
import { AgentRunError } from './agent-run'
import type { AgentState } from './agent-run'
import { checkOptimization, improvementPreferences, optimizationClient } from './optimization'
import type { Focus, ImprovementDraft, OptimizationClient, OptimizationRun, StartOptimizationRequest } from './optimization'

export interface ImprovementState {
  focus: Focus | null
  instruction?: string | null
  draft: ImprovementDraft
  phase: 'idle' | 'editing' | 'preparing' | 'prepared' | 'running' | 'completed' | 'no_improvement' | 'failed' | 'budget_exhausted' | 'cancelling' | 'cancelled' | 'error' | 'expired'
  result: OptimizationRun | null
  error: string | null
  recovery: boolean
  progress?: RunProgress
  streamMode?: StreamMode
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
  private streamEpoch = 0
  private streamId: string | null = null
  private stopWatching: (() => void) | null = null
  private refreshQueued = false
  private source: AgentState
  private context: StartOptimizationRequest['context'] = null
  private instruction: string | null = null
  constructor(source: AgentState, private client: OptimizationClient = optimizationClient, private now = Date.now, private observe: ProgressObserver | undefined = client === optimizationClient ? observeProgress : undefined) { this.source = structuredClone(source) }
  getSnapshot = () => this.state
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener) } }
  private update(patch: Partial<ImprovementState>) { this.state = { ...this.state, ...patch }; this.listeners.forEach(listener => listener()) }
  available = () => ['completed', 'partial'].includes(this.source.status) && !!this.source.working_scene && Date.parse(this.source.expires_at) > this.now()
  locked = () => ['preparing', 'running', 'cancelling', 'prepared', 'expired'].includes(this.state.phase) || this.state.recovery
  select = (focus: Focus) => {
    if (this.locked()) return
    if (!this.available()) { this.update({ phase: 'expired', error: 'This reconstruction is no longer available for improvements.' }); return }
    this.stopPolling(); this.reading?.abort(); this.reading = null; this.active?.abort(); this.active = null
    this.stopStream()
    this.pending = null; this.executionRequested = false; this.cancelRequested = false
    this.context = null; this.instruction = null
    this.update({ focus, instruction: null, phase: 'editing', error: null, result: null, progress: undefined, streamMode: undefined })
  }
  change = (draft: Partial<ImprovementDraft>) => { if (!this.locked()) this.update({ draft: { ...this.state.draft, ...draft }, error: null }) }
  /** Capture once; retries reuse this exact snapshot, never a newer browser scene. */
  setContext = (context: NonNullable<StartOptimizationRequest['context']>) => { if (!this.locked()) this.context = structuredClone(context) }
  refine = async (instruction: string, context: NonNullable<StartOptimizationRequest['context']>) => {
    if (this.locked()) return
    if (!instruction.trim() || instruction.trim().length > 1000) { this.update({ error: 'Write a request of up to 1,000 characters.' }); return }
    this.select('aesthetics')
    if (!this.available()) return
    this.context = structuredClone(context); this.instruction = instruction.trim()
    this.update({ instruction: this.instruction, draft: { budget: '', currency: 'USD', style: '', constraints: '' } })
    await this.start()
  }
  close = () => { if (!this.locked()) this.update({ focus: null, phase: 'idle', error: null }) }
  connect = () => {
    this.connected = true
    this.schedule()
    const timer = setTimeout(() => { this.stopStream(); this.stopPolling(); this.reading?.abort(); this.reading = null; this.active?.abort(); this.active = null; this.update({ phase: 'expired', recovery: false, error: 'This temporary reconstruction has expired. Generate again to request improvements.' }) }, Math.max(0, Date.parse(this.source.expires_at) - this.now()))
    return () => {
      clearTimeout(timer); this.connected = false; this.stopStream(); this.stopPolling(); this.reading?.abort(); this.reading = null
      this.active?.abort(); this.active = null
      if (this.source.persistence !== 'account' && this.state.result && (this.state.result.status === 'running' || (this.executionRequested && this.state.result.status === 'prepared'))) {
        void this.client.cancel(this.source.run_id, this.state.result.run_id, AbortSignal.timeout(10000)).catch(() => {})
      }
    }
  }
  prepare = async () => {
    if (this.locked() || !this.state.focus) return
    if (!this.available()) { this.update({ phase: 'expired', error: 'This temporary reconstruction has expired.' }); return }
    try {
      this.pending = { request_id: crypto.randomUUID(), focus: this.state.focus, preferences: improvementPreferences(this.state.draft),
        ...(this.context ? { context: structuredClone(this.context) } : {}), ...(this.instruction ? { instruction: this.instruction } : {}) }
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
  private stopStream() { this.streamEpoch++; this.streamId = null; this.stopWatching?.(); this.stopWatching = null }
  private ensureStream() {
    const run = this.state.result
    const wanted = this.connected && this.state.phase !== 'expired' && run && (run.status === 'running' || (run.status === 'prepared' && this.executionRequested) || this.cancelRequested)
    if (!wanted) { this.stopStream(); return }
    if (!this.observe || this.streamId === run.run_id) return
    this.stopStream()
    const id = this.streamId = run.run_id, epoch = this.streamEpoch
    const current = () => this.streamEpoch === epoch && this.connected && this.streamId === id && this.state.result?.run_id === id
    const stop = this.observe(optimizationEvents(this.source.run_id, id), id, 'optimization', {
      event: event => { if (current()) { this.update({ progress: event }); this.refreshQueued = true; void this.poll() } },
      mode: mode => { if (current() && mode !== this.state.streamMode) { this.update({ streamMode: mode }); this.schedule() } },
      unavailable: () => { if (current()) { this.stopStream(); this.refreshQueued = true; void this.poll() } },
    })
    if (current()) this.stopWatching = stop
    else stop()
  }
  private schedule() {
    this.stopPolling()
    this.ensureStream()
    if (this.connected && this.state.streamMode !== 'streaming' && this.state.result && (this.state.result.status === 'running' || this.state.phase === 'cancelling')) this.pollTimer = setTimeout(() => { void this.poll() }, 1200)
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
    if (this.state.result?.status === 'prepared' && !this.state.recovery && !this.cancelRequested) await this.execute()
  }
  execute = async () => {
    const result = this.state.result
    if (!result || this.active || this.cancelRequested || result.status !== 'prepared') return
    this.executionRequested = true
    await this.dispatchExecution()
  }
  private async dispatchExecution() {
    if (!this.state.result || this.active || this.cancelRequested) return
    const abort = new AbortController(); this.active = abort
    this.update({ phase: 'running', recovery: false, error: null })
    // A read can display progress while the execute request awaits completion.
    this.schedule()
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
    this.refreshQueued = false
    try {
      const result = await this.client.read(this.source.run_id, this.state.result.run_id, abort.signal)
      if (!abort.signal.aborted) this.accept(result)
    } catch (error) { if (!abort.signal.aborted) this.executionFailure(error) }
    finally {
      if (this.reading === abort) this.reading = null
      if (this.refreshQueued && this.connected) { this.refreshQueued = false; void this.poll() }
    }
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
  leaveWorkspace = async () => {
    const activeResult = this.state.result && ['prepared', 'running'].includes(this.state.result.status)
    if (!this.pending || (!activeResult && !['preparing', 'prepared', 'running', 'cancelling'].includes(this.state.phase) && !this.state.recovery)) return
    // If preparation lost its response, recover the same free request before
    // cancelling it. Never execute/retry a paid candidate just to leave a page.
    this.active?.abort(); this.active = null
    this.executionRequested = false
    this.cancelRequested = true
    await this.submit()
    if (this.state.recovery || this.state.phase === 'preparing' || this.state.phase === 'error') {
      throw new Error('Couldn’t confirm the improvement stopped. Retry leaving, or keep this room and check its status.')
    }
    if (this.state.result?.status === 'prepared') await this.submit()
    const latest = this.getSnapshot()
    if (latest.recovery || latest.phase === 'error' || latest.result?.status === 'prepared') throw new Error('Couldn’t confirm cancellation. Your workspace is kept.')
  }
}
