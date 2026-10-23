import { agentClient } from './agent-api'
import type { AgentClient } from './agent-api'
import { activeRun, AgentRunError, checkRunInput, newerRun } from './agent-run'
import type { AgentState, ClarificationAnswer, StartRunRequest } from './agent-run'
import type { PreparedRoomInput } from './room-input-response.generated'
import type { RoomInputController } from './room-input-controller'

export interface GenerationState {
  phase: 'idle' | 'uploading' | 'starting' | 'running' | 'awaiting_answer' | 'completed' | 'partial' | 'incomplete' | 'failed' | 'budget_exhausted' | 'cancelled' | 'expired'
  run: AgentState | null
  cancelling: boolean
  recovery: boolean
  answering: boolean
  connection?: boolean
  notice: { text: string; kind: 'info' | 'error'; connected?: boolean } | null
}

export function generationProgress(state: GenerationState): string {
  if (state.cancelling) return 'Stopping your generation…'
  if (state.phase === 'uploading') return 'Preparing your photos…'
  if (state.recovery) return 'Connection interrupted. Your generation may still be running.'
  if (state.phase === 'starting') return 'Starting your room reconstruction…'
  if (state.phase === 'awaiting_answer') return 'A little more context will help.'
  switch (state.run?.pending_tool?.name) {
    case 'analyze_photos': return 'Analyzing your room photos together…'
    case 'propose_scene': return 'Building your room and checking its geometry…'
    case 'request_clarification': return 'Preparing a question about your room…'
    case 'move_object': case 'rotate_object': return 'Checking a placement adjustment…'
    case 'search_furniture': case 'get_furniture_details': case 'list_furniture_categories': return 'Checking furniture models and dimensions…'
    case 'validate_scene': return 'Checking the reconstructed room…'
    default: return 'Working on your room reconstruction…'
  }
}

/** Transient client state only. Reconnects reuse the exact start ID/answer. */
export class RoomGenerationController {
  private state: GenerationState = { phase: 'idle', run: null, cancelling: false, recovery: false, answering: false, notice: null }
  private listeners = new Set<() => void>()
  private revision = 0
  private input: PreparedRoomInput | null = null
  private startRequest: StartRunRequest | null = null
  private pendingAnswer: ClarificationAnswer | null = null
  private requests = new Set<AbortController>()
  private mutation: AbortController | null = null
  private timer: ReturnType<typeof setTimeout> | null = null
  private reading: AbortController | null = null
  private cancelPending = false
  private connected = false
  constructor(private inputs: RoomInputController, private client: AgentClient = agentClient, private now = Date.now, private pollMs = 1200) {}
  getSnapshot = () => this.state
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener) } }
  locked = () => this.state.recovery || this.state.cancelling || ['uploading', 'starting', 'running', 'awaiting_answer'].includes(this.state.phase)
  private update(patch: Partial<GenerationState>) { this.state = { ...this.state, ...patch }; this.listeners.forEach(listener => listener()) }
  private stopTimer() { if (this.timer) clearTimeout(this.timer); this.timer = null }
  private schedule() {
    this.stopTimer()
    if (this.connected && this.startRequest && (this.pendingAnswer || this.state.cancelling || ['starting', 'running'].includes(this.state.phase))) {
      this.timer = setTimeout(() => { void this.poll() }, this.pollMs)
    }
  }
  private accept(run: AgentState, revision: number) {
    if (revision !== this.revision || !this.input) return
    checkRunInput(run, this.input, this.state.run?.run_id)
    if (!newerRun(run, this.state.run)) return
    if (this.pendingAnswer && run.submitted_answers?.[this.pendingAnswer.question_id]) this.pendingAnswer = null
    const expired = Date.parse(run.expires_at) <= this.now()
    if (expired || (!activeRun(run) && run.status !== 'awaiting_answer')) this.pendingAnswer = null
    const waitingRetry = Boolean(this.pendingAnswer) && run.status === 'awaiting_answer' && this.state.recovery
    const previous = this.state.run
    const stableRun = previous && JSON.stringify(run) === JSON.stringify(previous) ? previous : {
      ...run,
      working_scene: previous && JSON.stringify(run.working_scene) === JSON.stringify(previous.working_scene) ? previous.working_scene : run.working_scene,
      base_scene: previous && JSON.stringify(run.base_scene) === JSON.stringify(previous.base_scene) ? previous.base_scene : run.base_scene,
    }
    this.update({ run: stableRun, phase: expired ? 'expired' : run.status === 'queued' ? 'starting' : run.status,
      recovery: waitingRetry, answering: Boolean(this.pendingAnswer),
      cancelling: this.state.cancelling && !expired && (activeRun(run) || run.status === 'awaiting_answer'), notice: waitingRetry ? this.state.notice : null, connection: true })
    this.schedule()
  }
  private failure(error: unknown) {
    if (error instanceof AgentRunError && error.status === 404 && this.state.run) {
      this.pendingAnswer = null
      this.stopTimer()
      this.update({ phase: 'expired', cancelling: false, answering: false, recovery: false, connection: true,
        notice: { kind: 'info', text: 'This temporary generation is no longer available. Your selected photos are kept.', connected: true } })
      return
    }
    this.update({ recovery: true, connection: error instanceof AgentRunError, notice: { kind: 'error',
      text: error instanceof AgentRunError ? error.message : 'The connection was interrupted. Check status to recover this generation.',
      connected: error instanceof AgentRunError ? true : false } })
  }
  private sync = () => {
    const state = this.inputs.getSnapshot()
    if (this.input && this.state.phase !== 'uploading' && (state.dirty || state.uncertain || state.accepted?.input_id !== this.input.input_id)) {
      this.releaseRun()
      this.input = null; this.startRequest = null; this.pendingAnswer = null
      this.update({ run: null, phase: 'idle', cancelling: false, answering: false, recovery: false, notice: null })
    }
  }
  connect = () => {
    this.connected = true
    const unsubscribe = this.inputs.subscribe(this.sync)
    this.schedule()
    return () => { unsubscribe(); this.connected = false; this.releaseRun() }
  }
  private async mutate(operation: (signal: AbortSignal) => Promise<AgentState>) {
    const revision = this.revision, controller = new AbortController()
    this.mutation = controller; this.requests.add(controller)
    try {
      this.accept(await operation(controller.signal), revision)
      if (revision === this.revision && this.state.cancelling && this.state.run) await this.sendCancel(revision)
    } catch (error) {
      if (revision !== this.revision || controller.signal.aborted || this.mutation !== controller) return
      if (this.state.run && !activeRun(this.state.run) && !this.pendingAnswer) return
      this.failure(error)
    } finally {
      this.requests.delete(controller)
      if (this.mutation === controller) this.mutation = null
    }
  }
  generate = async () => {
    if (this.locked() || this.inputs.getSnapshot().busy) return
    const before = this.inputs.getSnapshot()
    if (['completed', 'partial'].includes(this.state.phase) && !before.dirty && !before.uncertain && before.accepted) return
    const revision = ++this.revision
    this.stopTimer()
    this.update({ phase: 'uploading', run: null, recovery: false, notice: null, answering: false, cancelling: false })
    this.inputs.checkExpiry()
    const current = this.inputs.getSnapshot()
    if (!current.accepted || current.dirty || current.uncertain) {
      if (!await this.inputs.submit()) { if (revision === this.revision) this.update({ phase: 'idle' }); return }
    }
    if (revision !== this.revision) return
    this.input = this.inputs.getSnapshot().accepted
    if (!this.input) { this.update({ phase: 'idle' }); return }
    this.startRequest = { input_id: this.input.input_id, request_id: crypto.randomUUID() }
    this.pendingAnswer = null
    this.update({ phase: 'starting' }); this.schedule()
    await this.mutate(signal => this.client.start(this.startRequest!, signal))
  }
  poll = async () => {
    if (this.reading || !this.startRequest || !this.input) return
    const revision = this.revision, controller = new AbortController()
    this.reading = controller
    this.requests.add(controller)
    try {
      const run = this.state.run ? await this.client.read(this.state.run.run_id, controller.signal) : await this.client.find(this.startRequest.request_id, controller.signal)
      this.accept(run, revision)
      if (revision === this.revision && this.state.cancelling) await this.sendCancel(revision)
    } catch (error) {
      if (revision !== this.revision || controller.signal.aborted) return
      if (error instanceof AgentRunError && error.status === 404 && !this.state.run && this.mutation) this.schedule()
      else { this.stopTimer(); this.failure(error) }
    } finally { this.requests.delete(controller); if (this.reading === controller) this.reading = null }
  }
  answer = async (answer: ClarificationAnswer) => {
    if (!this.state.run || this.state.phase !== 'awaiting_answer' || this.state.answering || this.state.cancelling || this.state.recovery) return
    this.pendingAnswer = structuredClone(answer)
    this.update({ answering: true, notice: null })
    this.schedule()
    await this.submitAnswer()
  }
  private async submitAnswer() {
    if (!this.state.run || !this.pendingAnswer) return
    const answer = this.pendingAnswer, runId = this.state.run.run_id
    const revision = this.revision
    let rejection: string | null = null
    this.update({ answering: true, recovery: false, notice: null }); this.schedule()
    await this.mutate(async signal => {
      try { return await this.client.answer(runId, answer, signal) }
      catch (error) {
        if (revision === this.revision && error instanceof AgentRunError && error.status === 422) {
          this.pendingAnswer = null
          rejection = error.message
          return this.state.run!
        }
        throw error
      }
    })
    if (revision === this.revision && rejection) {
      this.update({ answering: false, recovery: false, notice: { kind: 'error', text: rejection, connected: true } })
    }
  }
  retry = async () => {
    if (!this.startRequest) return
    if (this.state.cancelling) { await this.poll(); return }
    if (this.pendingAnswer) { await this.submitAnswer(); return }
    if (this.state.run) { await this.poll(); return }
    this.update({ recovery: false, phase: 'starting', notice: null }); this.schedule()
    const request = this.startRequest
    await this.mutate(signal => this.client.start(request, signal))
  }
  private async sendCancel(revision: number) {
    const runId = this.state.run?.run_id
    if (!runId || !this.state.cancelling || this.cancelPending) return
    this.cancelPending = true
    const controller = new AbortController(); this.requests.add(controller)
    try { this.accept(await this.client.cancel(runId, controller.signal), revision) }
    catch (error) { if (revision === this.revision && !controller.signal.aborted) this.failure(error) }
    finally { this.requests.delete(controller); this.cancelPending = false }
  }
  cancel = async () => {
    if (this.state.phase === 'uploading') {
      this.revision++; this.inputs.cancel(); this.update({ phase: 'cancelled' }); return
    }
    if (!this.startRequest || (!this.locked() && !this.pendingAnswer)) return
    this.update({ cancelling: true, notice: null })
    if (this.state.run) await this.sendCancel(this.revision)
    else await this.poll()
    this.schedule()
  }
  discard = async () => {
    this.releaseRun()
    if (this.inputs.getSnapshot().busy) this.inputs.cancel()
    if (await this.inputs.discard()) {
      this.input = null; this.startRequest = null; this.pendingAnswer = null
      this.update({ run: null, phase: 'idle', cancelling: false, recovery: false, answering: false, notice: null })
    }
  }
  private releaseRun() {
    const request = this.startRequest, run = this.state.run
    const shouldCancel = request && this.locked()
    this.revision++; this.stopTimer()
    this.reading = null; this.cancelPending = false
    this.requests.forEach(controller => controller.abort()); this.requests.clear(); this.mutation = null
    if (shouldCancel) {
      const signal = AbortSignal.timeout(10000)
      void (run ? Promise.resolve(run) : this.client.find(request.request_id, signal))
        .then(result => activeRun(result) || result.status === 'awaiting_answer' ? this.client.cancel(result.run_id, signal) : undefined).catch(() => {})
    }
  }
}
