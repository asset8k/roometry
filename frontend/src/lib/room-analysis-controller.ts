import { analyzeRoom, proposeRoomScene } from './api'
import { dimensionAxes, dimensionLabels, RoomAnalysisError } from './room-analysis'
import type { DimensionAxis } from './room-analysis'
import type { PreparedRoomInput } from './room-input-response.generated'
import type { RoomAnalysisResponse } from './room-analysis-response.generated'
import type { SceneProposalRequest } from './scene-proposal-request.generated'
import type { SceneProposalResponse } from './scene-proposal-response.generated'
import type { RoomScene } from './scene.generated'

export interface AnalysisNotice { kind: 'info' | 'error'; text: string; connected?: boolean }
export interface AnalysisState {
  input: PreparedRoomInput | null; analysis: RoomAnalysisResponse | null
  proposal: SceneProposalResponse | null; scene: RoomScene | null
  busy: 'analysis' | 'proposal' | null; notice: AnalysisNotice | null
  measurements: Record<DimensionAxis, string>; enteredAxes: DimensionAxis[]
}
interface AnalysisClient { analyze: typeof analyzeRoom; propose: typeof proposeRoomScene }
const emptyMeasurements = (): AnalysisState['measurements'] => ({ width_m: '', length_m: '', height_m: '' })
const initialState = (): AnalysisState => ({ input: null, analysis: null, proposal: null, scene: null, busy: null,
  notice: null, measurements: emptyMeasurements(), enteredAxes: [] })

/** Temporary UI state; abort/revision guards prevent stale requests replacing a newer room. */
export class RoomAnalysisController {
  private state = initialState()
  private listeners = new Set<() => void>()
  private pending: AbortController | null = null
  private revision = 0
  private revokedInputId: string | null = null
  constructor(private client: AnalysisClient = { analyze: analyzeRoom, propose: proposeRoomScene }, private now = Date.now) {}
  getSnapshot = () => this.state
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener) } }
  private update(patch: Partial<AnalysisState>) {
    this.state = { ...this.state, ...patch }
    this.listeners.forEach(listener => listener())
  }
  setInput = (input: PreparedRoomInput | null, available = true) => {
    const next = available && input && input.input_id !== this.revokedInputId && Date.parse(input.expires_at) > this.now() ? input : null
    if (next === this.state.input) return
    this.revision++
    this.pending?.abort()
    this.pending = null
    this.update({ ...initialState(), input: next })
  }
  private currentInput(): PreparedRoomInput | null {
    const input = this.state.input
    if (input && Date.parse(input.expires_at) <= this.now()) {
      this.setInput(null)
      this.update({ notice: { kind: 'info', text: 'Your room input has expired. Upload your selected photos again to continue.' } })
      return null
    }
    return input
  }
  changeMeasurements = (patch: Partial<AnalysisState['measurements']>) => {
    if (this.state.busy) return
    const enteredAxes = dimensionAxes.filter(axis => patch[axis] !== undefined
      ? Boolean(patch[axis]?.trim()) : this.state.enteredAxes.includes(axis))
    this.update({ measurements: { ...this.state.measurements, ...patch }, enteredAxes, notice: null })
  }
  cancel = () => {
    this.revision++
    this.pending?.abort()
    this.pending = null
    if (this.state.busy) this.update({ busy: null, notice: { kind: 'info', text: 'Stopped waiting. The service may still finish this attempt. Retrying reuses a completed result when available.' } })
  }
  private failure(error: unknown) {
    if (error instanceof RoomAnalysisError && error.status === 404) {
      this.revokedInputId = this.state.input?.input_id ?? null
      this.setInput(null)
      this.update({ notice: { kind: 'error', text: 'These temporary room inputs are no longer available. Upload the photos again.', connected: true } })
      return
    }
    this.update({ busy: null, notice: { kind: 'error', text: error instanceof RoomAnalysisError ? error.message
      : 'Couldn’t complete the request. Your inputs are kept. Check the connection and try again; the service may still finish this attempt.',
    connected: error instanceof RoomAnalysisError ? error.connected : false } })
  }
  analyze = async () => {
    const input = this.currentInput()
    if (!input || this.state.busy || this.state.analysis) return
    const revision = ++this.revision, controller = new AbortController()
    this.pending = controller
    this.update({ busy: 'analysis', notice: null })
    try {
      const analysis = await this.client.analyze(input.input_id, controller.signal)
      if (revision !== this.revision || controller.signal.aborted) return
      if (!this.currentInput()) return
      if (analysis.input_id !== input.input_id || Date.parse(analysis.expires_at) !== Date.parse(input.expires_at)
        || JSON.stringify(analysis.observations.image_ids) !== JSON.stringify(input.images.map(image => image.image_id))) {
        throw new RoomAnalysisError('The analysis does not match your current photos. No scene was opened.')
      }
      const dimensions = analysis.input_options.dimensions
      const measurements = emptyMeasurements()
      for (const axis of dimensionAxes) measurements[axis] = String(dimensions?.[axis] ?? analysis.observations.estimated_dimensions?.[axis] ?? '')
      this.update({ analysis, measurements, busy: null, notice: { kind: 'info', text: 'Your photos have been analyzed. Review the observations and check the room dimensions.', connected: true },
        enteredAxes: dimensions?.measurements_source === 'user_confirmed' ? dimensionAxes.filter(axis => dimensions[axis] != null) : [] })
    } catch (error) { if (revision === this.revision && !controller.signal.aborted) this.failure(error) }
    finally { if (revision === this.revision) this.pending = null }
  }
  build = async () => {
    const input = this.currentInput(), analysis = this.state.analysis
    if (!input || !analysis || this.state.busy) return
    let request: SceneProposalRequest
    try {
      request = { analysis_id: analysis.analysis_id, allow_estimated_dimensions: true }
      const values: Partial<Record<DimensionAxis, number>> = {}
      for (const axis of dimensionAxes) {
        const text = this.state.measurements[axis].trim()
        if (!text) continue
        const value = Number(text), max = axis === 'height_m' ? 10 : 30
        if (!/^(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(text) || !Number.isFinite(value) || value <= 0 || value > max) {
          throw new RoomAnalysisError(`Enter ${dimensionLabels[axis].toLowerCase()} greater than 0 and no more than ${max} meters.`, 'invalid_measurements')
        }
        if (this.state.enteredAxes.includes(axis)) values[axis] = value
      }
      // Submitting typed values is the user action that establishes their
      // source. Untouched estimates stay on the server and retain that label.
      if (Object.keys(values).length) request.dimensions = { ...values, measurements_source: 'user_confirmed' }
    } catch (error) { this.failure(error); return }
    const revision = ++this.revision, controller = new AbortController()
    this.pending = controller
    this.update({ busy: 'proposal', notice: null })
    try {
      const proposal = await this.client.propose(request, controller.signal)
      if (revision !== this.revision || controller.signal.aborted) return
      if (!this.currentInput()) return
      if (proposal.input_id !== input.input_id || proposal.analysis_id !== analysis.analysis_id
        || Date.parse(proposal.expires_at) !== Date.parse(input.expires_at)) throw new RoomAnalysisError('The scene proposal does not match your current room.')
      const measurements = emptyMeasurements()
      for (const axis of dimensionAxes) measurements[axis] = String(proposal.dimensions[axis] ?? '')
      const estimatedAxes: ReadonlySet<string> = new Set(proposal.estimated_axes)
      const enteredAxes = dimensionAxes.filter(axis => proposal.dimensions[axis] != null && !estimatedAxes.has(axis))
      this.update({ proposal, measurements, enteredAxes, busy: null, scene: proposal.status === 'ready' ? proposal.scene : this.state.scene,
        notice: proposal.status === 'incomplete' && this.state.scene
          ? { kind: 'info', text: 'The new proposal could not be validated. The viewer still shows your last validated base scene.', connected: true }
          : { kind: 'info', text: proposal.status === 'ready' ? 'Your approximate base scene is ready.' : 'Your observations are saved while you review the room details.', connected: true } })
    } catch (error) { if (revision === this.revision && !controller.signal.aborted) this.failure(error) }
    finally { if (revision === this.revision) this.pending = null }
  }
  release = () => { this.setInput(null) }
}
