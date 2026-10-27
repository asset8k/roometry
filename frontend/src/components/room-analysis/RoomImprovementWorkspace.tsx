import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import type { OpenedProject } from '../../lib/projects'
import { savedWorkspaceSignature } from '../../lib/projects'
import { useWorkspaceGuard } from '../site/WorkspaceNavigation'
import type { AgentState } from '../../lib/agent-run'
import { OptimizationController } from '../../lib/optimization-controller'
import type { ImprovementState } from '../../lib/optimization-controller'
import { improvementLabels } from '../../lib/optimization'
import RunWorkflow from './RunWorkflow'
import SaveRoomControl from '../site/SaveRoomControl'
import { progressLabels } from '../../lib/run-progress'
import RoomAssessmentCards from './RoomAssessmentCards'
import SceneWorkspace from '../room-viewer/SceneWorkspace'
import { SceneComparison } from '../../lib/scene-comparison'
import { retainedImprovement } from '../../lib/scene-comparison'
import { Button, Icon } from '../ui/primitives'
import { useSurfaceMotion } from '../ui/MotionRegion'
import { conciseText, readableText } from '../../lib/display-text'
import { useAllowance } from '../../lib/quota'
import AllowanceNotice from './AllowanceNotice'
import { useSiteActions } from '../site/SiteActions'

export default function RoomImprovementWorkspace({ run, onConnectionChange, restored }: { run: AgentState; onConnectionChange: (online: boolean) => void; restored?: OpenedProject }) {
  const [controller] = useState(() => new OptimizationController(run))
  const [comparison] = useState(() => restored ? SceneComparison.restore(restored) : new SceneComparison(run.working_scene!))
  const [messages, setMessages] = useState<{ id: string; role: 'user' | 'assistant'; text: string }[]>(restored?.project.snapshot.messages ?? [])
  const [savedSignature, setSavedSignature] = useState(() => restored ? savedWorkspaceSignature(run.run_id, comparison.snapshotForSave(), restored.project.snapshot.messages) : null)
  const [draft, setDraft] = useState('')
  const recorded = useRef(new Set<string>())
  const transcript = useRef<HTMLDivElement>(null)
  useEffect(() => controller.connect(), [controller])
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot)
  const allowance = useAllowance(run.run_id, `${useSiteActions()?.userId}:${state.phase}`)
  const actionsUsed = allowance?.enabled && allowance.actions_remaining === 0
  const views = useSyncExternalStore(comparison.subscribe, comparison.getSnapshot, comparison.getSnapshot)
  const editor = useSyncExternalStore(views.editor.subscribe, views.editor.getSnapshot, views.editor.getSnapshot)
  useEffect(() => {
    if (state.result) comparison.offer(state.result, state.result.source_scene)
    const result = state.result
    if (!result?.instruction || ['prepared', 'running'].includes(result.status) || recorded.current.has(result.run_id)) return
    recorded.current.add(result.run_id)
    const accepted = [...result.attempts].reverse().find(attempt => attempt.outcome === 'accepted')
    setMessages(history => [...history, { id: result.run_id, role: 'assistant', text: retainedImprovement(result)
      ? `${accepted?.summary ?? 'A validated refinement is available.'} Compare it with Original or edit a copy.`
      : `${result.message} ${result.suggestions[0] ?? 'Your previous layout is kept.'}` }])
  }, [state.result, comparison])
  useEffect(() => { transcript.current?.scrollTo({ top: transcript.current.scrollHeight, behavior: 'auto' }) }, [messages.length])
  const busy = controller.locked() || editor.busy || editor.dragging
  let signature: string | null = null
  try { signature = savedWorkspaceSignature(run.run_id, comparison.snapshotForSave(), messages) } catch { /* Unfinished edits are dirty until accepted or cancelled. */ }
  const active = ['preparing', 'prepared', 'running', 'cancelling'].includes(state.phase) || state.recovery || !!state.result && ['prepared', 'running'].includes(state.result.status)
  const unsentPreferences = state.phase === 'editing' && (!!state.draft.style.trim() || !!state.draft.constraints.trim())
  useWorkspaceGuard({ dirty: !savedSignature || signature !== savedSignature || !!draft.trim() || unsentPreferences || editor.busy || editor.dragging,
    active, hasWork: true, priority: 10, leave: controller.leaveWorkspace })
  const overview = <>
    <AllowanceNotice allowance={allowance} room />
    <RoomAssessmentCards assessment={run.room_assessment ?? null} onImprove={controller.available() ? controller.select : undefined} disabled={busy || actionsUsed} />
    {!state.instruction && <ImprovementForm state={state} controller={controller} partial={run.status === 'partial'}
      beforeStart={() => controller.setContext(comparison.currentContext())} />}
  </>
  const chat = <section className="scene-chat" aria-label="Room chat">
    <h3>Refine your room</h3><p className="input-help">Starts from the visible layout, including edits and locks.</p>
    <AllowanceNotice allowance={allowance} room />
    <div ref={transcript} className="chat-transcript" role="log" aria-label="Refinement conversation" aria-relevant="additions text">
      {messages.length === 0 && <p className="chat-empty">Try “Move the desk closer to the window” or “Use warmer colors”.</p>}
      {messages.map(message => <article key={message.id} className={`chat-message chat-message--${message.role}`}><span>{message.role === 'user' ? 'You' : 'Roometry'}</span><p>{message.role === 'assistant' ? readableText(message.text) : message.text}</p></article>)}
    </div>
    {state.instruction && <ImprovementForm state={state} controller={controller} partial={run.status === 'partial'} compact />}
    {state.error && !state.instruction && <p className="input-notice error" role="alert">{state.error}</p>}
    <form className="chat-composer" onSubmit={event => {
      event.preventDefault()
      if (busy || actionsUsed || !draft.trim() || !controller.available()) return
      const instruction = draft.trim(), context = comparison.currentContext()
      setMessages(history => [...history, { id: crypto.randomUUID(), role: 'user', text: instruction }])
      setDraft('')
      void controller.refine(instruction, context)
    }}>
      <label htmlFor="room-refinement">Your request</label>
      <textarea id="room-refinement" value={draft} maxLength={1000} rows={3} disabled={busy || actionsUsed || !controller.available()}
        placeholder="What would you like to change?" onChange={event => setDraft(event.target.value)} />
      <Button variant="primary" type="submit" disabled={busy || actionsUsed || !draft.trim() || !controller.available()}>Refine room<Icon name="arrow"/></Button>
    </form>
  </section>
  return run.working_scene && <SceneWorkspace sourceRunId={run.run_id} goals={run.input_options.goals} initialScene={run.working_scene}
    comparison={comparison} saveAction={<SaveRoomControl run={run} comparison={comparison} messages={messages} initial={restored ? { id: restored.project.id, title: restored.project.title, revision: restored.project.revision,
      image_count: restored.project.images.length, version_count: restored.project.versions.length, visibility: 'private', saved_at: run.created_at } : undefined} onSaved={setSavedSignature} disabled={editor.busy || editor.dragging || ['preparing', 'prepared', 'running', 'cancelling'].includes(state.phase) || state.recovery} />} overview={overview} chat={chat} improvement={state.result}
    objectLabels={Object.fromEntries((run.analysis?.observations.objects ?? []).map(object => [object.id, object.label]))}
    onConnectionChange={onConnectionChange} />
}

export function ImprovementForm({ state, controller, partial, beforeStart, compact = false }: {
  state: ImprovementState; controller: OptimizationController; partial: boolean; beforeStart?: () => void; compact?: boolean
}) {
  const form = useRef<HTMLElement>(null)
  const phase = ['preparing', 'prepared', 'running', 'cancelling'].includes(state.phase) ? 'working' : state.phase === 'editing' ? 'request' : 'result'
  useSurfaceMotion(form, `${state.focus}:${phase}`)
  useEffect(() => {
    if (state.phase === 'editing') {
      form.current?.scrollIntoView({ block: 'nearest' })
      form.current?.focus({ preventScroll: true })
    }
  }, [state.focus, state.phase])
  if (!state.focus) return state.error ? <p className="input-notice error" role="alert">{state.error}</p> : null
  const ready = state.result?.status === 'prepared'
  return <section ref={form} tabIndex={-1} className="improvement-request" aria-labelledby={compact ? undefined : 'improvement-heading'} aria-label={compact ? 'Refinement status' : undefined}>
    {!compact && <h2 id="improvement-heading">{state.instruction ? 'Room refinement' : improvementLabels[state.focus]}</h2>}
    {partial && <p className="analysis-caution">Partial reconstruction · Some items remain unresolved.</p>}
    {!compact && !state.result && state.phase !== 'expired' && <form onSubmit={event => { event.preventDefault(); beforeStart?.(); void controller.start() }}>
      <p className="input-help">Starts from the visible layout. Preferences are optional.</p>
      <fieldset disabled={controller.locked()}>
        <legend>Optional improvement preferences</legend>
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
      <RunWorkflow kind="optimization" run={state.result} progress={state.progress} streamMode={state.streamMode} message={state.phase === 'cancelling' ? 'Stopping further improvement work…' : state.progress && state.streamMode === 'streaming' ? progressLabels[state.progress.stage] : state.result.stage === 'reviewing' ? 'Reviewing the proposed layout and your preferences…' : state.result.stage === 'validating' ? 'Checking placement, walking space and goal scores…' : compact ? 'Working on your request…' : 'Exploring a better arrangement for your chosen focus…'}/>
      <p>{state.result.attempts.length} candidates checked · up to {state.result.max_candidates} candidates.</p>
      <Button variant="quiet" className="refinement-cancel" disabled={state.phase === 'cancelling'} onClick={() => void controller.cancel()}>{compact ? 'Cancel refinement' : 'Cancel improvement'}</Button>
    </div>}
    {state.result && !['prepared', 'running', 'cancelled'].includes(state.result.status) && <div className="improvement-outcome" role="status">
      {!compact && <h3>{state.result.result_scene ? 'Candidate ready' : 'Previous layout kept'}</h3>}
      {!compact && <p>{conciseText(state.result.message)}</p>}


      {!compact && state.result.suggestions.length > 0 && <><h4>Suggestions to consider</h4><ul>{state.result.suggestions.slice(0, 3).map((note, i) => <li key={i}>{conciseText(note)}</li>)}</ul></>}
      <details><summary>Checks and limitations</summary><p>{state.result.usage.model_calls} model calls · {state.result.attempts.length} candidates checked.</p><ul>{state.result.attempts.map(attempt => <li key={attempt.number}>
        Candidate {attempt.number}: {attempt.outcome === 'accepted' ? 'retained' : 'not applied'}. {attempt.before_score !== null && attempt.after_score !== null && <>Focus estimate: {attempt.before_score} → {attempt.after_score} / 10. </>}
        {attempt.review?.explanation && conciseText(attempt.review.explanation)} {conciseText(attempt.issues.join(' '))}
      </li>)}</ul><ul>{state.result.limitations.map((note, i) => <li key={i}>{conciseText(note)}</li>)}</ul></details>
    </div>}
    {state.phase === 'cancelled' && <p role="status">{compact ? 'Refinement cancelled. Your previous layout is kept.' : 'Improvement request cancelled. You can choose another focus above.'}</p>}
  </section>
}
