import { useState } from 'react'
import type { RoomScene } from '../../lib/scene.generated'
import Dialog from '../ui/Dialog'
import { Button, Icon } from '../ui/primitives'

export default function ShareDialog({ scene, onClose, onSignIn, signedIn = false }: { scene: RoomScene; onClose: () => void; onSignIn: () => void; signedIn?: boolean }) {
  const [consent, setConsent] = useState(false)
  const room = scene.room.room_type.replaceAll('_', ' ')
  return <Dialog title="Share your space." onClose={onClose}>
    <p className="dialog-description">Choose what others can see.</p>
    <div className="sharing-preview"><Icon name="room"/><div><strong>Your {room}</strong><span>{scene.room.width_m} × {scene.room.length_m} m · {scene.objects.length} objects</span></div><span className="private-badge"><Icon name="lock"/>Private</span></div>
    <label className="sharing-title">Title<input data-initial-focus defaultValue={`My ${room}`} maxLength={80}/></label>
    <label className="consent-choice"><input type="checkbox" checked={consent} onChange={event=>setConsent(event.target.checked)}/><span>Publish this 3D design to Explore.</span></label>
    <p className="sharing-privacy"><Icon name="lock"/>Photos and chat stay private.</p>
    <p className="feature-notice" id="sharing-availability">Public sharing is coming later. Nothing will be published.</p>
    <div className="dialog-actions">{!signedIn && <Button onClick={onSignIn}>Sign in</Button>}<Button variant="primary" disabled aria-describedby="sharing-availability">Share to Explore<Icon name="share"/></Button></div>
  </Dialog>
}
