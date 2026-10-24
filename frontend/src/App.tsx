import { useCallback, useEffect, useState } from 'react'
import { fetchHealth } from './lib/api'
import RoomInputWorkspace from './components/room-input/RoomInputWorkspace'
import { Button, Icon } from './components/ui/primitives'

type ServiceStatus = 'checking' | 'online' | 'offline'

const statusLabels: Record<ServiceStatus, string> = {
  checking: 'Connecting',
  online: 'Connected',
  offline: 'Offline',
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
          <span className="brand-mark"><Icon name="room" /></span>
          Roometry
        </a>
        <a className="header-workspace-link" href="#room-input"><span className="status-dot" aria-hidden="true" />Create a room</a>
        <div className="header-status">
          <p className={`connection-status ${status}`} role="status" aria-live="polite">
            <span className="status-dot" aria-hidden="true" />
            {statusLabels[status]}
          </p>
          {status === 'offline' && <Button variant="quiet" onClick={() => setAttempt(value => value + 1)}><Icon name="refresh" />Retry</Button>}
        </div>
      </header>

      <main>
        <div className="workspace-heading">
          <div>
            <p className="eyebrow"><Icon name="spark" />A little room for possibility</p>
            <h1>See your room<span> differently.</span></h1>
            <p className="workspace-subtitle">Your photos. Your space. A fresh perspective.</p>
          </div>
        </div>

        <section aria-label="Your room"><RoomInputWorkspace onConnectionChange={onConnectionChange} /></section>
      </main>

      <footer><span>Roometry</span><span>Made for your space.</span></footer>
    </div>
  )
}
