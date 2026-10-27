import { lazy, Suspense, useEffect, useState, useSyncExternalStore } from 'react'
import type { SharedRoom } from '../../lib/feed'
import { apiAsset, feedClient } from '../../lib/feed'
import { SceneEditor } from '../../lib/scene-editor'
import { Button, GlassPanel } from '../ui/primitives'

const RoomViewer = lazy(() => import('../room-viewer/RoomViewer'))
function ReadOnlyRoom({ room }: { room: SharedRoom }) {
  const [editor] = useState(() => { const value = new SceneEditor(room.scene, undefined, room.scene); value.switchView('before'); return value })
  const state = useSyncExternalStore(editor.subscribe, editor.getSnapshot, editor.getSnapshot)
  useEffect(() => () => editor.cancel(false), [editor])
  return <section className="viewer-card public-room-viewer" aria-label="Shared 3D room">
    <p className="public-room-caption">Shared layout · Read-only</p>
    <Suspense fallback={<img className="public-room-fallback" src={apiAsset(room.preview_url)} alt="Shared room preview"/>}>
      <RoomViewer scene={state.scene} editor={editor} state={state} onSelect={() => {}}/>
    </Suspense>
  </section>
}
export default function PublicRoomPage({ id, onCreate }: { id: string; onCreate: () => void }) {
  const [data, setData] = useState<{ id: string; room: SharedRoom } | null>(null), [error, setError] = useState<string | null>(null), [retry, setRetry] = useState(0)
  useEffect(() => {
    const abort = new AbortController(); let sequence = 0
    const load = async () => {
      const current = ++sequence
      try { const room = await feedClient.read(id, abort.signal); if (!abort.signal.aborted && current === sequence) { setData({ id, room }); setError(null) } }
      catch (reason) { if (!abort.signal.aborted && current === sequence) { setData(null); setError((reason as Error).message) } }
    }
    void load(); window.addEventListener('focus', load)
    const timer = setInterval(() => { if (!document.hidden) void load() }, 30000)
    return () => { abort.abort(); clearInterval(timer); window.removeEventListener('focus', load) }
  }, [id, retry])
  const room = data?.id === id ? data.room : null
  return <section className="public-room-page">
    <div className="page-heading"><div><h1>{room?.title ?? 'Shared room'}</h1>{room?.summary && <p>{room.summary}</p>}</div><a className="ui-button" href="#explore">Back to Explore</a></div>
    {error ? <GlassPanel className="library-empty"><p role="alert">{error}</p><Button onClick={() => setRetry(value => value + 1)}>Check again</Button><a className="ui-button" href="#explore">Explore other rooms</a></GlassPanel>
      : room ? <ReadOnlyRoom key={`${id}:${room.published_at}`} room={room}/> : <p role="status">Opening shared room…</p>}
    {room && <div className="public-room-footer"><p>An approximate 3D layout. Source photos and chat are private.</p><Button onClick={onCreate}>Create your own room</Button></div>}
  </section>
}
