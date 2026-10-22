import { requestLayoutScore } from './api'
import type { LayoutScoreReport, ScoreLayoutRequest } from './layout-scoring'
import type { RoomScene } from './scene.generated'

export interface AssessmentState {
  status: 'loading' | 'ready' | 'error'
  report: LayoutScoreReport | null
}
type Client = typeof requestLayoutScore

/** Assessment follows accepted scenes only; stale responses never label a new layout. */
export class LayoutAssessmentController {
  private state: AssessmentState = { status: 'loading', report: null }
  private listeners = new Set<() => void>()
  private pending: AbortController | null = null
  constructor(private client: Client = requestLayoutScore) {}
  getSnapshot = () => this.state
  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => { this.listeners.delete(listener) }
  }
  private update(state: AssessmentState) {
    this.state = state
    this.listeners.forEach(listener => listener())
  }
  cancel = () => { this.pending?.abort(); this.pending = null }
  load = async (scene: RoomScene, goals: ScoreLayoutRequest['goals']) => {
    this.cancel()
    const controller = new AbortController()
    this.pending = controller
    this.update({ status: 'loading', report: null })
    try {
      const report = await this.client(scene, goals, controller.signal)
      if (!controller.signal.aborted) this.update({ status: 'ready', report })
    } catch {
      if (!controller.signal.aborted) this.update({ status: 'error', report: null })
    }
  }
}
