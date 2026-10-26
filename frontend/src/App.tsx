import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { AuthController } from './lib/auth'
import SavedRoomPage from './components/site/SavedRoomPage'
import RoomInputWorkspace from './components/room-input/RoomInputWorkspace'
import { Button } from './components/ui/primitives'
import BrandMark from './components/ui/BrandMark'
import AccountDialog from './components/site/AccountDialog'
import ShareDialog from './components/site/ShareDialog'
import { Explore, MyRooms } from './components/site/LibraryPages'
import { SiteActions } from './components/site/SiteActions'
import type { RoomScene } from './lib/scene.generated'

type Page = 'create' | 'explore' | 'my-rooms' | 'saved' | 'recover'
const pageFromHash = (): Page => typeof window !== 'undefined' && /^#room\/[a-f0-9-]{36}$/.test(window.location.hash) ? 'saved' : typeof window !== 'undefined' && /^#run\/[a-f0-9-]{36}$/.test(window.location.hash) ? 'recover' : typeof window !== 'undefined' && window.location.hash === '#explore' ? 'explore' : typeof window !== 'undefined' && window.location.hash === '#my-rooms' ? 'my-rooms' : 'create'

export default function App() {
  const [auth] = useState(() => new AuthController())
  const session = useSyncExternalStore(auth.subscribe, auth.getSnapshot, auth.getSnapshot)
  useEffect(auth.connect, [auth])
  const [page, setPage] = useState<Page>(pageFromHash)
  const [routeHash, setRouteHash] = useState(() => typeof window === 'undefined' ? '' : window.location.hash)
  const [account, setAccount] = useState<'signin' | 'save' | null>(null)
  const [sharing, setSharing] = useState<RoomScene | null>(null)
  useEffect(() => { setSharing(null) }, [session.user?.id])
  const main = useRef<HTMLElement>(null)
  const previousPage = useRef(page)
  // Connectivity errors remain at the operation that failed, rather than in global chrome.
  const onConnectionChange = useCallback((_online: boolean) => {}, [])
  const openAccount = useCallback((intent: 'signin' | 'save' = 'signin') => { setSharing(null); setAccount(intent) }, [])
  const openShare = useCallback((scene: RoomScene) => { setAccount(null); setSharing(scene) }, [])
  useEffect(() => {
    const change = () => { setPage(pageFromHash()); setRouteHash(window.location.hash) }
    window.addEventListener('hashchange', change)
    return () => window.removeEventListener('hashchange', change)
  }, [])
  useEffect(() => {
    if (previousPage.current !== page) main.current?.focus({ preventScroll: true })
    previousPage.current = page
  }, [page])
  const goCreate = () => { window.location.hash = ''; setPage('create') }
  return <SiteActions.Provider value={{ userId: session.user?.id ?? null, account: openAccount, share: openShare }}>
    <div className="app-shell">
      <a className="skip-link" href="#main-content" onClick={event => { event.preventDefault(); main.current?.focus() }}>Skip to content</a>
      <header className="app-header">
        <a className="wordmark" href="#" aria-label="Roometry home"><BrandMark/>Roometry</a>
        <nav className="site-nav" aria-label="Main navigation">
          <a href="#explore" aria-current={page === 'explore' ? 'page' : undefined}>Explore</a>
          <a href="#my-rooms" aria-current={['my-rooms', 'saved', 'recover'].includes(page) ? 'page' : undefined}>My rooms</a>
          {session.user ? <><span className="account-name" title={session.user.username}>{session.user.username}</span><Button disabled={session.pending} onClick={() => void auth.logout()}>Sign out</Button></>
            : <Button onClick={() => { auth.clearError(); openAccount() }}>Sign in</Button>}
        </nav>
      </header>
      {session.user && session.error && <p className="auth-session-error" role="alert">{session.error}</p>}
      <main ref={main} id="main-content" tabIndex={-1}>
        {/* Keep the live workspace mounted across navigation, preserving guest files and runs. */}
        <div hidden={page !== 'create'}>
          <div className="workspace-heading"><div><h1 id="page-heading" tabIndex={-1}>See your room<span> differently.</span></h1><p className="workspace-subtitle">A few photos. A new perspective.</p></div></div>
          <section aria-label="Your room"><RoomInputWorkspace onConnectionChange={onConnectionChange} /></section>
        </div>
        {page === 'my-rooms' && <MyRooms user={session.user} onSignIn={() => openAccount()} onCreate={goCreate}/>}
        {(page === 'saved' || page === 'recover') && <SavedRoomPage id={routeHash.split('/')[1]} kind={page} user={session.user} onSignIn={() => openAccount()} onConnectionChange={onConnectionChange}/>}
        {page === 'explore' && <Explore onCreate={goCreate}/>}
      </main>
    </div>
    {account && <AccountDialog controller={auth} intent={account} onClose={() => setAccount(null)} onGuest={() => { setAccount(null); goCreate() }}/>}
    {sharing && <ShareDialog signedIn={!!session.user} scene={sharing} onClose={() => setSharing(null)} onSignIn={() => openAccount()}/>}
  </SiteActions.Provider>
}
