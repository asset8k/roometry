import { useEffect, useMemo, useSyncExternalStore } from 'react'
import RoomAnalysisWorkspace from '../room-analysis/RoomAnalysisWorkspace'
import { RoomAnalysisController } from '../../lib/room-analysis-controller'
import { RoomWorkflowController } from '../../lib/room-workflow-controller'
import { RoomInputController } from '../../lib/room-input-controller'
import type { RoomInputState } from '../../lib/room-input-controller'
import { formatBytes } from '../../lib/room-input'
import type { GoalChoice } from '../../lib/room-input'

const roomTypes = [
  ['bedroom', 'Bedroom'], ['living_room', 'Living room'], ['kitchen', 'Kitchen'], ['bathroom', 'Bathroom'],
  ['office', 'Office'], ['entryway', 'Entryway'], ['other', 'Other'],
] as const
const goals: { value: GoalChoice; label: string }[] = [
  { value: 'more_storage', label: 'More storage' }, { value: 'workspace', label: 'A better workspace' },
  { value: 'more_open_space', label: 'More open space' },
  { value: 'better_aesthetics', label: 'Better aesthetics' },
]
const roomLabel = (type: string | null | undefined) => roomTypes.find(([value]) => value === type)?.[1] ?? 'Not specified'

export default function RoomInputWorkspace({ onConnectionChange }: { onConnectionChange: (online: boolean) => void }) {
  const controller = useMemo(() => new RoomInputController(), [])
  const analysisController = useMemo(() => new RoomAnalysisController(), [])
  const workflow = useMemo(() => new RoomWorkflowController(controller, analysisController), [controller, analysisController])
  const analysisState = useSyncExternalStore(analysisController.subscribe, analysisController.getSnapshot, analysisController.getSnapshot)
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot)
  useEffect(workflow.connect, [workflow])
  useEffect(() => {
    void controller.loadLimits()
    const timer = window.setInterval(controller.checkExpiry, 5000)
    return () => { window.clearInterval(timer); controller.release() }
  }, [controller])
  useEffect(() => {
    if (state.notice?.connected !== undefined) onConnectionChange(state.notice.connected)
  }, [state.notice, onConnectionChange])
  useEffect(() => {
    if (analysisState.notice?.connected !== undefined) onConnectionChange(analysisState.notice.connected)
  }, [analysisState.notice, onConnectionChange])
  return <>
    <RoomInputForm state={state} controller={controller} locked={Boolean(analysisState.busy)} analyzing={analysisState.busy === 'analysis'} analyzed={Boolean(analysisState.analysis)} onAnalyze={workflow.analyze} onCancel={workflow.cancel} />
    <RoomAnalysisWorkspace state={analysisState} controller={analysisController} onConnectionChange={onConnectionChange} />
  </>
}

export function RoomInputForm({ state, controller, onAnalyze, onCancel, locked = false, analyzing = false, analyzed = false }: {
  state: RoomInputState; controller: RoomInputController; onAnalyze: () => Promise<void>; onCancel: () => void
  locked?: boolean; analyzing?: boolean; analyzed?: boolean
}) {
  const { limits, draft, photos, busy, accepted } = state
  const total = photos.reduce((bytes, photo) => bytes + photo.file.size, 0)
  const set = controller.change
  const acceptedOptions = accepted?.options
  return (
    <div className="input-workspace">
      <form className="room-input-form" onSubmit={event => { event.preventDefault(); void onAnalyze() }}>
        <fieldset className="input-edit-lock" disabled={locked}>
        <div className="input-section-title"><span className="section-label">Start with your space</span><h2>A few photos. A little context.</h2><p>Capture different angles of one room, then tell us what you’d like to improve.</p></div>
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
          <legend>2. How would you like to improve this room?</legend>
          <label>Room type<select value={draft.roomType ?? ''} onChange={event => set({ roomType: event.target.value as typeof draft.roomType })}>
            <option value="">Not sure yet</option>{roomTypes.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select></label>
          <div className="goal-choices" role="group" aria-label="Room improvement goals">{goals.map(goal => <label key={goal.value}>
            <input type="checkbox" checked={draft.goals.includes(goal.value)} onChange={event => set({ goals: event.target.checked ? [...draft.goals, goal.value] : draft.goals.filter(value => value !== goal.value) })} /><span>{goal.label}</span>
          </label>)}</div>
          <p className="input-help">No specific goal? Leave these blank for a general room analysis.</p>
          <label>Anything you want to improve? <span className="optional">Optional</span><textarea value={draft.goalText} maxLength={limits?.max_goal_text_chars} rows={3}
            placeholder="A cozier look, better storage, a calmer layout… Tell us what to keep or avoid." onChange={event => set({ goalText: event.target.value })} /></label>

          <details className="optional-room-details"><summary>Room details & preferences <span>Optional</span></summary>
            <label>Describe the room<textarea value={draft.description} maxLength={limits?.max_description_chars} rows={3} placeholder="How do you use this room?"
              onChange={event => set({ description: event.target.value })} /></label>
            <div className="dimension-inputs">
              <label>Width (m)<input type="number" step="any" min="0" max={limits?.max_room_side_m} inputMode="decimal" value={draft.width} onChange={event => set({ width: event.target.value })} /></label>
              <label>Length (m)<input type="number" step="any" min="0" max={limits?.max_room_side_m} inputMode="decimal" value={draft.length} onChange={event => set({ length: event.target.value })} /></label>
              <label>Height (m)<input type="number" step="any" min="0" max={limits?.max_room_height_m} inputMode="decimal" value={draft.height} onChange={event => set({ height: event.target.value })} /></label>
            </div>
            <p className="input-help">Leave unknown measurements blank. Photos alone don’t establish exact dimensions.</p>
            <div className="budget-inputs"><label>Budget<input type="number" min={0} max={limits?.max_budget_amount} step="any" inputMode="decimal" placeholder="Not specified" value={draft.budget} onChange={event => set({ budget: event.target.value })} /></label>
              <label>Currency<input type="text" maxLength={3} value={draft.currency} autoCapitalize="characters" onChange={event => set({ currency: event.target.value.toUpperCase() })} aria-describedby="budget-hint" /></label></div>
            <p id="budget-hint" className="input-help">Use a three-letter code, such as USD, EUR or KZT. A budget of 0 means no purchase budget.</p>
            <label>Style preference<input type="text" value={draft.style} maxLength={limits?.max_style_chars} placeholder="Simple, cozy, minimalist…" onChange={event => set({ style: event.target.value })} /></label>
          </details>
        </fieldset>

        </fieldset>
        {state.notice && !(state.notice.kind === 'success' && (analyzing || analyzed)) && <div className={`input-notice ${state.notice.kind}`} role={state.notice.kind === 'error' ? 'alert' : 'status'}>
          <p>{state.notice.text}</p>{!!state.notice.issues?.length && <ul>{state.notice.issues.map((issue, index) => <li key={index}>
            {issue.file_index != null ? `${photos[issue.file_index]?.file.name ?? `Photo ${issue.file_index + 1}`}: ` : ''}{issue.message}
          </li>)}</ul>}
        </div>}
        {(busy || analyzing) && <div className="upload-progress" role="status"><progress aria-label={analyzing ? 'Analyzing room photos' : busy === 'upload' ? 'Preparing room photos' : 'Clearing room inputs'} /><span>{analyzing ? 'Analyzing your photos together…' : busy === 'upload' ? 'Preparing your photos…' : 'Clearing your room inputs…'}</span></div>}
        <div className="input-actions"><button className="primary-action" type="submit" disabled={Boolean(busy) || locked || !limits || state.policyLoading || !photos.length || (analyzed && !state.dirty && !state.uncertain)}>
          {analyzing ? 'Analyzing photos…' : busy === 'upload' ? 'Preparing photos…' : analyzed && !state.dirty && !state.uncertain ? 'Photos analyzed' : 'Analyze photos'}</button>
          {busy || analyzing ? <button type="button" onClick={onCancel}>Cancel</button> : <button type="button" onClick={() => void controller.discard()} disabled={locked || (!photos.length && !accepted && !state.dirty && !state.uncertain)}>Clear inputs</button>}
        </div>
        <p className="input-help privacy-note">Photos stay private and temporary. Refresh clears this form; uploaded copies expire automatically. Nothing is shared publicly.</p>
      </form>

      <aside className="input-summary" aria-label="Your room input summary">
        <span className="section-label">Your room, your priorities</span>
        {accepted ? <>
          <h2>{state.uncertain ? 'Request not completed' : state.dirty ? 'Changes not analyzed' : analyzing ? 'Analyzing your photos' : analyzed ? 'Room analyzed' : 'Ready to analyze'}</h2>
          <p>{state.uncertain ? 'Try Analyze photos again, or clear the inputs to start over.' : state.dirty ? 'Your form has changed. Choose Analyze photos to use the updated inputs.' : analyzing ? 'We’re identifying the room, furniture, doors, and windows.' : analyzed ? 'Review the observations and room dimensions below.' : 'Choose Analyze photos to continue with your current inputs.'}</p>
          <dl className="input-summary-list"><div><dt>Photos</dt><dd>{accepted.images.length}</dd></div><div><dt>Room type</dt><dd>{roomLabel(acceptedOptions?.room_type)}</dd></div>
            <div><dt>Available until</dt><dd>{new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' }).format(new Date(accepted.expires_at))}</dd></div></dl>
          {acceptedOptions?.goal_text && <p className="submitted-note">{acceptedOptions.goal_text}</p>}
          <details className="accepted-details"><summary>View submitted details</summary><dl>
            <dt>Goals</dt><dd>{acceptedOptions?.goals?.map(value => goals.find(goal => goal.value === value)?.label ?? 'General analysis').join(', ')}</dd>
            <dt>Dimensions</dt><dd>{acceptedOptions?.dimensions ? `${acceptedOptions.dimensions.width_m ?? '?'} × ${acceptedOptions.dimensions.length_m ?? '?'} × ${acceptedOptions.dimensions.height_m ?? '?'} m (${acceptedOptions.dimensions.measurements_source === 'user_confirmed' ? 'provided by you' : 'estimated'})` : 'Not specified'}</dd>
            <dt>Budget</dt><dd>{acceptedOptions?.budget ? `${acceptedOptions.budget.amount} ${acceptedOptions.budget.currency}` : 'Not specified'}</dd>
            {acceptedOptions?.description && <><dt>Description</dt><dd>{acceptedOptions.description}</dd></>}
            {acceptedOptions?.style && <><dt>Style</dt><dd>{acceptedOptions.style}</dd></>}
            {acceptedOptions?.constraints && <><dt>Keep or avoid</dt><dd>{acceptedOptions.constraints}</dd></>}
          </dl></details>
          <p className="input-help">These are your submitted details. Analysis and the room model appear below.</p>
        </> : <>
          <h2>Start with what you know.</h2><p>A few different angles help show the room, its furniture, doors, and windows.</p>
          <ol className="photo-tips"><li>Photograph the same room from different corners.</li><li>Include the floor and as much of each wall as you can.</li><li>Use clear, well-lit still photos.</li></ol>
          <div className="input-side-note"><strong>No measurements yet?</strong><p>You can leave dimensions blank and add them later.</p></div>
        </>}
      </aside>
    </div>
  )
}
