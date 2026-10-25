import { useEffect, useId, useMemo, useRef, useSyncExternalStore } from 'react'
import RoomGenerationWorkspace from '../room-analysis/RoomGenerationWorkspace'
import { Button, Icon } from '../ui/primitives'
import GlassSelect from '../ui/GlassSelect'
import { RoomGenerationController } from '../../lib/room-generation-controller'
import { RoomInputController } from '../../lib/room-input-controller'
import type { RoomInputState } from '../../lib/room-input-controller'
import { formatBytes } from '../../lib/room-input'

const roomTypes = [
  ['bedroom', 'Bedroom'], ['living_room', 'Living room'], ['kitchen', 'Kitchen'], ['bathroom', 'Bathroom'],
  ['office', 'Office'], ['entryway', 'Entryway'], ['other', 'Other'],
] as const
const roomLabel = (type: string | null | undefined) => roomTypes.find(([value]) => value === type)?.[1] ?? 'Not specified'

export default function RoomInputWorkspace({ onConnectionChange, inputController }: { onConnectionChange: (online: boolean) => void; inputController?: RoomInputController }) {
  const controller = useMemo(() => inputController ?? new RoomInputController(), [inputController])
  const generation = useMemo(() => new RoomGenerationController(controller), [controller])
  const generationState = useSyncExternalStore(generation.subscribe, generation.getSnapshot, generation.getSnapshot)
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot)
  useEffect(generation.connect, [generation])
  useEffect(() => {
    void controller.loadLimits()
    const timer = window.setInterval(controller.checkExpiry, 5000)
    return () => { window.clearInterval(timer); controller.release() }
  }, [controller])
  useEffect(() => {
    if (state.notice?.connected !== undefined) onConnectionChange(state.notice.connected)
  }, [state.notice, onConnectionChange])
  useEffect(() => {
    if (generationState.connection !== undefined) onConnectionChange(generationState.connection)
  }, [generationState.connection, onConnectionChange])
  const form = <RoomInputForm state={state} controller={controller} locked={generation.locked()} generating={['starting', 'running', 'awaiting_answer'].includes(generationState.phase)} generated={['completed', 'partial'].includes(generationState.phase)} recovering={generationState.recovery} onGenerate={generation.generate} onCancel={() => void generation.cancel()} onClear={() => void generation.discard()} />
  return <>
    {generationState.run?.working_scene && !['starting', 'running', 'awaiting_answer'].includes(generationState.phase)
      ? <details className="source-inputs"><summary>Room photos &amp; details</summary>{form}</details> : form}
    <RoomGenerationWorkspace state={generationState} controller={generation} onConnectionChange={onConnectionChange} />
  </>
}

export function RoomInputForm({ state, controller, onGenerate, onCancel, onClear, recovering = false, locked = false, generating = false, generated = false }: {
  state: RoomInputState; controller: RoomInputController; onGenerate: () => Promise<void>; onCancel: () => void
  locked?: boolean; generating?: boolean; generated?: boolean; recovering?: boolean; onClear?: () => void
}) {
  const { limits, draft, photos, busy, accepted } = state
  const photoInput = useId()
  const errorSummary = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (state.notice?.kind === 'error' && state.notice.text) {
      errorSummary.current?.focus({ preventScroll: true })
      errorSummary.current?.scrollIntoView({ block: 'nearest' })
    }
  }, [state.notice])
  const set = controller.change
  const acceptedOptions = accepted?.options
  return <div className="input-workspace" id="room-input">
    <form className="room-input-form glass-panel" aria-label="Create your room" onSubmit={event => { event.preventDefault(); void onGenerate() }}>
      <fieldset className="input-edit-lock" disabled={locked}>
        <div className="input-columns">
          <section className="photo-section" aria-labelledby={`${photoInput}-heading`}>
            <div className="input-section-heading"><h3 id={`${photoInput}-heading`}>Room photos</h3><span>{photos.length}{limits ? ` / ${limits.max_images}` : ''}</span></div>
            {state.policyLoading && <p className="input-help" role="status">Loading photo requirements…</p>}
            {state.policyError && <div className="input-notice error" role="alert"><p>{state.policyError}</p><Button onClick={() => void controller.loadLimits()}>Retry connection</Button></div>}
            <div className={`photo-picker${photos.length ? ' has-photos' : ''}`}>
              <span className="upload-symbol"><Icon name="upload" /></span>
              <strong>{photos.length ? 'Add another angle' : `${limits?.min_images ?? 1}–${limits?.max_images ?? 10} photos of the same room`}</strong>
              <label className="photo-picker-control" htmlFor={photoInput}>
                <Icon name="upload" /><span>{photos.length ? 'Add photos' : 'Choose photos'}</span>
                <input id={photoInput} className="visually-hidden" type="file" multiple aria-label="Choose room photos" disabled={!limits || Boolean(busy) || state.policyLoading}
                  accept={limits ? [...limits.allowed_extensions, ...limits.allowed_media_types].join(',') : undefined}
                  onChange={event => { controller.addPhotos(Array.from(event.currentTarget.files ?? [])); event.currentTarget.value = '' }} />
              </label>
            </div>
            {!!photos.length && <>
              <ul className="photo-grid" aria-label="Selected room photos">{photos.map((photo, index) => <li key={photo.id}>
                <div className="photo-preview">
                  {photo.url ? <img src={photo.url} alt={`Selected room photo ${index + 1}`} loading="lazy" onError={() => controller.previewFailed(photo.id)} onLoad={event => controller.previewSize(photo.id, event.currentTarget.naturalWidth, event.currentTarget.naturalHeight)} />
                    : <div className="photo-fallback"><strong>{photo.file.name.split('.').at(-1)?.toUpperCase() ?? 'Photo'}</strong><span>Preview unavailable. Ready to upload.</span></div>}
                  <Button className="photo-remove" disabled={Boolean(busy)} aria-label={`Remove ${photo.file.name}`} onClick={() => controller.removePhoto(photo.id)}><Icon name="close" /></Button>
                </div>
                <div className="photo-caption"><strong title={photo.file.name}>{photo.file.name}</strong><span>{formatBytes(photo.file.size)}</span></div>
                {photo.resolutionIssue && <p className="photo-issue">{photo.resolutionIssue}</p>}
              </li>)}</ul>
            </>}
            {limits && <details className="photo-requirements"><summary>Photo requirements</summary><p>JPEG, PNG, WebP, HEIC, HEIF or AVIF · Up to {formatBytes(limits.max_file_bytes)} each.</p><p>{limits.min_edge_px}–{limits.max_edge_px.toLocaleString()} px per edge · Up to {Math.floor(limits.max_pixels / 1000000)} MP · {formatBytes(limits.max_total_file_bytes)} total.</p></details>}
          </section>
          <fieldset className="room-preferences" disabled={Boolean(busy)}>
            <legend>Room details</legend>
            <GlassSelect label="Room type" value={draft.roomType ?? ''} disabled={locked || Boolean(busy)} placeholder="Choose a room"
              options={roomTypes.map(([value, label]) => ({ value, label }))} onChange={value => set({ roomType: value as typeof draft.roomType })} />
            <div className="dimension-heading">Measurements <span className="optional">Optional</span></div>
            <div className="dimension-inputs">
              <label>Width (m)<input type="number" step="any" min="0" max={limits?.max_room_side_m} inputMode="decimal" placeholder="—" value={draft.width} onChange={event => set({ width: event.target.value })} /></label>
              <label>Length (m)<input type="number" step="any" min="0" max={limits?.max_room_side_m} inputMode="decimal" placeholder="—" value={draft.length} onChange={event => set({ length: event.target.value })} /></label>
              <label>Height (m)<input type="number" step="any" min="0" max={limits?.max_room_height_m} inputMode="decimal" placeholder="—" value={draft.height} onChange={event => set({ height: event.target.value })} /></label>
            </div>
            <p className="input-help measurement-help">Leave unknown measurements blank.</p>
            <label><span>Describe the room <span className="optional">Optional</span></span><textarea value={draft.description} maxLength={limits?.max_description_chars} rows={2} placeholder="Anything the photos don't show?" onChange={event => set({ description: event.target.value })} /></label>
          </fieldset>
        </div>
      </fieldset>
      {state.notice?.text && !(state.notice.kind === 'success' && (generating || generated)) && <div ref={state.notice.kind === 'error' ? errorSummary : undefined} tabIndex={state.notice.kind === 'error' ? -1 : undefined} className={`input-notice ${state.notice.kind}`} role={state.notice.kind === 'error' ? 'alert' : 'status'}>
        <p>{state.notice.text}</p>{!!state.notice.issues?.length && <ul>{state.notice.issues.map((issue, index) => <li key={index}>{issue.file_index != null ? `${photos[issue.file_index]?.file.name ?? `Photo ${issue.file_index + 1}`}: ` : ''}{issue.message}</li>)}</ul>}
      </div>}
      {busy && <div className="upload-progress" role="status"><progress aria-label={generating ? 'Generating room' : busy === 'upload' ? 'Preparing room photos' : 'Clearing room inputs'} /><span>{generating ? 'Generating your room…' : busy === 'upload' ? 'Preparing your photos…' : 'Clearing your room inputs…'}</span></div>}
      {accepted && <details className="accepted-details"><summary>{state.uncertain ? 'Request not completed' : state.dirty ? 'Changes not generated' : 'View submitted details'}</summary><dl>
        <dt>Photos</dt><dd>{accepted.images.length}</dd><dt>Room</dt><dd>{roomLabel(acceptedOptions?.room_type)}</dd>
        <dt>Dimensions</dt><dd>{acceptedOptions?.dimensions ? `${acceptedOptions.dimensions.width_m ?? '?'} × ${acceptedOptions.dimensions.length_m ?? '?'} × ${acceptedOptions.dimensions.height_m ?? '?'} m (${acceptedOptions.dimensions.measurements_source === 'user_confirmed' ? 'provided by you' : 'estimated'})` : 'Not specified'}</dd>
        {acceptedOptions?.description && <><dt>Description</dt><dd>{acceptedOptions.description}</dd></>}
        <dt>Available until</dt><dd>{new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' }).format(new Date(accepted.expires_at))}</dd>
      </dl></details>}
      <div className="input-form-footer">
        <details className="privacy-note"><summary><Icon name="lock" />Private photos · Temporary session</summary><p>Refresh clears this form. Uploaded copies expire automatically. Nothing is shared publicly.</p></details>
        <div className="input-actions">
          {busy || generating || locked ? <Button onClick={onCancel}>Cancel</Button> : (photos.length > 0 || accepted || state.dirty || state.uncertain) && <Button variant="quiet" onClick={onClear ?? (() => void controller.discard())} disabled={!photos.length && !accepted && !state.dirty && !state.uncertain}>Clear</Button>}
          {recovering && <Button onClick={onClear}>Discard &amp; clear inputs</Button>}
          <Button variant="primary" type="submit" disabled={Boolean(busy) || locked || !limits || state.policyLoading || (generated && !state.dirty && !state.uncertain)}>
            {generating ? 'Creating room…' : busy === 'upload' ? 'Preparing photos…' : generated && !state.dirty && !state.uncertain ? 'Room created' : 'Create room'}<Icon name={generated && !state.dirty && !state.uncertain ? 'check' : 'arrow'} />
          </Button>
        </div>
      </div>
    </form>
  </div>
}
