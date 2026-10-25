import { useEffect, useState, useSyncExternalStore } from 'react'
import type { AgentState } from '../../lib/agent-run'
import { OptimizationController } from '../../lib/optimization-controller'
import type { ImprovementState } from '../../lib/optimization-controller'
import { improvementLabels } from '../../lib/optimization'
import RunWorkflow from './RunWorkflow'
import { progressLabels } from '../../lib/run-progress'
import RoomAssessmentCards from './RoomAssessmentCards'
import SceneWorkspace from '../room-viewer/SceneWorkspace'

export default function RoomImprovementWorkspace({ run, onConnectionChange }: { run: AgentState; onConnectionChange: (online: boolean) => void }) {
  const [controller] = useState(() => new OptimizationController(run))
  useEffect(() => controller.connect(), [controller])
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot)
  return <>
    <RoomAssessmentCards assessment={run.room_assessment ?? null} onImprove={controller.available() ? controller.select : undefined} disabled={controller.locked()} />
    <ImprovementForm state={state} controller={controller} partial={run.status === 'partial'} />
    {run.working_scene && <SceneWorkspace goals={run.input_options.goals} initialScene={run.working_scene}
      improvement={state.result} objectLabels={Object.fromEntries((run.analysis?.observations.objects ?? []).map(object => [object.id, object.label]))}
      onConnectionChange={onConnectionChange} />}
  </>
}

export function ImprovementForm({ state, controller, partial }: {
  state: ImprovementState; controller: OptimizationController; partial: boolean
}) {
  if (!state.focus) return state.error ? <p className="input-notice error" role="alert">{state.error}</p> : null
  const ready = state.result?.status === 'prepared'
  return <section className="improvement-request" aria-labelledby="improvement-heading">
    <span className="section-label">Your improvement request</span>
    <h2 id="improvement-heading">{improvementLabels[state.focus]}</h2>
    {partial && <p className="analysis-caution">This starts from a partial reconstruction. Missing or uncertain items remain limitations.</p>}
    {!state.result && state.phase !== 'expired' && <form onSubmit={event => { event.preventDefault(); void controller.start() }}>
      <p>Tell us what matters for this improvement. All fields are optional; blank preferences add no spending limit or new style requirement.</p>
      <p>Your original room, measurements, and fixed or locked objects are preserved.</p>
      <fieldset disabled={controller.locked()}>
        <legend>Optional improvement preferences</legend>
        <div className="improvement-budget"><label>Budget (optional)<input type="number" min="0" max="1000000" step="any" placeholder="Not specified" value={state.draft.budget} onChange={event => controller.change({ budget: event.target.value })} /></label>
          <label>Currency<input type="text" maxLength={3} value={state.draft.currency} onChange={event => controller.change({ currency: event.target.value })} disabled={!state.draft.budget.trim()} /></label></div>
        <p className="input-help">A budget of 0 means no new purchases. Prices and availability are not verified yet.</p>
        <label>Style preference (optional)<input maxLength={100} placeholder="For example, warm and minimal" value={state.draft.style} onChange={event => controller.change({ style: event.target.value })} /></label>
        <label>What should we keep or consider? (optional)<textarea maxLength={1000} placeholder="For example, keep the desk near the window" value={state.draft.constraints} onChange={event => controller.change({ constraints: event.target.value })} /></label>
        <div className="input-actions"><button className="primary-action" type="submit">Improve room</button><button type="button" onClick={controller.close}>Back to ratings</button></div>
      </fieldset>
    </form>}
    {state.phase === 'preparing' && <p role="status">Saving your improvement request…</p>}
    {state.phase === 'cancelling' && <p role="status">Cancelling your improvement request…</p>}
    {state.error && <p className="input-notice error" role="alert">{state.error}</p>}
    {state.recovery && <button type="button" onClick={() => void controller.retry()}>Retry the same request</button>}
    {ready && state.phase === 'prepared' && <div className="input-notice success" role="status">
      <p>Your improvement preferences are saved. Your original room is unchanged.</p>
      <button type="button" onClick={() => void controller.execute()}>Run improvement</button>
      <button type="button" onClick={() => void controller.cancel()}>Cancel improvement request</button>
    </div>}
    {(state.phase === 'running' || state.phase === 'cancelling') && state.result && <div role="status">
      <RunWorkflow kind="optimization" run={state.result} progress={state.progress} streamMode={state.streamMode} message={state.phase === 'cancelling' ? 'Stopping further improvement work…' : state.progress && state.streamMode === 'streaming' ? progressLabels[state.progress.stage] : state.result.stage === 'reviewing' ? 'Reviewing the proposed layout and your preferences…' : state.result.stage === 'validating' ? 'Checking placement, walking space and goal scores…' : 'Exploring a better arrangement for your chosen focus…'}/>
      <p>{state.result.attempts.length} candidates checked · up to {state.result.max_candidates} candidates.</p>
      <button type="button" disabled={state.phase === 'cancelling'} onClick={() => void controller.cancel()}>Cancel improvement</button>
    </div>}
    {state.result && !['prepared', 'running', 'cancelled'].includes(state.result.status) && <div className="improvement-outcome" role="status">
      <h3>{state.result.result_scene ? 'A validated candidate was retained' : 'Your original room is retained'}</h3>
      <p>{state.result.message}</p>
      <p>{state.result.usage.model_calls} model calls · {state.result.attempts.length} candidates checked.</p>
      {state.result.result_scene && <p>Compare Original and Improved below. Edit a copy to try your own changes; both saved scenes stay read-only.</p>}
      {state.result.suggestions.length > 0 && <><h4>Suggestions to consider</h4><ul>{state.result.suggestions.map((note, i) => <li key={i}>{note}</li>)}</ul></>}
      <details><summary>Improvement checks and limitations</summary><ul>{state.result.attempts.map(attempt => <li key={attempt.number}>
        Candidate {attempt.number}: {attempt.outcome === 'accepted' ? 'retained' : 'not applied'}. {attempt.before_score !== null && attempt.after_score !== null && <>Focus estimate: {attempt.before_score} → {attempt.after_score} / 10. </>}
        {attempt.review?.explanation} {attempt.issues.join(' ')}
      </li>)}</ul><ul>{state.result.limitations.map((note, i) => <li key={i}>{note}</li>)}</ul></details>
    </div>}
    {state.phase === 'cancelled' && <p role="status">Improvement request cancelled. You can choose another focus above.</p>}
  </section>
}
