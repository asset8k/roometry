import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react'
import { fetchHealth } from './lib/api'
import { loadExampleScene, loadAlternativeScene } from './lib/scene'
import SceneWorkspace from './components/room-viewer/SceneWorkspace'
import RoomInputWorkspace from './components/room-input/RoomInputWorkspace'

const FurnitureGallery = lazy(() => import('./components/room-viewer/FurnitureGallery'))

type ServiceStatus = 'checking' | 'online' | 'offline'

const statusLabels: Record<ServiceStatus, string> = {
  checking: 'Checking connection…',
  online: 'Service connected',
  offline: 'Service unavailable',
}

export default function App() {
  const [status, setStatus] = useState<ServiceStatus>('checking')
  const [attempt, setAttempt] = useState(0)
  const [workspace, setWorkspace] = useState<'input' | 'example' | 'furniture'>('input')
  const [exampleOpened, setExampleOpened] = useState(false)
  const sceneLoad = useMemo(() => {
    try {
      return { scene: loadExampleScene(), afterScene: loadAlternativeScene(), error: null }
    } catch {
      return { scene: null, afterScene: null, error: 'The example room could not be loaded. Please refresh the page.' }
    }
  }, [])
  const sourceScene = sceneLoad.scene
  const onConnectionChange = useCallback((online: boolean) => setStatus(online ? 'online' : 'offline'), [])

  useEffect(() => {
    const controller = new AbortController()
    setStatus('checking')

    fetchHealth(controller.signal)
      .then(() => {
        if (!controller.signal.aborted) setStatus('online')
      })
      .catch(() => {
        if (!controller.signal.aborted) setStatus('offline')
      })

    return () => controller.abort()
  }, [attempt])

  return (
    <div className="app-shell">
      <header className="app-header">
        <a className="wordmark" href="/" aria-label="Roometry home">
          <span className="brand-mark" aria-hidden="true">R</span>
          Roometry
        </a>
        <div className="header-status">
          <p className={`connection-status ${status}`} role="status" aria-live="polite">
            <span className="status-dot" aria-hidden="true" />
            {statusLabels[status]}
          </p>
          <button type="button" onClick={() => setAttempt(value => value + 1)} disabled={status === 'checking'}>
            Check again
          </button>
        </div>
      </header>

      <main>
        <div className="workspace-heading">
          <div>
            <p className="eyebrow">A little room for possibility</p>
            <h1>A new perspective<span> on your space.</span></h1>
          </div>
          <span className="development-badge">{workspace === 'input' ? 'One room, new possibilities' : workspace === 'example' ? 'Example room' : 'Object collection'}</span>
        </div>

        <div className="workspace-navigation" role="group" aria-label="Workspace">
          <button type="button" aria-pressed={workspace === 'input'} aria-controls="your-room-input" onClick={() => setWorkspace('input')}>Your room</button>
          <button type="button" aria-pressed={workspace === 'example'} aria-controls="example-room-workspace" onClick={() => { setExampleOpened(true); setWorkspace('example') }}>Explore example</button>
          <button type="button" aria-pressed={workspace === 'furniture'} aria-controls="furniture-catalog" onClick={() => setWorkspace('furniture')}>Object catalog</button>
        </div>
        <section id="your-room-input" hidden={workspace !== 'input'} aria-label="Prepare your room"><RoomInputWorkspace onConnectionChange={onConnectionChange} /></section>

        {exampleOpened && <section id="example-room-workspace" hidden={workspace !== 'example'} aria-label="Example room workspace">
          <p className="example-notice">An authored example bedroom. It is separate from your uploaded photos.</p>
          {!sourceScene && <div className="viewer-message" role="alert"><p>{sceneLoad.error}</p></div>}

        {sourceScene && sceneLoad.afterScene && (
          <SceneWorkspace beforeScene={sourceScene} initialScene={sceneLoad.afterScene} onConnectionChange={onConnectionChange} />
        )}
        </section>}
        {workspace === 'furniture' && <section id="furniture-catalog" aria-label="Object catalog">
          <Suspense fallback={<p role="status">Loading objects…</p>}><FurnitureGallery /></Suspense>
        </section>}
      </main>

      <footer>Roometry · Thoughtful spaces, new possibilities.</footer>
    </div>
  )
}
