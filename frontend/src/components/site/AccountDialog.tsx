import { useId, useState } from 'react'
import Dialog from '../ui/Dialog'
import { Button, Icon } from '../ui/primitives'

export default function AccountDialog({ intent = 'signin', onClose, onGuest }: {
  intent?: 'signin' | 'save'; onClose: () => void; onGuest: () => void
}) {
  const [mode, setMode] = useState<'signin' | 'signup'>('signin')
  const [visible, setVisible] = useState(false)
  const passwordId = useId()
  const title = mode === 'signup' ? 'Make room for more.' : intent === 'save' ? 'Keep your room.' : 'Welcome back.'
  return <Dialog title={title} onClose={onClose}>
    <p className="dialog-description">{mode === 'signup' ? 'Save your designs. Pick up where you left off.' : 'Your rooms, right where you left them.'}</p>
    <div className="auth-tabs" role="group" aria-label="Account form">
      <button type="button" aria-pressed={mode === 'signin'} onClick={() => { setMode('signin'); setVisible(false) }}>Sign in</button>
      <button type="button" aria-pressed={mode === 'signup'} onClick={() => { setMode('signup'); setVisible(false) }}>Create account</button>
    </div>
    <p className="feature-notice" role="status">Accounts are coming soon. You can create a room as a guest.</p>
    {/* Design only until the auth milestone. Never transmit or persist credentials. */}
    <form key={mode} className="account-form" onSubmit={event => event.preventDefault()}>
      <label>Username<input data-initial-focus name="username" autoComplete="username" autoCapitalize="none" spellCheck={false} placeholder="Your username" /></label>
      <div className="account-field"><label htmlFor={passwordId}>Password</label><span className="password-field"><input id={passwordId} name="password" type={visible ? 'text' : 'password'} autoComplete={mode === 'signup' ? 'new-password' : 'current-password'} placeholder={mode === 'signup' ? 'Choose a password' : 'Your password'} />
        <Button variant="quiet" aria-label={visible ? 'Hide password' : 'Show password'} aria-pressed={visible} onClick={() => setVisible(value => !value)}><Icon name={visible ? 'eyeOff' : 'eye'}/></Button>
      </span></div>
      {mode === 'signup' && <label>Confirm password<input name="confirmation" type={visible ? 'text' : 'password'} autoComplete="new-password" placeholder="Repeat your password" /></label>}
      <Button variant="primary" type="submit" disabled aria-describedby="account-availability">{mode === 'signup' ? 'Create account' : 'Sign in'}<Icon name="arrow"/></Button>
      <span id="account-availability" className="visually-hidden">Account access is not available yet.</span>
    </form>
    <Button variant="quiet" className="guest-action" onClick={onGuest}>Continue as guest<Icon name="arrow"/></Button>
  </Dialog>
}
