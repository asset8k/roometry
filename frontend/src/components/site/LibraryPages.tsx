import { Button, GlassPanel, Icon } from '../ui/primitives'

export function MyRooms({ onSignIn, onCreate }: { onSignIn: () => void; onCreate: () => void }) {
  return <section className="library-page" aria-labelledby="my-rooms-title">
    <div className="page-heading"><div><h1 id="my-rooms-title">My rooms</h1><p>A place for your saved designs.</p></div><Button onClick={onCreate}>New room<Icon name="arrow"/></Button></div>
    <GlassPanel className="library-empty"><span className="empty-symbol"><Icon name="folder"/></span><h2>Keep the rooms you create.</h2><p>Sign in to save and reopen your designs.</p><Button variant="primary" onClick={onSignIn}>Sign in<Icon name="arrow"/></Button><span className="availability-note">Saved rooms are coming with accounts.</span></GlassPanel>
  </section>
}

export function Explore({ onCreate }: { onCreate: () => void }) {
  return <section className="library-page" aria-labelledby="explore-title">
    <div className="page-heading"><div><h1 id="explore-title">Explore</h1><p>Real rooms. Fresh perspectives.</p></div></div>
    <GlassPanel className="library-empty"><span className="empty-symbol"><Icon name="globe"/></span><h2>A little inspiration is on its way.</h2><p>Community rooms are coming soon.</p><Button variant="primary" onClick={onCreate}>Create your own<Icon name="arrow"/></Button></GlassPanel>
  </section>
}
