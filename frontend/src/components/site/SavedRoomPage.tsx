import { useEffect, useState, useSyncExternalStore } from 'react'
import type { AccountUser } from '../../lib/auth'
import { projectsClient, projectRequest } from '../../lib/projects'
import type { OpenedProject } from '../../lib/projects'
import { agentClient } from '../../lib/agent-api'
import { parseAgentRun } from '../../lib/agent-run'
import { parsePreparedInput } from '../../lib/room-input'
import { RoomInputController } from '../../lib/room-input-controller'
import { RoomGenerationController } from '../../lib/room-generation-controller'
import RoomGenerationWorkspace from '../room-analysis/RoomGenerationWorkspace'
import RoomImprovementWorkspace from '../room-analysis/RoomImprovementWorkspace'
import { Button, GlassPanel } from '../ui/primitives'
import { useWorkspaceGuard } from './WorkspaceNavigation'

function RecoveredRun({ data, onConnectionChange }: { data: { run: unknown; input: unknown; request_id?: string }; onConnectionChange: (online: boolean) => void }) {
  const [controller] = useState(() => {
    const inputs = new RoomInputController(), generation = new RoomGenerationController(inputs)
    generation.restore(parsePreparedInput(data.input), parseAgentRun(data.run)); return generation
  })
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot)
  const active = ['starting', 'running', 'awaiting_answer'].includes(state.phase) || state.cancelling || state.recovery
  useWorkspaceGuard({ dirty: !state.run?.working_scene || active, active, hasWork: true, priority: 20, leave: controller.leaveWorkspace })
  useEffect(controller.connect, [controller])
  const [starting, setStarting] = useState(false), [error, setError] = useState<string | null>(null)
  return <>{state.phase === 'starting' && data.request_id && <Button disabled={starting} onClick={() => {
    setStarting(true); setError(null)
    const input = parsePreparedInput(data.input)
    void agentClient.start({ input_id: input.input_id, request_id: data.request_id! }, AbortSignal.timeout(1800000))
      .then(run => controller.restore(input, run)).catch(reason => setError((reason as Error).message)).finally(() => setStarting(false))
  }}>{starting ? 'Continuing…' : 'Continue reconstruction'}</Button>}{error && <p role="alert">{error}</p>}<RoomGenerationWorkspace state={state} controller={controller} onConnectionChange={onConnectionChange}/></>
}
export default function SavedRoomPage({ id, kind, user, onSignIn, onConnectionChange }: {
  id: string; kind: 'saved' | 'recover'; user: AccountUser | null; onSignIn: () => void; onConnectionChange: (online: boolean) => void
}) {
  const [loaded, setLoaded] = useState<{ owner: string; id: string; kind: string; value: OpenedProject | { run: unknown; input: unknown } } | null>(null)
  const [error, setError] = useState<string | null>(null), [retry, setRetry] = useState(0)
  useEffect(() => {
    setError(null)
    if (!user) return
    const abort = new AbortController()
    const request = kind === 'saved' ? projectsClient.open(id, abort.signal) : projectRequest(`/runs/${encodeURIComponent(id)}`, abort.signal)
    void request.then(value => { if (!abort.signal.aborted) setLoaded({ owner: user.id, id, kind, value: value as OpenedProject }) })
      .catch(reason => { if (!abort.signal.aborted) setError(reason instanceof Error ? reason.message : 'This room is unavailable.') })
    return () => abort.abort()
  }, [user?.id, id, kind, retry])
  if (!user) return <GlassPanel className="library-empty"><h2>Sign in to open your room.</h2><Button variant="primary" onClick={onSignIn}>Sign in</Button></GlassPanel>
  const current = loaded?.owner === user.id && loaded.id === id && loaded.kind === kind ? loaded.value : null
  return <section className="saved-room-page">
    <div className="page-heading"><h1>{kind === 'saved' && current ? (current as OpenedProject).project.title : 'Your room'}</h1><a className="ui-button" href="#my-rooms">My rooms</a></div>
    {kind === 'saved' && current && <details className="source-inputs"><summary>Room photos</summary><ul className="photo-grid">{(current as OpenedProject).project.images.map((image, index) => <li key={image.id}><div className="photo-preview"><img loading="lazy" src={`${(import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/+$/, '')}/projects/${id}/images/${image.id}`} crossOrigin="use-credentials" alt={`Saved room photo ${index + 1}`}/></div></li>)}</ul></details>}
    {error ? <GlassPanel className="library-empty"><p role="alert">{error}</p><Button onClick={() => setRetry(value => value + 1)}>Retry opening</Button></GlassPanel>
      : current ? kind === 'saved' ? <RoomImprovementWorkspace key={`${user.id}-${id}-${retry}`} run={(current as OpenedProject).run} restored={current as OpenedProject} onConnectionChange={onConnectionChange}/>
        : <RecoveredRun key={`${user.id}-${id}-${retry}`} data={current as { run: unknown; input: unknown }} onConnectionChange={onConnectionChange}/>
      : <p role="status">Opening your room…</p>}
  </section>
}
