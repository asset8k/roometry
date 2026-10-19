import SceneWorkspace from '../room-viewer/SceneWorkspace'
import { dimensionAxes, dimensionLabels } from '../../lib/room-analysis'
import type { AnalysisState, RoomAnalysisController } from '../../lib/room-analysis-controller'

const label = (value: string) => value.replaceAll('_', ' ')

export default function RoomAnalysisWorkspace({ state, controller, onConnectionChange }: {
  state: AnalysisState; controller: RoomAnalysisController; onConnectionChange: (online: boolean) => void
}) {
  const { input, analysis, proposal, busy, scene } = state
  if (!input && !state.notice) return null
  const observations = analysis?.observations
  const entities = observations ? [...observations.objects, ...observations.openings, ...observations.walls] : []
  return <section className="analysis-workspace" aria-label="Room analysis and reconstruction" aria-busy={Boolean(busy)}>
    <div className="analysis-card">
      <span className="section-label">From photos to a room</span>
      <h2>3. Understand your space</h2>
      {!analysis && input && <>
        <p>Analyze your {input.images.length} uploaded photo{input.images.length === 1 ? '' : 's'} together to identify furniture, doors, windows, and an approximate layout.</p>
        <p className="input-help">Photos can suggest what is there. Measurements help turn those observations into a useful room model.</p>
        <div className="input-actions"><button className="primary-action" type="button" disabled={Boolean(busy)} onClick={() => void controller.analyze()}>{busy === 'analysis' ? 'Analyzing photos…' : 'Analyze photos'}</button></div>
      </>}
      {observations && <>
        <div className="analysis-stats"><span>{observations.objects.length} objects</span><span>{observations.openings.length} doors & windows</span><span>{label(observations.room_type ?? 'room type uncertain')}</span></div>
        <p className="input-help">These are observations and estimates. Review uncertain details before relying on the model.</p>
        <details className="analysis-observations" open><summary>Detected objects and openings</summary>
          {!observations.objects.length && !observations.openings.length && <p>No individual objects or openings were identified.</p>}
          <ul>{[...observations.objects, ...observations.openings].map(entity => <li key={entity.id}>
            <div><strong>{'label' in entity ? entity.label : label(entity.type)}</strong><span>{'relative_location' in entity && entity.relative_location ? entity.relative_location : 'Location uncertain'}</span></div>
            <span className={`observation-confidence ${entity.confidence}`}>{entity.confidence} confidence · {entity.source_image_ids.length} photo{entity.source_image_ids.length === 1 ? '' : 's'}</span>
          </li>)}</ul>
        </details>
        {!!observations.possible_duplicates.length && <div className="analysis-caution"><h3>Possible duplicate observations</h3><p>These may show the same item from different angles. They remain separate in the scene until their identity is resolved.</p><ul>{observations.possible_duplicates.map((pair, index) => <li key={index}><strong>{pair.entity_ids.map(id => {
          const entity = entities.find(item => item.id === id)
          return entity && 'label' in entity ? entity.label : id
        }).join(' / ')}</strong>: {pair.reason}</li>)}</ul></div>}
        {!!observations.unknowns.length && <div className="analysis-caution"><h3>What we couldn’t establish</h3><ul>{observations.unknowns.map((note, index) => <li key={index}>{note}</li>)}</ul></div>}
        {!!(observations.assumptions.length + observations.warnings.length) && <details className="analysis-notes"><summary>Analysis assumptions and cautions</summary><ul>{[...observations.assumptions, ...observations.warnings].map((note, index) => <li key={index}>{note}</li>)}</ul></details>}
        <form className="measurement-form" onSubmit={event => { event.preventDefault(); void controller.build() }}>
          <fieldset className="room-preferences" disabled={Boolean(busy)}>
            <legend>4. Give your room its dimensions</legend>
            <p className="input-help">Enter or check the room’s internal measurements in meters. Photo estimates are starting points; verify them in your room.</p>
            <div className="dimension-inputs">{dimensionAxes.map(axis => <label key={axis} htmlFor={`analysis-${axis}`}>
              {dimensionLabels[axis]} (m)<input id={`analysis-${axis}`} type="number" step="any" min="0" max={axis === 'height_m' ? 10 : 30} inputMode="decimal" value={state.measurements[axis]} onChange={event => controller.changeMeasurements({ [axis]: event.target.value })} />
            </label>)}</div>
            <label className="confirmation-check"><input type="checkbox" checked={state.confirmed} onChange={event => controller.confirm(event.target.checked)} /><span>I have checked the measurements entered above.</span></label>
            <label className="confirmation-check"><input type="checkbox" checked={state.allowEstimates} onChange={event => controller.acceptEstimates(event.target.checked)} /><span>Use available photo estimates for dimensions I haven’t confirmed.</span></label>
            {scene && <p className="input-help">Measurement edits apply after a new scene passes validation. The viewer shows the last validated base scene.</p>}
            <p className="input-help">Unknown dimensions stay unknown. If a measurement cannot be estimated, we’ll ask you to enter it.</p>
            <div className="input-actions"><button type="submit" className="primary-action">{busy === 'proposal' ? 'Building your room…' : scene ? 'Update base scene' : 'Build base scene'}</button></div>
          </fieldset>
        </form>
      </>}
      {busy && <div className="upload-progress" role="status"><progress aria-label={busy === 'analysis' ? 'Analyzing room photos' : 'Building base scene'} /><span>{busy === 'analysis' ? 'Reading the photos together…' : 'Proposing and checking the room geometry…'}</span><button type="button" onClick={controller.cancel}>Cancel</button></div>}
      {state.notice && <div className={`input-notice ${state.notice.kind}`} role={state.notice.kind === 'error' ? 'alert' : 'status'}><p>{state.notice.text}</p></div>}
      {proposal?.status === 'needs_dimensions' && <div className="input-notice" role="status"><strong>A few measurements are still needed</strong><p>Enter and confirm: {proposal.dimensions_to_confirm.map(axis => dimensionLabels[axis].toLowerCase()).join(', ')}. Your photo analysis is saved temporarily; you don’t need to analyze again.</p></div>}
      {proposal?.status === 'incomplete' && <div className="input-notice error" role="alert"><strong>We couldn’t validate a complete base scene</strong><ul>{proposal.issues.map((issue, index) => <li key={index}>{issue.message}{!!issue.entity_ids.length && ` (${issue.entity_ids.join(', ')})`}</li>)}</ul><p>Review the room details. Clearer photos or corrected measurements may be needed.</p></div>}
      {proposal?.estimated_axes.length ? <p className="analysis-caution">Unconfirmed room dimensions: {proposal.estimated_axes.map(axis => dimensionLabels[axis].toLowerCase()).join(', ')}. These remain labeled as estimates.</p> : null}
    </div>
    {scene && <section className="reconstructed-room" aria-label="Your reconstructed room">
      <div className="analysis-card"><span className="section-label">Your approximate room</span><h2>5. Explore your base scene</h2><p>A reconstruction from your photos and dimensions. “Your layout” starts as a copy of the base scene for your own edits.</p>
        <details className="analysis-notes"><summary>Scene estimates, assumptions, and cautions</summary><ul>{[...scene.assumptions, ...scene.warnings].map((note, index) => <li key={index}>{note}</li>)}</ul></details>
      </div>
      <SceneWorkspace initialScene={scene} beforeScene={scene} variant="reconstruction" onConnectionChange={onConnectionChange} />
    </section>}
  </section>
}
