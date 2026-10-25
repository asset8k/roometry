import { requestEditPreview } from './api'
import { describeRejection, editRestriction, previewTransform, validEditValues } from './scene-edits'
import type { EditMode, SceneEdit } from './scene-edits'
import type { PreviewEditResponse } from './edit-response.generated'
import type { RoomScene } from './scene.generated'

export interface EditorNotice {
  kind: 'checking' | 'success' | 'rejected' | 'error' | 'info'
  text: string
  details?: string[]
  connection?: boolean
}

export interface EditorState {
  view: 'before' | 'after'
  scene: RoomScene
  draft: RoomScene | null
  selectedId: string | null
  mode: EditMode
  dragging: boolean
  busy: boolean
  notice: EditorNotice | null
}

type PreviewClient = (scene: RoomScene, edit: SceneEdit, signal: AbortSignal) => Promise<PreviewEditResponse>

/** Own the accepted scene separately from temporary drag/request candidates. */
export class SceneEditor {
  private state: EditorState
  private listeners = new Set<() => void>()
  private revision = 0
  private pending: AbortController | null = null
  private beforeScene: RoomScene | null
  private afterBaseline: RoomScene
  private afterScene: RoomScene

  constructor(scene: RoomScene, private client: PreviewClient = requestEditPreview, beforeScene?: RoomScene) {
    this.beforeScene = beforeScene ? structuredClone(beforeScene) : null
    this.afterBaseline = structuredClone(scene)
    this.afterScene = structuredClone(scene)
    this.state = {
      view: 'after', scene: this.afterScene, draft: null, selectedId: null,
      mode: 'move', dragging: false, busy: false, notice: null,
    }
  }

  getSnapshot = () => this.state
  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => { this.listeners.delete(listener) }
  }
  private update(patch: Partial<EditorState>) {
    this.state = { ...this.state, ...patch }
    this.listeners.forEach(listener => listener())
  }

  switchView = (view: EditorState['view']) => {
    const scene = view === 'before' ? this.beforeScene : this.afterScene
    if (view === this.state.view || !scene) return
    this.cancel(false)
    this.update({
      view, scene,
      selectedId: null, draft: null, notice: null,
    })
  }

  /** Reset only After geometry; independent color previews belong to the workspace. */
  resetLayout = () => {
    if (this.state.view === 'before') return
    this.cancel(false)
    this.afterScene = structuredClone(this.afterBaseline)
    this.update({
      scene: this.afterScene, selectedId: null, draft: null,
      notice: { kind: 'info', text: 'Editable layout restored. Your color previews are kept.' },
    })
  }

  select = (id: string | null) => {
    if (this.state.busy || this.state.dragging) return
    const selectedId = this.state.scene.objects.some(object => object.id === id) ? id : null
    if (selectedId === this.state.selectedId) return
    this.update({ selectedId, notice: null, draft: null })
  }
  setMode = (mode: EditMode) => {
    if (this.state.view === 'after' && !this.state.busy && !this.state.dragging) this.update({ mode })
  }
  canToggleLock = (id: string) => this.state.view === 'after' && !this.state.busy && !this.state.dragging
    && this.afterBaseline.objects.some(object => object.id === id && !object.fixed && !object.locked)
  toggleLock = (id: string, previewColor?: string) => {
    if (!this.canToggleLock(id)) return
    this.afterScene = { ...this.afterScene, objects: this.afterScene.objects.map(object => object.id === id
      ? { ...object, ...(!object.locked && previewColor && /^#[0-9a-fA-F]{6}$/.test(previewColor) ? { color: previewColor.toUpperCase() } : {}), locked: !object.locked } : object) }
    this.update({ scene: this.afterScene, notice: null })
  }
  private allowed(edit?: SceneEdit) {
    if (this.state.view === 'before') return false
    const object = this.state.scene.objects.find(item => item.id === this.state.selectedId)
    return Boolean(object && (!edit || edit.object_id === object.id) && !editRestriction(this.state.scene, object))
  }
  beginDrag = () => {
    if (this.state.busy || this.state.dragging || !this.allowed()) return false
    this.update({ dragging: true, notice: null })
    return true
  }
  previewDrag = (edit: SceneEdit) => {
    if (!this.state.dragging || !this.allowed(edit)) return
    this.update({ draft: previewTransform(this.state.scene, edit) })
  }
  finishDrag = (edit: SceneEdit) => {
    if (!this.state.dragging) return Promise.resolve(false)
    return this.submit(edit)
  }

  cancel = (notify = true) => {
    const active = this.state.busy || this.state.dragging
    this.revision++
    this.pending?.abort()
    this.pending = null
    if (active) this.update({
      busy: false, dragging: false, draft: null,
      notice: notify ? { kind: 'info', text: 'Edit cancelled. Your previous layout is restored.' } : null,
    })
  }

  submit = async (edit: SceneEdit): Promise<boolean> => {
    if (this.state.busy || !this.allowed(edit)) return false
    if (!validEditValues(edit)) {
      this.update({ dragging: false, draft: null, notice: { kind: 'error', text: 'Enter a valid position or angle and try again.' } })
      return false
    }
    const base = this.state.scene
    const draft = previewTransform(base, edit)
    if (draft === base) {
      this.update({ dragging: false, draft: null, notice: { kind: 'info', text: 'No layout change.' } })
      return true
    }
    const revision = ++this.revision
    const controller = new AbortController()
    this.pending = controller
    this.update({ busy: true, dragging: false, draft, notice: { kind: 'checking', text: 'Checking placement…' } })
    try {
      const result = await this.client(base, edit, controller.signal)
      if (revision !== this.revision || controller.signal.aborted) return false
      if (result.accepted) {
        this.afterScene = structuredClone(result.scene)
        this.update({
          scene: this.afterScene, draft: null, busy: false,
          notice: { kind: 'success', connection: true, text: 'Layout updated. Changes stay here until you refresh.' },
        })
        return true
      }
      this.update({
        draft: null, busy: false,
        notice: { kind: 'rejected', connection: true, text: 'That placement doesn’t fit. Your previous layout is restored.', details: describeRejection(result, base) },
      })
      return false
    } catch {
      if (revision !== this.revision || controller.signal.aborted) return false
      this.update({
        draft: null, busy: false,
        notice: { kind: 'error', connection: false, text: 'Couldn’t validate this edit. Your previous layout is restored. Check the service connection and try again.' },
      })
      return false
    } finally {
      if (revision === this.revision) this.pending = null
    }
  }
}
