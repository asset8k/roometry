import { useCallback, useEffect, useRef, useState } from 'react'
import RoomInputWorkspace from './components/room-input/RoomInputWorkspace'
import { Button } from './components/ui/primitives'
import BrandMark from './components/ui/BrandMark'
import AccountDialog from './components/site/AccountDialog'
import ShareDialog from './components/site/ShareDialog'
import { Explore, MyRooms } from './components/site/LibraryPages'
import { SiteActions } from './components/site/SiteActions'
import type { RoomScene } from './lib/scene.generated'

type Page = 'create' | 'explore' | 'my-rooms'
const pageFromHash = (): Page => typeof window !== 'undefined' && window.location.hash === '#explore' ? 'explore' : typeof window !== 'undefined' && window.location.hash === '#my-rooms' ? 'my-rooms' : 'create'

export default function App() {
  const [page, setPage] = useState<Page>(pageFromHash)
  const [account, setAccount] = useState<'signin' | 'save' | null>(null)
  const [sharing, setSharing] = useState<RoomScene | null>(null)
  const main = useRef<HTMLElement>(null)
  const previousPage = useRef(page)
  // Connectivity errors remain at the operation that failed, rather than in global chrome.
  const onConnectionChange = useCallback((_online: boolean) => {}, [])
  const openAccount = useCallback((intent: 'signin' | 'save' = 'signin') => { setSharing(null); setAccount(intent) }, [])
  const openShare = useCallback((scene: RoomScene) => { setAccount(null); setSharing(scene) }, [])
  useEffect(() => {
    const change = () => setPage(pageFromHash())
    window.addEventListener('hashchange', change)
    return () => window.removeEventListener('hashchange', change)
  }, [])
  useEffect(() => {
    if (previousPage.current !== page) main.current?.focus({ preventScroll: true })
    previousPage.current = page
  }, [page])
  const goCreate = () => { window.location.hash = ''; setPage('create') }
  return <SiteActions.Provider value={{ account: openAccount, share: openShare }}>
    <div className="app-shell">
      <a className="skip-link" href="#main-content" onClick={event => { event.preventDefault(); main.current?.focus() }}>Skip to content</a>
      <header className="app-header">
        <a className="wordmark" href="#" aria-label="Roometry home"><BrandMark/>Roometry</a>
        <nav className="site-nav" aria-label="Main navigation">
          <a href="#explore" aria-current={page === 'explore' ? 'page' : undefined}>Explore</a>
          <a href="#my-rooms" aria-current={page === 'my-rooms' ? 'page' : undefined}>My rooms</a>
          <Button onClick={() => openAccount()}>Sign in</Button>
        </nav>
      </header>
      <main ref={main} id="main-content" tabIndex={-1}>
        {/* Keep the live workspace mounted across navigation, preserving guest files and runs. */}
        <div hidden={page !== 'create'}>
          <div className="workspace-heading"><div><h1 id="page-heading" tabIndex={-1}>See your room<span> differently.</span></h1><p className="workspace-subtitle">A few photos. A new perspective.</p></div></div>
          <section aria-label="Your room"><RoomInputWorkspace onConnectionChange={onConnectionChange} /></section>
        </div>
        {page === 'my-rooms' && <MyRooms onSignIn={() => openAccount()} onCreate={goCreate}/>}
        {page === 'explore' && <Explore onCreate={goCreate}/>}
      </main>
    </div>
    {account && <AccountDialog intent={account} onClose={() => setAccount(null)} onGuest={() => { setAccount(null); goCreate() }}/>}
    {sharing && <ShareDialog scene={sharing} onClose={() => setSharing(null)} onSignIn={() => openAccount()}/>}
  </SiteActions.Provider>
}
