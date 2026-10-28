import { canonicalJson } from './canonical-json'
import type { OpenedProject, SaveSnapshot } from './projects'
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
  hasEditableCopy: boolean
  colors: Readonly<Record<string, string>>
}

export function retainedImprovement(run: OptimizationRun | null): run is OptimizationRun & { result_scene: RoomScene } {
  return !!run?.result_scene && !['prepared', 'running', 'no_improvement'].includes(run.status)
    && run.attempts.some(attempt => attempt.outcome === 'accepted')
    && canonicalJson(run.source_scene) !== canonicalJson(run.result_scene)
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
    editor.switchView('before')
    this.state = { view: 'original', hasEditableCopy: false, editor, editingId: 'original', selectedImprovementId: null, improvements: [], colors: {} }
  }
  static restore(saved: OpenedProject, client?: EditClient): SceneComparison {
    const result = new SceneComparison(saved.run.working_scene!, client)
    const snapshot = saved.project.snapshot
    // JSONB object keys are unordered. Restore the actual run chronology, with
    // the saved manifest as a stable tie-breaker for identical timestamps.
    const order = new Map(snapshot.versions.map((version, index) => [version.key, index]))
    const improvements = Object.values(snapshot.improvements).sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at)
      || (order.get(a.run_id) ?? 0) - (order.get(b.run_id) ?? 0))
    for (const improvement of improvements) result.offer(improvement, improvement.source_scene)
    const versions = new Map(saved.project.versions.map(version => [version.id, version.scene]))
    let legacyEdits = false
    for (const version of snapshot.versions.filter(item => item.key.startsWith('manual:'))) {
      const copy = result.copies.get(version.parent_key!)
      if (copy) {
        const scene = versions.get(version.version_id)!, baseline = copy.editor.savedScene()
        legacyEdits ||= canonicalJson(scene) !== canonicalJson(baseline)
        copy.colors = Object.fromEntries(scene.objects.filter(object => baseline.objects.find(item => item.id === object.id)?.color !== object.color).map(object => [object.id, object.color]))
        copy.editor.restoreAccepted(scene)
      }
    }
    const editingId = snapshot.comparison.editing_parent_run_id ?? 'original'
    const hasEditableCopy = !!snapshot.comparison.has_editable_copy || snapshot.comparison.view === 'editing'
      || legacyEdits
    result.update({ editingId, hasEditableCopy, selectedImprovementId: snapshot.comparison.selected_improvement_id })
    const view = snapshot.comparison.view
    const active = view === 'original' ? 'original' : view === 'editing' ? editingId : snapshot.comparison.selected_improvement_id!
    result.activate(view, active)
    result.update({ colors: result.copies.get(active)!.colors })
    return result
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
  offer = (run: OptimizationRun | null, requestScene?: RoomScene) => {
    if (!retainedImprovement(run) || this.copies.has(run.run_id)
      || canonicalJson(run.source_scene) !== canonicalJson(requestScene ?? this.original)) return false
    const saved = structuredClone(run)
    this.copies.set(saved.run_id, { editor: new SceneEditor(saved.result_scene!, this.client, saved.result_scene!), colors: {} })
    const editing = this.state.editor.getSnapshot()
    // Do not interrupt a drag or an in-flight manual edit when a result arrives.
    // Do not replace newer manual edits made while a candidate was being checked.
    const current = this.currentContext().scene
    const activate = !editing.busy && !editing.dragging && (!requestScene || canonicalJson(current) === canonicalJson(requestScene))
    this.update({ improvements: [...this.state.improvements, saved],
      ...(activate || this.state.view !== 'improved' ? { selectedImprovementId: saved.run_id } : {}) })
    if (activate) this.activate('improved', saved.run_id)
    return true
  }
  currentContext = () => {
    const state = this.state, editor = state.editor.getSnapshot()
    const raw = editor.scene
    const colors = state.view === 'editing' ? state.colors : {}
    const scene = { ...raw, objects: raw.objects.map(object => object.locked || !colors[object.id] ? object : { ...object, color: colors[object.id].toUpperCase() }) }
    const parent = state.view === 'original' ? 'original' : state.view === 'improved' ? state.selectedImprovementId : state.editingId
    return { scene: structuredClone(scene), parent_run_id: parent === 'original' ? null : parent }
  }
  show = (view: ComparisonView) => {
    if (view === 'editing' && !this.state.hasEditableCopy) return
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
    this.update({ editingId: id, hasEditableCopy: true })
    this.activate('editing', id)
  }
  setColor = (id: string, color: string) => {
    if (this.state.view !== 'editing' || this.state.colors[id] === color) return
    const copy = this.copies.get(this.state.editingId)!
    copy.colors = { ...copy.colors, [id]: color }
    this.update({ colors: copy.colors })
  }
  toggleLock = (id: string) => this.state.editor.toggleLock(id, this.state.colors[id])
  resetColors = () => {
    if (this.state.view !== 'editing') return
    this.copies.get(this.state.editingId)!.colors = {}
    this.update({ colors: {} })
  }
  snapshotForSave = (): SaveSnapshot => {
    const manual_copies = [...this.copies].map(([id, copy]) => {
      const status = copy.editor.getSnapshot()
      if (status.busy || status.dragging) throw new Error('Finish the current edit before saving.')
      const scene = copy.editor.savedScene()
      scene.objects = scene.objects.map(object => object.locked || !copy.colors[object.id] ? object : { ...object, color: copy.colors[object.id].toUpperCase() })
      return { parent_run_id: id === 'original' ? null : id, scene }
    })
    return { manual_copies, has_editable_copy: this.state.hasEditableCopy, improvement_run_ids: this.state.improvements.map(run => run.run_id), view: this.state.view,
      editing_parent_run_id: this.state.editingId === 'original' ? null : this.state.editingId,
      selected_improvement_id: this.state.selectedImprovementId }
  }
  cancelEdits = () => { for (const copy of this.copies.values()) copy.editor.cancel(false) }
}
