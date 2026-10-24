import { useEffect, useMemo, useSyncExternalStore } from 'react'
import RoomGenerationWorkspace from '../room-analysis/RoomGenerationWorkspace'
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
  return <>
    <RoomInputForm state={state} controller={controller} locked={generation.locked()} generating={['starting', 'running', 'awaiting_answer'].includes(generationState.phase)} generated={['completed', 'partial'].includes(generationState.phase)} recovering={generationState.recovery} onGenerate={generation.generate} onCancel={() => void generation.cancel()} onClear={() => void generation.discard()} />
    <RoomGenerationWorkspace state={generationState} controller={generation} onConnectionChange={onConnectionChange} />
  </>
}

export function RoomInputForm({ state, controller, onGenerate, onCancel, onClear, recovering = false, locked = false, generating = false, generated = false }: {
  state: RoomInputState; controller: RoomInputController; onGenerate: () => Promise<void>; onCancel: () => void
  locked?: boolean; generating?: boolean; generated?: boolean; recovering?: boolean; onClear?: () => void
}) {
  const { limits, draft, photos, busy, accepted } = state
  const total = photos.reduce((bytes, photo) => bytes + photo.file.size, 0)
  const set = controller.change
  const acceptedOptions = accepted?.options
  return (
    <div className="input-workspace">
      <form className="room-input-form" onSubmit={event => { event.preventDefault(); void onGenerate() }}>
        <fieldset className="input-edit-lock" disabled={locked}>
        <div className="input-section-title"><span className="section-label">Start with your space</span><h2>A few photos. A little context.</h2><p>Capture different angles of one room. We’ll use your photos and room details to reconstruct your current space.</p></div>
        <section className="photo-section" aria-labelledby="photo-heading">
          <div className="input-section-heading"><h3 id="photo-heading">1. Room photos</h3><span>{photos.length}{limits ? ` / ${limits.max_images}` : ''} selected</span></div>
          {state.policyLoading && <p className="input-help" role="status">Loading photo requirements…</p>}
          {state.policyError && <div className="input-notice error" role="alert"><p>{state.policyError}</p><button type="button" onClick={() => void controller.loadLimits()}>Retry connection</button></div>}
          <div className="photo-picker">
            <strong>{photos.length ? 'Add another angle' : 'Bring your room into view'}</strong>
            <p>Choose photos from your phone or computer.</p>
            <input type="file" multiple aria-label="Choose room photos" disabled={!limits || Boolean(busy) || state.policyLoading}
              accept={limits ? [...limits.allowed_extensions, ...limits.allowed_media_types].join(',') : undefined}
              onChange={event => { controller.addPhotos(Array.from(event.currentTarget.files ?? [])); event.currentTarget.value = '' }} />
            {limits && <p className="input-help">JPEG, PNG, WebP, HEIC, HEIF or AVIF · Up to {formatBytes(limits.max_file_bytes)} each</p>}
          </div>
          {limits && <p className="input-help">{limits.min_edge_px}–{limits.max_edge_px.toLocaleString()} px per edge · Up to {Math.floor(limits.max_pixels / 1000000)} MP · {formatBytes(limits.max_total_file_bytes)} total</p>}
          {!!photos.length && <>
            <ul className="photo-grid" aria-label="Selected room photos">
              {photos.map((photo, index) => <li key={photo.id}>
                <div className="photo-preview">{photo.url ? <img src={photo.url} alt={`Selected room photo ${index + 1}`} loading="lazy"
                  onError={() => controller.previewFailed(photo.id)} onLoad={event => controller.previewSize(photo.id, event.currentTarget.naturalWidth, event.currentTarget.naturalHeight)} />
                  : <div className="photo-fallback"><strong>{photo.file.name.split('.').at(-1)?.toUpperCase() ?? 'Photo'}</strong><span>Preview unavailable in this browser. You can still upload it.</span></div>}</div>
                <div className="photo-caption"><strong title={photo.file.name}>{photo.file.name}</strong><span>{formatBytes(photo.file.size)}</span>
                  <button type="button" disabled={Boolean(busy)} aria-label={`Remove ${photo.file.name}`} onClick={() => controller.removePhoto(photo.id)}>Remove</button></div>
                {photo.resolutionIssue && <p className="photo-issue">{photo.resolutionIssue}</p>}
              </li>)}
            </ul>
            <p className="input-help">{photos.length} photo{photos.length === 1 ? '' : 's'} · {formatBytes(total)} selected</p>
          </>}
        </section>

        <fieldset className="room-preferences" disabled={Boolean(busy)}>
          <legend>2. About your room</legend>
          <label>Room type<select value={draft.roomType ?? ''} onChange={event => set({ roomType: event.target.value as typeof draft.roomType })}>
            <option value="">Not sure yet</option>{roomTypes.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select></label>
          <label>Describe the room <span className="optional">Optional</span><textarea value={draft.description} maxLength={limits?.max_description_chars} rows={3} placeholder="How do you use this room? Describe its current layout."
            onChange={event => set({ description: event.target.value })} /></label>
          <div className="dimension-inputs">
            <label>Width (m)<input type="number" step="any" min="0" max={limits?.max_room_side_m} inputMode="decimal" value={draft.width} onChange={event => set({ width: event.target.value })} /></label>
            <label>Length (m)<input type="number" step="any" min="0" max={limits?.max_room_side_m} inputMode="decimal" value={draft.length} onChange={event => set({ length: event.target.value })} /></label>
            <label>Height (m)<input type="number" step="any" min="0" max={limits?.max_room_height_m} inputMode="decimal" value={draft.height} onChange={event => set({ height: event.target.value })} /></label>
          </div>
          <p className="input-help">Measurements are optional. Leave unknown values blank; we’ll ask if a missing dimension is needed to build your room.</p>
        </fieldset>

        </fieldset>
        {state.notice && !(state.notice.kind === 'success' && (generating || generated)) && <div className={`input-notice ${state.notice.kind}`} role={state.notice.kind === 'error' ? 'alert' : 'status'}>
          <p>{state.notice.text}</p>{!!state.notice.issues?.length && <ul>{state.notice.issues.map((issue, index) => <li key={index}>
            {issue.file_index != null ? `${photos[issue.file_index]?.file.name ?? `Photo ${issue.file_index + 1}`}: ` : ''}{issue.message}
          </li>)}</ul>}
        </div>}
        {busy && <div className="upload-progress" role="status"><progress aria-label={generating ? 'Generating room' : busy === 'upload' ? 'Preparing room photos' : 'Clearing room inputs'} /><span>{generating ? 'Generating your room…' : busy === 'upload' ? 'Preparing your photos…' : 'Clearing your room inputs…'}</span></div>}
        <div className="input-actions"><button className="primary-action" type="submit" disabled={Boolean(busy) || locked || !limits || state.policyLoading || !photos.length || (generated && !state.dirty && !state.uncertain)}>
          {generating ? 'Generating room…' : busy === 'upload' ? 'Preparing photos…' : generated && !state.dirty && !state.uncertain ? 'Room generated' : 'Generate room'}</button>
          {busy || generating || locked ? <button type="button" onClick={onCancel}>Cancel</button> : <button type="button" onClick={onClear ?? (() => void controller.discard())} disabled={locked || (!photos.length && !accepted && !state.dirty && !state.uncertain)}>Clear inputs</button>}
          {recovering && <button type="button" onClick={onClear}>Discard &amp; clear inputs</button>}
        </div>
        <p className="input-help privacy-note">Photos stay private and temporary. Refresh clears this form; uploaded copies expire automatically. Nothing is shared publicly.</p>
      </form>

      <aside className="input-summary" aria-label="Your room input summary">
        <span className="section-label">Your room details</span>
        {accepted ? <>
          <h2>{state.uncertain ? 'Request not completed' : state.dirty ? 'Changes not generated' : generating ? 'Generating your room' : generated ? 'Room generated' : 'Ready to generate'}</h2>
          <p>{state.uncertain ? 'Try Generate room again, or clear the inputs to start over.' : state.dirty ? 'Your form has changed. Choose Generate room to use the updated inputs.' : generating ? 'Your photos are being turned into an approximate room model.' : generated ? 'Your reconstructed room is ready below.' : 'Choose Generate room to continue with your current inputs.'}</p>
          <dl className="input-summary-list"><div><dt>Photos</dt><dd>{accepted.images.length}</dd></div><div><dt>Room type</dt><dd>{roomLabel(acceptedOptions?.room_type)}</dd></div>
            <div><dt>Available until</dt><dd>{new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' }).format(new Date(accepted.expires_at))}</dd></div></dl>
          <details className="accepted-details"><summary>View submitted details</summary><dl>
            <dt>Dimensions</dt><dd>{acceptedOptions?.dimensions ? `${acceptedOptions.dimensions.width_m ?? '?'} × ${acceptedOptions.dimensions.length_m ?? '?'} × ${acceptedOptions.dimensions.height_m ?? '?'} m (${acceptedOptions.dimensions.measurements_source === 'user_confirmed' ? 'provided by you' : 'estimated'})` : 'Not specified'}</dd>
            {acceptedOptions?.description && <><dt>Description</dt><dd>{acceptedOptions.description}</dd></>}
          </dl></details>
          <p className="input-help">These are your submitted details. Generation progress and your room model appear below.</p>
        </> : <>
          <h2>Start with what you know.</h2><p>A few different angles help show the room, its furniture, doors, and windows.</p>
          <ol className="photo-tips"><li>Photograph the same room from different corners.</li><li>Include the floor and as much of each wall as you can.</li><li>Use clear, well-lit still photos.</li></ol>
          <div className="input-side-note"><strong>No measurements yet?</strong><p>You can leave dimensions blank and add them later.</p></div>
        </>}
      </aside>
    </div>
  )
}
