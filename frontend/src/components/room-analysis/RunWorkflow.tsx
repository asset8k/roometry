import type { AgentState } from '../../lib/agent-run'
import type { OptimizationRun } from '../../lib/optimization'
import type { RunProgress, StreamMode } from '../../lib/run-progress'
import { Icon } from '../ui/primitives'

type State = 'waiting' | 'active' | 'done' | 'limited'
export default function RunWorkflow({ kind, run, progress, streamMode, message, paused = false }: {
  kind: 'reconstruction' | 'optimization'; run: AgentState | OptimizationRun | null
  progress?: RunProgress; streamMode?: StreamMode; message: string; paused?: boolean
}) {
  const live = streamMode === 'streaming' ? progress : undefined
  let labels: string[], states: State[]
  if (kind === 'reconstruction') {
    const source = run as AgentState | null
    labels = ['Photo Analysis', 'Scene Construction', 'Geometry Checks', 'Visual Review', 'Room Ratings']
    const complete = [!!source?.analysis, !!source?.working_scene, !!source?.proposal?.validation?.valid,
      !!source?.proposal?.visual_review && source.proposal.visual_review.status !== 'unavailable', !!source?.room_assessment]
    const stage = live?.stage
    const active = stage === 'assessing' ? 4 : stage === 'reviewing' ? 3 : stage === 'checking' ? 2 : stage === 'constructing' ? 1 : complete.findIndex(done => !done)
    states = complete.map((done, index) => done ? 'done' : index === active ? 'active' : 'waiting')
    if (source?.proposal?.visual_review?.status === 'unavailable') states[3] = 'limited'
  } else {
    const source = run as OptimizationRun | null
    labels = ['Layout Proposal', 'Geometry Checks', 'Visual Review']
    const stage = live?.stage ?? source?.stage
    const active = stage === 'reviewing' ? 2 : stage === 'checking' || stage === 'validating' ? 1 : 0
    states = labels.map((_, index) => index < active ? 'done' : index === active ? 'active' : 'waiting')
  }
  return <div className="run-workflow" data-kind={kind} data-paused={paused}>
    <ol aria-label={kind === 'reconstruction' ? 'Room generation stages' : 'Improvement stages'}>
      {labels.map((label, index) => <li key={label} data-state={states[index]} aria-current={states[index] === 'active' ? 'step' : undefined}>
        <span className="workflow-node">{states[index] === 'done' ? <Icon name="check"/> : <span className="workflow-dot" aria-hidden="true"/>}<span>{label}</span>
          <span className="visually-hidden"> · {states[index] === 'done' ? 'complete' : states[index] === 'limited' ? 'unavailable' : states[index] === 'active' ? paused ? 'waiting for your answer' : 'in progress' : 'waiting'}</span>
        </span>{index < labels.length - 1 && <Icon name="arrow" className="workflow-arrow"/>}
      </li>)}
    </ol>
    <p className="workflow-message" role="status" hidden={paused}>{message}</p>
    {streamMode === 'reconnecting' && <p className="workflow-reconnecting" role="status">Reconnecting… your room keeps running.</p>}
    {!paused && <progress className="workflow-indicator" aria-label={kind === 'reconstruction' ? 'Room generation progress' : 'Room improvement progress'}/>}
  </div>
}
