import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { fetchHealth } from './lib/api'
import { loadExampleScene } from './lib/scene'
import { applyColorPreviews } from './lib/color-previews'
import ObjectList from './components/room-viewer/ObjectList'

const RoomViewer = lazy(() => import('./components/room-viewer/RoomViewer'))

type ServiceStatus = 'checking' | 'online' | 'offline'

const statusLabels: Record<ServiceStatus, string> = {
  checking: 'Checking connection…',
  online: 'Service connected',
  offline: 'Service unavailable',
}

export default function App() {
  const [status, setStatus] = useState<ServiceStatus>('checking')
  const [attempt, setAttempt] = useState(0)
  const [detailsView, setDetailsView] = useState<'room' | 'objects'>('room')
  const [previewColors, setPreviewColors] = useState<Record<string, string>>({})
  const sceneLoad = useMemo(() => {
    try {
      return { scene: loadExampleScene(), error: null }
    } catch {
      return { scene: null, error: 'The example room could not be loaded. Please refresh the page.' }
    }
  }, [])
  const sourceScene = sceneLoad.scene
  const scene = useMemo(
    () => sourceScene ? applyColorPreviews(sourceScene, previewColors) : null,
    [sourceScene, previewColors],
  )

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
          <span className="development-badge">Example room</span>
        </div>

        {!scene && <div className="viewer-message" role="alert"><p>{sceneLoad.error}</p></div>}

        {scene && (
          <div className="workspace-grid">
            <aside className="room-details" aria-label="Room details">
              <div className="room-title">
                <span className="section-label">Your space</span>
                <h2>Example bedroom</h2>
                <p>A simple room to explore from every angle.</p>
              </div>

              <div className="details-switch" role="group" aria-label="Details view">
                <button type="button" aria-pressed={detailsView === 'room'} onClick={() => setDetailsView('room')}>Room</button>
                <button type="button" aria-pressed={detailsView === 'objects'} onClick={() => setDetailsView('objects')}>Objects <span>{scene.objects.length}</span></button>
              </div>

              {detailsView === 'room' && (
                <>
                  <dl className="room-measurements">
                    <div><dt>Width</dt><dd>{scene.room.width_m.toFixed(1)} <span>m</span></dd></div>
                    <div><dt>Length</dt><dd>{scene.room.length_m.toFixed(1)} <span>m</span></dd></div>
                    <div><dt>Height</dt><dd>{scene.room.height_m.toFixed(1)} <span>m</span></dd></div>
                    <div><dt>Floor area</dt><dd>{(scene.room.width_m * scene.room.length_m).toFixed(1)} <span>m²</span></dd></div>
                  </dl>

                  <section className="openings-summary">
                    <h3>Doors & windows</h3>
                    <ul>
                      {scene.openings.map(opening => (
                        <li key={opening.id}>
                          <span className={`opening-swatch ${opening.type}`} aria-hidden="true" />
                          <div><strong>{opening.type === 'door' ? 'Door' : 'Window'}</strong><span>{opening.wall} wall · {opening.width_m.toFixed(1)} m wide</span></div>
                        </li>
                      ))}
                    </ul>
                  </section>

                  <div className="scene-note">
                    <strong>Make it yours</strong>
                    <p>Open Objects to try colors on the {scene.objects.length} furnishings. Previews stay in this browser session.</p>
                  </div>
                  <p className="approximation-note">An approximate model. Opening positions are estimated; verify measurements in your room.</p>
                </>
              )}

              {detailsView === 'objects' && (
                <ObjectList
                  scene={scene}
                  hasColorPreviews={Object.keys(previewColors).length > 0}
                  onColorChange={(id, color) => setPreviewColors(current => ({ ...current, [id]: color }))}
                  onResetColors={() => setPreviewColors({})}
                />
              )}
            </aside>

            <section className="viewer-card" aria-label="3D room viewer">
              <div className="viewer-heading"><h2>Room overview</h2><span>{scene.objects.length} objects</span></div>
              <div className="room-canvas">
                <Suspense fallback={<div className="viewer-message" role="status">Loading your room…</div>}>
                  <RoomViewer scene={scene} />
                </Suspense>
              </div>
              <div className="viewer-footer">
                <p>View controls change the camera only. Color previews reset on refresh.</p>
                <span>Openings shown as markers</span>
              </div>
            </section>
          </div>
        )}
      </main>

      <footer>Roometry · Thoughtful spaces, new possibilities.</footer>
    </div>
  )
}
