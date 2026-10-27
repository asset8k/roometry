import { useEffect, useRef, useState } from 'react'
import { apiAsset, feedClient } from '../../lib/feed'
import type { FeedPage } from '../../lib/feed'
import { Button, GlassPanel, Icon } from '../ui/primitives'

export default function Explore({ onCreate }: { onCreate: () => void }) {
  const [data, setData] = useState<FeedPage | null>(null), [error, setError] = useState<string | null>(null)
  const [retry, setRetry] = useState(0), [more, setMore] = useState(false)
  const pending = useRef<AbortController | null>(null)
  useEffect(() => {
    const abort = new AbortController(); pending.current?.abort(); pending.current = null; setMore(false); setError(null)
    void feedClient.list(abort.signal).then(value => { if (!abort.signal.aborted) setData(value) })
      .catch(reason => { if (!abort.signal.aborted) setError((reason as Error).message) })
    return () => { abort.abort(); pending.current?.abort() }
  }, [retry])
  useEffect(() => { const refresh = () => setRetry(value => value + 1); window.addEventListener('focus', refresh); return () => window.removeEventListener('focus', refresh) }, [])
  const loadMore = async () => {
    if (!data?.next_cursor || more) return
    const abort = pending.current = new AbortController(); setMore(true); setError(null)
    try {
      const next = await feedClient.list(abort.signal, data.next_cursor)
      if (!abort.signal.aborted) setData(previous => previous ? { items: [...previous.items, ...next.items.filter(item => !previous.items.some(old => old.id === item.id))], next_cursor: next.next_cursor } : next)
    } catch (reason) { if (!abort.signal.aborted) setError((reason as Error).message) }
    finally { if (!abort.signal.aborted) setMore(false) }
  }
  return <section className="library-page" aria-labelledby="explore-title">
    <div className="page-heading"><div><h1 id="explore-title">Explore</h1><p>Rooms shared by the community.</p></div><Button onClick={onCreate}>Create a room<Icon name="arrow"/></Button></div>
    {!data && !error && <p role="status">Loading shared rooms…</p>}
    {error && <GlassPanel className="library-empty"><p role="alert">{error}</p><Button onClick={() => setRetry(value => value + 1)}>Retry loading</Button></GlassPanel>}
    {data && data.items.length === 0 && !error && <GlassPanel className="library-empty"><Icon name="globe"/><h2>No shared rooms yet.</h2><p>Share a saved layout to inspire someone.</p><Button variant="primary" onClick={onCreate}>Create your own<Icon name="arrow"/></Button></GlassPanel>}
    {data && <div className="public-room-grid">{data.items.map(room => <a key={room.id} className="public-room-card glass-panel" href={`#shared/${room.id}`}>
      <img src={apiAsset(room.preview_url)} alt={`${room.title} layout preview`} loading="lazy"/>
      <div><span className="section-label">{room.room_type.replaceAll('_', ' ')} · {room.object_count} objects</span><h2>{room.title}</h2>{room.summary && <p>{room.summary}</p>}<span className="public-room-cta">Explore room<Icon name="arrow"/></span></div>
    </a>)}</div>}
    {data?.next_cursor && <Button disabled={more} onClick={() => void loadMore()}>{more ? 'Loading…' : 'Load more rooms'}</Button>}
  </section>
}
