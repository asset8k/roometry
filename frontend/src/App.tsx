import { useEffect, useState } from 'react'
import { fetchHealth } from './lib/api'

type ServiceStatus = 'checking' | 'online' | 'offline'

const statusLabels: Record<ServiceStatus, string> = {
  checking: 'Checking connection…',
  online: 'Service connected',
  offline: 'Service unavailable',
}

export default function App() {
  const [status, setStatus] = useState<ServiceStatus>('checking')
  const [attempt, setAttempt] = useState(0)

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
        <span className="development-badge">In development</span>
      </header>

      <main className="welcome">
        <p className="eyebrow">A little room for possibility</p>
        <h1>Your room,<br /><span>reimagined.</span></h1>
        <p className="welcome-copy">
          A fresh perspective on the space you call home.
          Your interactive room workspace is taking shape.
        </p>

        <section className="workspace-placeholder" aria-label="Room workspace coming soon">
          <div className="room-outline" aria-hidden="true">
            <span className="room-outline-inner" />
          </div>
          <h2>A new view is coming</h2>
          <p>Explore an approximate 3D layout, then make it your own.</p>
        </section>

        <div className="connection-row">
          <p className={`connection-status ${status}`} role="status" aria-live="polite">
            <span className="status-dot" aria-hidden="true" />
            {statusLabels[status]}
          </p>
          <button
            type="button"
            onClick={() => setAttempt(value => value + 1)}
            disabled={status === 'checking'}
          >
            Check again
          </button>
        </div>
      </main>

      <footer>Roometry · Thoughtful spaces, new possibilities.</footer>
    </div>
  )
}
