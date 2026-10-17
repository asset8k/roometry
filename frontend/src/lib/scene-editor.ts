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

  constructor(scene: RoomScene, private client: PreviewClient = requestEditPreview) {
    this.state = {
      scene: structuredClone(scene), draft: null, selectedId: null,
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

  select = (id: string | null) => {
    if (this.state.busy || this.state.dragging) return
    const selectedId = this.state.scene.objects.some(object => object.id === id) ? id : null
    if (selectedId === this.state.selectedId) return
    this.update({ selectedId, notice: null, draft: null })
  }
  setMode = (mode: EditMode) => {
    if (!this.state.busy && !this.state.dragging) this.update({ mode })
  }
  private allowed(edit?: SceneEdit) {
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
        this.update({
          scene: structuredClone(result.scene), draft: null, busy: false,
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
