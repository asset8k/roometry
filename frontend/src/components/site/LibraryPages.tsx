import { useEffect, useState } from 'react'
import { Button, GlassPanel, Icon } from '../ui/primitives'
import Dialog from '../ui/Dialog'
import type { AccountUser } from '../../lib/auth'
import { projectsClient, recoverableRuns } from '../../lib/projects'
import type { ProjectSummary, RecentRun } from '../../lib/projects'

export function MyRooms({ onSignIn, onCreate, user }: { onSignIn: () => void; onCreate: () => void; user?: AccountUser | null }) {
  const [data, setData] = useState<{ owner: string; rooms: ProjectSummary[]; runs: RecentRun[] } | null>(null)
  const [error, setError] = useState<string | null>(null), [refresh, setRefresh] = useState(0)
  const [action, setAction] = useState<{ kind: 'rename' | 'delete'; room: ProjectSummary } | null>(null)
  const [title, setTitle] = useState(''), [busy, setBusy] = useState(false), [more, setMore] = useState(false)
  useEffect(() => {
    setError(null); setAction(null)
    if (!user) return
    const abort = new AbortController()
    void Promise.all([projectsClient.list(abort.signal), projectsClient.recent(abort.signal)]).then(([rooms, runs]) => {
      if (!abort.signal.aborted) { setData({ owner: user.id, rooms, runs }); setMore(rooms.length === 50) }
    }).catch(reason => { if (!abort.signal.aborted) setError(reason instanceof Error ? reason.message : 'Couldn’t load rooms.') })
    return () => abort.abort()
  }, [user?.id, refresh])
  const own = user && data?.owner === user.id ? data : null
  const unsaved = own ? recoverableRuns(own.runs) : []
  const runLabels: Record<string, string> = { awaiting_answer: 'Answer needed', completed: 'Ready to save', running: 'In progress', queued: 'In progress', partial: 'Partial reconstruction', cancelled: 'Cancelled', failed: 'Stopped' }
  return <section className="library-page" aria-labelledby="my-rooms-title">
    <div className="page-heading"><div><h1 id="my-rooms-title">My rooms</h1><p>Private rooms and recoverable work.</p></div><Button onClick={onCreate}>New room<Icon name="arrow"/></Button></div>
    {!user ? <GlassPanel className="library-empty"><Icon name="folder"/><h2>Keep the rooms you create.</h2><p>Sign in to your Roometry account.</p><Button variant="primary" onClick={onSignIn}>Sign in</Button></GlassPanel>
      : <>
        {error && <div className="input-notice error" role="alert"><p>{error}</p><Button onClick={() => setRefresh(value => value + 1)}>Retry loading</Button></div>}
        {!own && !error && <p role="status">Loading your rooms…</p>}
        {own && !own.rooms.length && !unsaved.length && <GlassPanel className="library-empty"><Icon name="folder"/><h2>Your rooms will live here.</h2><p>Create a room, then save it privately.</p><Button variant="primary" onClick={onCreate}>Create a room</Button></GlassPanel>}
        {own && <div className="private-room-grid">{own.rooms.map(room => <GlassPanel key={room.id} className="private-room-card">
          <span className="section-label">Private · {room.room_type.replaceAll('_', ' ')}</span><h2>{room.title}</h2>
          <div className="input-actions"><a className="ui-button ui-button--primary" href={`#room/${room.id}`}>Open room<Icon name="arrow"/></a>
            <Button onClick={() => { setTitle(room.title); setAction({ kind: 'rename', room }) }}>Rename</Button>
            <Button onClick={() => setAction({ kind: 'delete', room })}>Delete</Button></div>
        </GlassPanel>)}</div>}
        {own && more && <Button disabled={busy} onClick={() => {
          setBusy(true)
          void projectsClient.list(AbortSignal.timeout(20000), own.rooms.length).then(rooms => {
            setData(previous => previous?.owner === user.id ? { ...previous, rooms: [...previous.rooms, ...rooms] } : previous); setMore(rooms.length === 50)
          }).catch(reason => setError((reason as Error).message)).finally(() => setBusy(false))
        }}>Load more rooms</Button>}
        {own && unsaved.length > 0 && <><h2 className="recovery-heading">Unsaved rooms</h2><div className="private-room-grid">{unsaved.map(run => <GlassPanel key={run.id} className="private-room-card"><span className="section-label">{runLabels[run.status] ?? 'Reconstruction'}</span><h3>Your {run.room_type.replaceAll('_', ' ')}</h3><a className="ui-button" href={`#run/${run.id}`}>{run.status === 'awaiting_answer' ? 'Answer question' : ['completed', 'partial'].includes(run.status) ? 'Open room' : 'Continue reconstruction'}<Icon name="arrow"/></a></GlassPanel>)}</div></>}
      </>}
    {action && own && <Dialog title={action.kind === 'rename' ? 'Rename room' : 'Delete room?'} onClose={() => { if (!busy) setAction(null) }} dismissible={!busy}>{dismiss => <>
      <form className="account-form" onSubmit={event => {
        event.preventDefault(); if (busy) return; setBusy(true); setError(null)
        const operation = action.kind === 'rename' ? projectsClient.rename(action.room, title.trim(), AbortSignal.timeout(20000)) : projectsClient.delete(action.room, AbortSignal.timeout(20000))
        void operation.then(() => { dismiss(() => { setAction(null); setRefresh(value => value + 1) }) }).catch(reason => { dismiss(() => { setAction(null); setError((reason as Error).message) }) }).finally(() => setBusy(false))
      }}>
        {action.kind === 'rename' ? <label>Room name<input data-initial-focus required maxLength={120} value={title} disabled={busy} onChange={event => setTitle(event.target.value)}/></label> : <p>Delete “{action.room.title}”, its photos and saved history? Any shared layout will also leave Explore. This can’t be undone.</p>}
        <Button variant="primary" type="submit" disabled={busy || (action.kind === 'rename' && !title.trim())}>{busy ? action.kind === 'delete' ? 'Deleting…' : 'Saving…' : action.kind === 'rename' ? 'Save name' : 'Delete room'}</Button>
        <Button disabled={busy} onClick={() => dismiss()}>{action.kind === 'delete' ? 'Keep room' : 'Cancel'}</Button>
      </form>
    </>}</Dialog>}
  </section>
}

export { default as Explore } from './ExplorePage'
