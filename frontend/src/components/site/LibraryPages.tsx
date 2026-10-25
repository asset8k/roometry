import { Button, GlassPanel, Icon } from '../ui/primitives'
import type { AccountUser } from '../../lib/auth'

export function MyRooms({ onSignIn, onCreate, user }: { onSignIn: () => void; onCreate: () => void; user?: AccountUser | null }) {
  return <section className="library-page" aria-labelledby="my-rooms-title">
    <div className="page-heading"><div><h1 id="my-rooms-title">My rooms</h1><p>A place for your saved designs.</p></div><Button onClick={onCreate}>New room<Icon name="arrow"/></Button></div>
    <GlassPanel className="library-empty"><span className="empty-symbol"><Icon name="folder"/></span><h2>{user ? 'You’re signed in.' : 'Keep the rooms you create.'}</h2><p>{user ? 'Room saving and reopening come next.' : 'Sign in to your Roometry account.'}</p><Button variant="primary" onClick={user ? onCreate : onSignIn}>{user ? 'Create a room' : 'Sign in'}<Icon name="arrow"/></Button><span className="availability-note">Saved rooms are the next step.</span></GlassPanel>
  </section>
}

export function Explore({ onCreate }: { onCreate: () => void }) {
  return <section className="library-page" aria-labelledby="explore-title">
    <div className="page-heading"><div><h1 id="explore-title">Explore</h1><p>Real rooms. Fresh perspectives.</p></div></div>
    <GlassPanel className="library-empty"><span className="empty-symbol"><Icon name="globe"/></span><h2>A little inspiration is on its way.</h2><p>Community rooms are coming soon.</p><Button variant="primary" onClick={onCreate}>Create your own<Icon name="arrow"/></Button></GlassPanel>
  </section>
}
