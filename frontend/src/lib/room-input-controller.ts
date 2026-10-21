import { discardRoomInput, fetchUploadLimits, uploadRoomInput } from './api'
import { checkPhotoFiles, draftOptions, emptyDraft, RoomInputError } from './room-input'
import type { InputDraft, InputLimits } from './room-input'
import type { PreparedRoomInput } from './room-input-response.generated'
import type { UploadIssue } from './room-input-error.generated'

export interface SelectedPhoto { id: string; file: File; url: string | null; resolutionIssue?: string }
export interface InputNotice { kind: 'info' | 'error' | 'success'; text: string; issues?: UploadIssue[]; connected?: boolean }
export interface RoomInputState {
  limits: InputLimits | null; policyLoading: boolean; policyError: string | null
  photos: SelectedPhoto[]; draft: InputDraft; busy: 'upload' | 'discard' | null
  accepted: PreparedRoomInput | null; dirty: boolean; uncertain: boolean; notice: InputNotice | null
}
interface InputClient {
  limits: typeof fetchUploadLimits; upload: typeof uploadRoomInput; discard: typeof discardRoomInput
}
interface ObjectUrls { create: (file: File) => string; revoke: (url: string) => void }

/** Own file handles/URLs and request revisions outside render; nothing is persisted. */
export class RoomInputController {
  private state: RoomInputState = {
    limits: null, policyLoading: false, policyError: null, photos: [], draft: emptyDraft(), busy: null,
    accepted: null, dirty: false, uncertain: false, notice: null,
  }
  private listeners = new Set<() => void>()
  private policy: AbortController | null = null
  private pending: AbortController | null = null
  private revision = 0
  constructor(
    private client: InputClient = { limits: fetchUploadLimits, upload: uploadRoomInput, discard: discardRoomInput },
    private urls: ObjectUrls = { create: file => URL.createObjectURL(file), revoke: url => URL.revokeObjectURL(url) },
    private now = Date.now,
  ) {}
  getSnapshot = () => this.state
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener) } }
  private update(patch: Partial<RoomInputState>) {
    this.state = { ...this.state, ...patch }
    this.listeners.forEach(listener => listener())
  }
  loadLimits = async () => {
    if (this.state.busy) return
    this.policy?.abort()
    const controller = new AbortController()
    this.policy = controller
    this.update({ policyLoading: true, policyError: null })
    try {
      const limits = await this.client.limits(controller.signal)
      if (this.policy !== controller || controller.signal.aborted) return
      this.update({ limits, policyLoading: false, notice: { kind: 'info', text: 'Ready to select your photos.', connected: true } })
    } catch (error) {
      if (this.policy !== controller || controller.signal.aborted) return
      this.update({ policyLoading: false, policyError: 'Couldn’t load upload limits. Check the service connection and try again.',
        notice: { kind: 'error', text: 'Upload limits are unavailable.', connected: error instanceof RoomInputError ? error.connected : false } })
    }
  }
  change = (patch: Partial<InputDraft>) => {
    if (this.state.busy) return
    const draft = { ...this.state.draft, ...patch }
    this.update({ draft, dirty: true, notice: null })
  }
  addPhotos = (files: readonly File[]) => {
    if (this.state.busy || !this.state.limits || !files.length) return
    const all = [...this.state.photos.map(photo => photo.file), ...files]
    const issues = checkPhotoFiles(all, this.state.limits, false)
    if (issues.length) {
      const named = issues.map(issue => issue.file_index == null ? issue : { ...issue, file_index: null, message: `${all[issue.file_index].name}: ${issue.message}` })
      this.update({ notice: { kind: 'error', text: 'Photos weren’t added. Check the selection.', issues: named } }); return
    }
    const added: SelectedPhoto[] = []
    try {
      for (const file of files) added.push({ id: crypto.randomUUID(), file, url: this.urls.create(file) })
    } catch {
      added.forEach(photo => { if (photo.url) this.urls.revoke(photo.url) })
      this.update({ notice: { kind: 'error', text: 'Couldn’t prepare photo previews. Try selecting the files again.' } })
      return
    }
    this.update({ photos: [...this.state.photos, ...added], dirty: true, notice: null })
  }
  removePhoto = (id: string) => {
    if (this.state.busy) return
    const photo = this.state.photos.find(photo => photo.id === id)
    if (photo?.url) this.urls.revoke(photo.url)
    this.update({ photos: this.state.photos.filter(photo => photo.id !== id), dirty: true, notice: null })
  }
  previewFailed = (id: string) => {
    const photo = this.state.photos.find(photo => photo.id === id)
    if (!photo?.url) return
    this.urls.revoke(photo.url)
    this.update({ photos: this.state.photos.map(photo => photo.id === id ? { ...photo, url: null } : photo) })
  }
  previewSize = (id: string, width: number, height: number) => {
    const limits = this.state.limits
    if (!limits) return
    const resolutionIssue = minOutside(width, height, limits) ? 'This photo is outside the allowed resolution. Resize it or choose another.' : undefined
    this.update({ photos: this.state.photos.map(photo => photo.id === id ? { ...photo, resolutionIssue } : photo) })
  }
  checkExpiry = () => {
    if (this.state.accepted && Date.parse(this.state.accepted.expires_at) <= this.now() && !this.state.busy) {
      this.update({ accepted: null, dirty: true, notice: { kind: 'info', text: 'Your temporary photos have expired. Your selections are kept; choose Generate room to continue.' } })
    }
  }
  cancel = (notify = true) => {
    const wasUpload = this.state.busy === 'upload'
    this.revision++
    this.pending?.abort()
    this.pending = null
    if (this.state.busy) this.update({ busy: null, uncertain: true,
      notice: notify ? { kind: 'info', text: wasUpload ? 'Cancelled. Your selections are kept; choose Generate room when ready.' : 'Discard cancelled. Your selections are kept; retry to confirm removal.' } : null })
  }
  submit = async (): Promise<boolean> => {
    if (this.state.busy || !this.state.limits || this.state.policyLoading) return false
    const files = this.state.photos.map(photo => photo.file)
    const issues = checkPhotoFiles(files, this.state.limits)
    this.state.photos.forEach((photo, file_index) => {
      if (photo.resolutionIssue) issues.push({ code: 'image_resolution', message: photo.resolutionIssue, file_index })
    })
    if (issues.length) { this.update({ notice: { kind: 'error', text: 'Check your selected photos.', issues } }); return false }
    let options
    try { options = draftOptions(this.state.draft, this.state.limits) } catch (error) {
      this.update({ notice: { kind: 'error', text: error instanceof Error ? error.message : 'Check your room details.' } }); return false
    }
    const revision = ++this.revision, controller = new AbortController()
    this.pending = controller
    this.update({ busy: 'upload', notice: null })
    try {
      const accepted = await this.client.upload(files, options, controller.signal)
      if (revision !== this.revision || controller.signal.aborted) return false
      if (Date.parse(accepted.expires_at) <= this.now()) throw new RoomInputError('This upload has already expired. Please try again.', [], true, true)
      this.update({ accepted, dirty: false, uncertain: false, busy: null,
        notice: { kind: 'success', text: 'Photos received. Your room details are ready.', connected: true } })
      return true
    } catch (error) {
      if (revision !== this.revision || controller.signal.aborted) return false
      this.update({ busy: null, uncertain: this.state.uncertain || !(error instanceof RoomInputError) || error.uncertain, notice: {
        kind: 'error', text: error instanceof RoomInputError ? error.message : 'Couldn’t complete the upload. Your selections are kept. Check the connection and try again.',
        issues: error instanceof RoomInputError ? error.issues : [], connected: error instanceof RoomInputError ? error.connected : false,
      } })
      return false
    } finally { if (revision === this.revision) this.pending = null }
  }
  discard = async (): Promise<boolean> => {
    if (this.state.busy) return false
    const accepted = this.state.accepted
    if (!accepted) { this.clear(); return true }
    const revision = ++this.revision, controller = new AbortController()
    this.pending = controller
    this.update({ busy: 'discard', notice: null })
    try {
      await this.client.discard(accepted.input_id, controller.signal)
      if (revision !== this.revision || controller.signal.aborted) return false
      this.clear(true)
      return true
    } catch (error) {
      if (revision !== this.revision || controller.signal.aborted) return false
      this.update({ busy: null, uncertain: true, notice: { kind: 'error', text: 'Couldn’t confirm deletion. Your inputs are kept so you can retry; temporary uploads expire automatically.',
        connected: error instanceof RoomInputError ? error.connected : false } })
      return false
    } finally { if (revision === this.revision) this.pending = null }
  }
  private clear(connected?: boolean) {
    this.state.photos.forEach(photo => { if (photo.url) this.urls.revoke(photo.url) })
    const uncertain = this.state.uncertain
    this.update({ photos: [], draft: emptyDraft(), accepted: null, dirty: false, uncertain: false, busy: null,
      notice: { kind: 'info', text: uncertain ? 'Selections cleared. Any interrupted upload will expire automatically.' : 'Your room inputs have been cleared.', connected } })
  }
  release = () => {
    this.policy?.abort()
    this.policy = null
    this.cancel(false)
    this.state.photos.forEach(photo => { if (photo.url) this.urls.revoke(photo.url) })
    this.update({ photos: [] })
  }
}

function minOutside(width: number, height: number, limits: InputLimits): boolean {
  return Math.min(width, height) < limits.min_edge_px || Math.max(width, height) > limits.max_edge_px || width * height > limits.max_pixels
}
