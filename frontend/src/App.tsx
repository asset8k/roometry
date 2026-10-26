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
import { isRoomRoute, protectPageUnload, WorkspaceNavigation } from './lib/workspace-navigation'
import type { RoomDestination } from './lib/workspace-navigation'
import { WorkspaceNavigationContext } from './components/site/WorkspaceNavigation'
import Dialog from './components/ui/Dialog'

type Page = 'create' | 'explore' | 'my-rooms' | 'saved' | 'recover'
const pageFromHash = (hash = typeof window === 'undefined' ? '#' : window.location.hash): Page => /^#room\/[a-f0-9-]{36}$/.test(hash) ? 'saved' : /^#run\/[a-f0-9-]{36}$/.test(hash) ? 'recover' : hash === '#explore' ? 'explore' : hash === '#my-rooms' ? 'my-rooms' : 'create'

export default function App() {
  const [auth] = useState(() => new AuthController())
  const session = useSyncExternalStore(auth.subscribe, auth.getSnapshot, auth.getSnapshot)
  useEffect(auth.connect, [auth])
  const [page, setPage] = useState<Page>(() => pageFromHash())
  const [routeHash, setRouteHash] = useState(() => typeof window === 'undefined' ? '' : window.location.hash)
  const [account, setAccount] = useState<'signin' | 'save' | null>(null)
  const [sharing, setSharing] = useState<RoomScene | null>(null)
  const [workspaceKey, setWorkspaceKey] = useState(0)
  useEffect(() => { setSharing(null) }, [session.user?.id])
  const main = useRef<HTMLElement>(null)
  const previousPage = useRef(page)
  const currentHash = useRef(typeof window === 'undefined' ? '#' : window.location.hash || '#')
  const position = useRef(typeof window === 'undefined' ? 0 : Number(window.history.state?.roometryPosition) || 0)
  const bounce = useRef<RoomDestination | null>(null)
  const [navigation] = useState(() => new WorkspaceNavigation(async destination => {
    if (destination.signOut) {
      await auth.logout()
      if (auth.getSnapshot().user) throw new Error('Couldn’t sign out. Your workspace is kept; please retry.')
    }
    const hash = destination.hash || '#'
    setAccount(null); setSharing(null)
    if (destination.traverse) {
      position.current += destination.traverse
      window.history.go(destination.traverse)
    } else if (hash !== currentHash.current || destination.replace) {
      if (!destination.replace) position.current++
      window.history[destination.replace ? 'replaceState' : 'pushState']({ roometryPosition: position.current }, '', hash)
    }
    currentHash.current = hash
    setRouteHash(hash); setPage(pageFromHash(hash))
    if (destination.fresh) setWorkspaceKey(key => key + 1)
    window.requestAnimationFrame(() => { main.current?.focus({ preventScroll: true }); window.scrollTo({ top: 0, behavior: 'auto' }) })
  }))
  const nav = useSyncExternalStore(navigation.subscribe, navigation.getSnapshot, navigation.getSnapshot)
  // Connectivity errors remain at the operation that failed, rather than in global chrome.
  const onConnectionChange = useCallback((_online: boolean) => {}, [])
  const openAccount = useCallback((intent: 'signin' | 'save' = 'signin') => { setSharing(null); setAccount(intent) }, [])
  const openShare = useCallback((scene: RoomScene) => { setAccount(null); setSharing(scene) }, [])
  useEffect(() => {
    window.history.replaceState({ roometryPosition: position.current }, '', window.location.href)
    const change = () => {
      const hash = window.location.hash || '#'
      if (hash === currentHash.current) {
        if (bounce.current) { const destination = bounce.current; bounce.current = null; navigation.request(destination) }
        return
      }
      const target = window.history.state?.roometryPosition
      if (Number.isInteger(target) && target !== position.current) {
        bounce.current = { hash, traverse: target - position.current }
        window.history.go(position.current - target)
      } else {
        // A directly typed hash has no application history metadata.
        window.history.replaceState({ roometryPosition: position.current }, '', currentHash.current)
        navigation.request({ hash: isRoomRoute(hash) ? hash : '#', replace: true })
      }
    }
    window.addEventListener('hashchange', change)
    return () => window.removeEventListener('hashchange', change)
  }, [navigation])
  useEffect(() => {
    if (!nav.dirty && !nav.active && !nav.saving) return
    const beforeUnload = (event: BeforeUnloadEvent) => protectPageUnload(navigation, event)
    window.addEventListener('beforeunload', beforeUnload)
    return () => window.removeEventListener('beforeunload', beforeUnload)
  }, [navigation, nav.dirty, nav.active, nav.saving])
  useEffect(() => {
    if (previousPage.current !== page) main.current?.focus({ preventScroll: true })
    previousPage.current = page
  }, [page])
  const goCreate = () => navigation.request({ hash: '#', fresh: true })
  return <WorkspaceNavigationContext.Provider value={navigation}><SiteActions.Provider value={{ userId: session.user?.id ?? null, account: openAccount, share: openShare }}>
    <div className="app-shell" onClickCapture={event => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || !(event.target instanceof Element)) return
      const link = event.target.closest<HTMLAnchorElement>('a[href]'), hash = link?.getAttribute('href')
      if (!link || link.target === '_blank' || !hash || !isRoomRoute(hash)) return
      event.preventDefault()
      if (hash === '#') goCreate()
      else if (hash !== currentHash.current) navigation.request({ hash })
    }}>
      <a className="skip-link" href="#main-content" onClick={event => { event.preventDefault(); main.current?.focus() }}>Skip to content</a>
      <header className="app-header">
        <a className="wordmark" href="#" aria-label="Roometry home"><BrandMark/>Roometry</a>
        <nav className="site-nav" aria-label="Main navigation">
          <a href="#explore" aria-current={page === 'explore' ? 'page' : undefined}>Explore</a>
          <a href="#my-rooms" aria-current={['my-rooms', 'saved', 'recover'].includes(page) ? 'page' : undefined}>My rooms</a>
          {(page !== 'create' || nav.hasWork) && <Button onClick={goCreate}>New room</Button>}
          {session.user ? <><span className="account-name" title={session.user.username}>{session.user.username}</span><Button disabled={session.pending} onClick={() => navigation.request({ hash: '#', fresh: true, signOut: true })}>Sign out</Button></>
            : <Button onClick={() => { auth.clearError(); openAccount() }}>Sign in</Button>}
        </nav>
      </header>
      {session.user && session.error && <p className="auth-session-error" role="alert">{session.error}</p>}
      <main ref={main} id="main-content" tabIndex={-1}>
        {page === 'create' && <div key={workspaceKey}>
          <div className="workspace-heading"><div><h1 id="page-heading" tabIndex={-1}>See your room<span> differently.</span></h1><p className="workspace-subtitle">A few photos. A new perspective.</p></div></div>
          <section aria-label="Your room"><RoomInputWorkspace onConnectionChange={onConnectionChange} /></section>
        </div>}
        {page === 'my-rooms' && <MyRooms user={session.user} onSignIn={() => openAccount()} onCreate={goCreate}/>}
        {(page === 'saved' || page === 'recover') && <SavedRoomPage id={routeHash.split('/')[1]} kind={page} user={session.user} onSignIn={() => openAccount()} onConnectionChange={onConnectionChange}/>}
        {page === 'explore' && <Explore onCreate={goCreate}/>}
      </main>
    </div>
    {account && <AccountDialog controller={auth} intent={account} onClose={() => setAccount(null)} onGuest={() => { setAccount(null); if (page !== 'create') navigation.request({ hash: '#' }) }}/>}
    {sharing && <ShareDialog signedIn={!!session.user} scene={sharing} onClose={() => setSharing(null)} onSignIn={() => openAccount()}/>}
    {nav.pending && (nav.dirty || nav.active || nav.saving || nav.error) && <Dialog title={nav.pending.fresh ? 'Start a new room?' : 'Leave this room?'} onClose={navigation.stay}>
      <p className="dialog-description">{nav.saving ? 'Wait for the save to finish before leaving.' : nav.active ? 'This will stop the current work and discard unsaved changes in this workspace. Saved rooms stay available.' : 'Unsaved changes will be discarded. Saved rooms stay available.'}</p>
      {nav.error && <p className="input-notice error" role="alert">{nav.error}</p>}
      <div className="dialog-actions"><Button data-initial-focus disabled={nav.leaving} onClick={navigation.stay}>Stay here</Button>
        <Button variant="primary" disabled={nav.leaving || nav.saving} onClick={() => void navigation.confirm()}>{nav.leaving ? 'Stopping…' : nav.pending.fresh ? 'Start new room' : 'Discard & leave'}</Button></div>
    </Dialog>}
  </SiteActions.Provider></WorkspaceNavigationContext.Provider>
}
