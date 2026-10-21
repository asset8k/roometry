import { useCallback, useEffect, useState } from 'react'
import { fetchHealth } from './lib/api'
import RoomInputWorkspace from './components/room-input/RoomInputWorkspace'

type ServiceStatus = 'checking' | 'online' | 'offline'

const statusLabels: Record<ServiceStatus, string> = {
  checking: 'Checking connection…',
  online: 'Service connected',
  offline: 'Service unavailable',
}

export default function App() {
  const [status, setStatus] = useState<ServiceStatus>('checking')
  const [attempt, setAttempt] = useState(0)
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
          <span className="development-badge">One room, new possibilities</span>
        </div>

        <section aria-label="Your room"><RoomInputWorkspace onConnectionChange={onConnectionChange} /></section>
      </main>

      <footer>Roometry · Thoughtful spaces, new possibilities.</footer>
    </div>
  )
}
