import type { RoomAnalysisController } from './room-analysis-controller'
import type { RoomInputController } from './room-input-controller'

/** One user action prepares the current photos and analyzes the owned upload. */
export class RoomWorkflowController {
  private revision = 0
  private running = false
  constructor(private inputs: RoomInputController, private analysis: RoomAnalysisController) {}

  private sync = () => {
    const state = this.inputs.getSnapshot()
    this.analysis.setInput(state.accepted, !state.dirty && !state.uncertain && !state.busy)
  }
  connect = () => {
    this.sync()
    const unsubscribe = this.inputs.subscribe(this.sync)
    return () => { unsubscribe(); this.cancel(); this.analysis.release() }
  }
  analyze = async () => {
    if (this.running || this.inputs.getSnapshot().busy || this.analysis.getSnapshot().busy) return
    const revision = ++this.revision
    this.running = true
    try {
      this.inputs.checkExpiry()
      this.sync()
      const state = this.inputs.getSnapshot()
      // Retry a failed analysis against the same valid upload. Changed,
      // ambiguous, expired, or revoked inputs must be prepared again first.
      if (!state.accepted || state.dirty || state.uncertain || this.analysis.getSnapshot().input !== state.accepted) {
        if (!await this.inputs.submit()) return
      }
      if (revision !== this.revision) return
      this.sync()
      await this.analysis.analyze()
    } finally {
      if (revision === this.revision) this.running = false
    }
  }
  cancel = () => {
    this.revision++
    this.running = false
    if (this.inputs.getSnapshot().busy) this.inputs.cancel()
    if (this.analysis.getSnapshot().busy) this.analysis.cancel()
  }
}
