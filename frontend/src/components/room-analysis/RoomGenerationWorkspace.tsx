import { useEffect, useRef, useState } from 'react'
import SceneWorkspace from '../room-viewer/SceneWorkspace'
import { dimensionLabels } from '../../lib/room-analysis'
import type { DimensionAxis } from '../../lib/room-analysis'
import { generationProgress } from '../../lib/room-generation-controller'
import type { GenerationState, RoomGenerationController } from '../../lib/room-generation-controller'
import type { AgentState, ClarificationAnswer } from '../../lib/agent-run'

const titles: Partial<Record<GenerationState['phase'], string>> = {
  completed: 'Your reconstructed room', partial: 'Your partial reconstruction',
  failed: 'Generation stopped', incomplete: 'Reconstruction could not finish',
  cancelled: 'Generation cancelled', expired: 'This generation has expired',
  budget_exhausted: 'Generation reached its limit',
}

export default function RoomGenerationWorkspace({ state, controller, onConnectionChange }: {
  state: GenerationState; controller: RoomGenerationController; onConnectionChange: (online: boolean) => void
}) {
  const run = state.run
  const card = useRef<HTMLHeadingElement>(null)
  useEffect(() => {
    if (state.phase === 'awaiting_answer' || state.phase === 'completed' || state.phase === 'partial') {
      card.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      card.current?.focus({ preventScroll: true })
    }
  }, [state.phase, run?.pending_question?.id])
  if (state.phase === 'idle' || state.phase === 'uploading') return null
  const terminal = !['uploading', 'starting', 'running', 'awaiting_answer'].includes(state.phase)
  const scene = terminal ? run?.working_scene : null
  const partial = run?.proposal?.status === 'partial'
  const omittedCount = run?.proposal?.unresolved_entities?.length ?? 0
  const needsDimensions = state.phase === 'incomplete' && run?.proposal?.status === 'needs_dimensions'
  const preview = partial && omittedCount === 0
  const objects = run?.analysis?.observations.objects ?? []
  const entityName = (id: string) => objects.find(obj => obj.id === id)?.label ?? run?.analysis?.observations.walls.find(wall => wall.id === id)?.label ?? id.replaceAll('_', ' ')
  const estimated = run?.proposal?.estimated_axes ?? []
  return <section className="analysis-workspace generation-workspace" aria-label="Room generation">
    <div className="analysis-card" aria-live="polite">
      <span className="section-label">From photos to your room</span>
      <h2 ref={card} tabIndex={-1}>{needsDimensions ? 'Room measurements are still needed' : preview ? 'Your reconstruction preview' : titles[state.phase] ?? 'Creating your room'}</h2>
      {!terminal && <div className="upload-progress" role="status">
        {state.phase !== 'awaiting_answer' && <progress aria-label="Room generation progress" />}
        <span>{generationProgress(state)}</span>
      </div>}
      {!terminal && <p className="input-help">We’ll ask if a missing detail matters. Your entered measurements are used directly.</p>}
      {state.phase === 'completed' && <p>Your approximate reconstruction is ready to explore and edit. This shows your current room; you can try your own arrangement in the editor.</p>}
      {run?.stop_reason === 'budget_limit' && ['completed', 'partial'].includes(state.phase) && <p className="input-help">Your validated reconstruction is available. Additional checks stopped at the run limit.</p>}
      {state.phase === 'partial' && <p>{preview ? 'Your scene is available to explore and edit. Some photo relationships still need correction, so this is a provisional reconstruction.' : 'The included geometry passed validation. Some observations remain unresolved and are listed below.'}</p>}
      {state.phase === 'incomplete' && <p>{needsDimensions ? 'Some room measurements remain unknown. Your selected photos and entered details are kept.' : 'We couldn’t build a usable arrangement from this attempt. Your selected photos, entered details, and available photo analysis are kept.'}</p>}
      {state.phase === 'failed' && <p>The service couldn’t finish this generation. Your selected photos and entered details are kept.</p>}
      {state.phase === 'budget_exhausted' && <p>The run stopped at its work limit. Any validated geometry is available below.</p>}
      {state.phase === 'cancelled' && <p>No further generation work will be started. Your selected photos are kept.</p>}
      {state.phase === 'expired' && <p>The temporary run is unavailable. You can generate again from your selected photos.</p>}
      {state.notice && <div className={`input-notice ${state.notice.kind}`} role={state.notice.kind === 'error' ? 'alert' : 'status'}><p>{state.notice.text}</p></div>}
      {state.recovery && <div className="input-actions"><button type="button" onClick={() => void controller.retry()}>{state.answering ? 'Retry the same answer' : 'Check status'}</button><span className="input-help">Reconnect to this generation without starting another one.</span></div>}
      {state.phase === 'awaiting_answer' && run?.pending_question && <ClarificationForm key={`${run.run_id}-${run.pending_question.id}`} run={run}
        disabled={state.answering || state.cancelling || state.recovery} onAnswer={controller.answer} />}
      {terminal && run?.proposal?.status === 'incomplete' && <details className="analysis-notes"><summary>Construction details</summary><ul>{run.proposal.issues.map((issue, index) => <li key={index}>{issue.entity_ids.length > 0 && <strong>{issue.entity_ids.map(entityName).join(', ')}: </strong>}{issue.message}</li>)}</ul></details>}
      {terminal && estimated.length > 0 && <p className="analysis-caution">Estimated room dimensions: {estimated.map(axis => dimensionLabels[axis].toLowerCase()).join(', ')}. These have not been supplied by you.</p>}
      {run?.analysis && <details className="analysis-notes"><summary>Photo observations and uncertainties</summary>
        <ul>{objects.map(obj => <li key={obj.id}><strong>{obj.label}</strong>{obj.relative_location ? ` · ${obj.relative_location}` : ''}</li>)}</ul>
        <ul>{run.analysis.observations.unknowns.map((note, index) => <li key={index}>{note}</li>)}</ul>
      </details>}
      {scene && <>
        {run?.proposal?.fidelity && <details className="analysis-notes" open={run.proposal.fidelity.recognizable === false}>
          <summary>Reconstruction quality</summary>
          <p>{run.proposal.fidelity.recognizable === true ? 'The major arrangement is consistent with the recorded photo evidence.' : run.proposal.fidelity.recognizable === false ? 'The major arrangement is not yet established. Review the missing or uncertain items below.' : 'Layout fidelity could not be established from the available evidence.'} This is an approximate model.</p>
          <p>Major furniture retained: {Math.round(run.proposal.fidelity.major_object_coverage * 100)}%.</p>
          <ul>{run.proposal.fidelity.checks.filter(check => !check.passed).map((check, i) => <li key={i}>{check.entity_ids.map(entityName).join(', ')}: {check.message}</li>)}</ul>
          <ul>{run.proposal.fidelity.unassessed.map((note, i) => <li key={i}>{note}</li>)}</ul>
        </details>}
        {(scene.estimate_corrections?.length ?? 0) > 0 && <details className="analysis-notes"><summary>Revised photo estimates</summary><ul>{scene.estimate_corrections?.map((c, i) => <li key={i}>{entityName(c.entity_id)} · {c.field}: {c.previous_m} → {c.revised_m} m. {c.reason}</li>)}</ul></details>}
        {partial && omittedCount > 0 && <div className="analysis-caution" role="status"><h3>Some observations still need resolving</h3>
          <p>{run.proposal?.unresolved_entities?.length} observations are unresolved and absent from the 3D scene.</p>
          <ul>{run.proposal?.unresolved_entities?.map(item => <li key={item.entity_id}><strong>{entityName(item.entity_id)}</strong>: {item.reason.message}</li>)}</ul>
        </div>}
        {state.phase !== 'completed' && state.phase !== 'partial' && <p className="analysis-caution">This run did not finish successfully. The viewer contains only the geometry validated before it stopped.</p>}
        <details className="analysis-notes"><summary>Scene estimates, assumptions, and cautions</summary><ul>{[...scene.assumptions, ...scene.warnings].map((note, index) => <li key={index}>{note}</li>)}</ul></details>
      </>}
    </div>
    {scene && run?.base_scene && <section className="reconstructed-room" aria-label="Your reconstructed room">
      <SceneWorkspace initialScene={scene} beforeScene={run.base_scene} onConnectionChange={onConnectionChange} />
    </section>}
  </section>
}

export function ClarificationForm({ run, disabled, onAnswer }: {
  run: AgentState; disabled: boolean; onAnswer: (answer: ClarificationAnswer) => Promise<void>
}) {
  const question = run.pending_question!
  const [choice, setChoice] = useState('')
  const [values, setValues] = useState<Partial<Record<DimensionAxis, string>>>({})
  const [error, setError] = useState<string | null>(null)
  const submit = () => {
    if (disabled || !choice) return
    const answer: ClarificationAnswer = { question_id: question.id, choice_id: choice }
    if (choice === 'enter_measurements') {
      const dimensions: Partial<Record<DimensionAxis, number>> = {}
      for (const axis of question.dimension_axes) {
        if (!values[axis]?.trim()) continue
        const value = Number(values[axis])
        if (!Number.isFinite(value) || value <= 0 || value > (axis === 'height_m' ? 10 : 30)) { setError('Enter positive measurements within the indicated limits.'); return }
        dimensions[axis] = value
      }
      if (!Object.keys(dimensions).length) { setError('Enter at least one requested measurement, or choose to leave them unknown.'); return }
      answer.dimensions = dimensions
    }
    setError(null)
    void onAnswer(answer)
  }
  return <form className="clarification-form" onSubmit={event => { event.preventDefault(); submit() }}>
    <fieldset disabled={disabled}>
      <legend><span className="section-label">Question {run.questions.length} of up to 3</span><strong>{question.question}</strong></legend>
      <div className="clarification-choices">{question.choices.map(option => <label key={option.id}>
        <input type="radio" name={`answer-${question.id}`} value={option.id} checked={choice === option.id} onChange={() => setChoice(option.id)} required /><span>{option.label}</span>
      </label>)}</div>
      {choice === 'enter_measurements' && <div className="room-preferences"><div className="dimension-inputs">{question.dimension_axes.map(axis => <label key={axis}>
        {dimensionLabels[axis]} (m)<input type="number" inputMode="decimal" min="0" max={axis === 'height_m' ? 10 : 30} step="any" value={values[axis] ?? ''}
          onChange={event => setValues(current => ({ ...current, [axis]: event.target.value }))} />
      </label>)}</div><p className="input-help">Leave anything you don’t know blank. We’ll only ask about measurements that are still missing.</p></div>}
      {error && <p className="input-notice error" role="alert">{error}</p>}
      <div className="input-actions"><button className="primary-action" type="submit" disabled={!choice}>{disabled ? 'Resuming your generation…' : 'Continue'}</button></div>
    </fieldset>
  </form>
}
