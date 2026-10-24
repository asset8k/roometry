import { SceneEditor } from './scene-editor'
import type { RoomScene } from './scene.generated'
import type { OptimizationRun } from './optimization'

export type ComparisonView = 'original' | 'improved' | 'editing'
type EditClient = ConstructorParameters<typeof SceneEditor>[1]
export interface ComparisonState {
  view: ComparisonView
  editor: SceneEditor
  editingId: string
  selectedImprovementId: string | null
  improvements: readonly OptimizationRun[]
  colors: Readonly<Record<string, string>>
}

export function retainedImprovement(run: OptimizationRun | null): run is OptimizationRun & { result_scene: RoomScene } {
  return !!run?.result_scene && !['prepared', 'running', 'no_improvement'].includes(run.status)
    && run.attempts.some(attempt => attempt.outcome === 'accepted')
    && JSON.stringify(run.source_scene) !== JSON.stringify(run.result_scene)
}

/** Saved scenes are read-only; every original/candidate has its own manual copy. */
export class SceneComparison {
  private state: ComparisonState
  private listeners = new Set<() => void>()
  private original: RoomScene
  private copies = new Map<string, { editor: SceneEditor; colors: Record<string, string> }>()
  constructor(scene: RoomScene, private client?: EditClient) {
    this.original = structuredClone(scene)
    const editor = new SceneEditor(scene, client, scene)
    this.copies.set('original', { editor, colors: {} })
    this.state = { view: 'editing', editor, editingId: 'original', selectedImprovementId: null, improvements: [], colors: {} }
  }
  getSnapshot = () => this.state
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener) } }
  private update(patch: Partial<ComparisonState>) { this.state = { ...this.state, ...patch }; this.listeners.forEach(listener => listener()) }
  private activate(view: ComparisonView, id: string) {
    const copy = this.copies.get(id)
    if (!copy) return
    if (view === this.state.view && copy.editor === this.state.editor) return
    this.state.editor.cancel(false)
    copy.editor.switchView(view === 'editing' ? 'after' : 'before')
    this.update({ view, editor: copy.editor, colors: copy.colors })
  }
  offer = (run: OptimizationRun | null) => {
    if (!retainedImprovement(run) || this.copies.has(run.run_id)
      || JSON.stringify(run.source_scene) !== JSON.stringify(this.original)) return false
    const saved = structuredClone(run)
    this.copies.set(saved.run_id, { editor: new SceneEditor(saved.result_scene!, this.client, saved.result_scene!), colors: {} })
    const editing = this.state.editor.getSnapshot()
    this.update({ improvements: [...this.state.improvements, saved], selectedImprovementId: saved.run_id })
    // Do not interrupt a drag or an in-flight manual edit when a result arrives.
    if (!editing.busy && !editing.dragging) this.activate('improved', saved.run_id)
    return true
  }
  show = (view: ComparisonView) => {
    const id = view === 'original' ? 'original' : view === 'editing' ? this.state.editingId : this.state.selectedImprovementId
    if (id) this.activate(view, id)
  }
  selectImprovement = (id: string) => {
    if (!this.state.improvements.some(run => run.run_id === id)) return
    this.update({ selectedImprovementId: id })
    this.activate('improved', id)
  }
  editCopy = () => {
    if (this.state.view === 'editing') return
    const id = this.state.view === 'improved' ? this.state.selectedImprovementId : 'original'
    if (!id) return
    this.update({ editingId: id })
    this.activate('editing', id)
  }
  setColor = (id: string, color: string) => {
    if (this.state.view !== 'editing') return
    const copy = this.copies.get(this.state.editingId)!
    copy.colors = { ...copy.colors, [id]: color }
    this.update({ colors: copy.colors })
  }
  resetColors = () => {
    if (this.state.view !== 'editing') return
    this.copies.get(this.state.editingId)!.colors = {}
    this.update({ colors: {} })
  }
  cancelEdits = () => { for (const copy of this.copies.values()) copy.editor.cancel(false) }
}
