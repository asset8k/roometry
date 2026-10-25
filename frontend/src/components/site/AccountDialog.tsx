import { useEffect, useId, useRef, useState, useSyncExternalStore } from 'react'
import Dialog from '../ui/Dialog'
import { Button, Icon } from '../ui/primitives'
import { AuthController } from '../../lib/auth'

export default function AccountDialog({ intent = 'signin', onClose, onGuest, controller: supplied }: {
  intent?: 'signin' | 'save'; onClose: () => void; onGuest: () => void; controller?: AuthController
}) {
  const [controller] = useState(() => supplied ?? new AuthController())
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot)
  const [mode, setMode] = useState<'signin' | 'signup'>('signin')
  const [visible, setVisible] = useState(false)
  const [username, setUsername] = useState(''), [password, setPassword] = useState(''), [confirmation, setConfirmation] = useState('')
  const [error, setError] = useState<string | null>(null)
  const mounted = useRef(true), notice = useRef<HTMLParagraphElement>(null)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  useEffect(() => { if (error || state.error) notice.current?.focus() }, [error, state.error])
  const passwordId = useId()
  const title = state.user ? 'You’re signed in.' : mode === 'signup' ? 'Make room for more.' : intent === 'save' ? 'Keep your room.' : 'Welcome back.'
  const change = () => { setError(null); controller.clearError() }
  if (state.user) return <Dialog title={title} onClose={onClose}>
    <p className="dialog-description">Signed in as {state.user.username}.</p>
    <p className="feature-notice">Room saving is the next step. Your current workspace stays here.</p>
    <Button variant="primary" onClick={onClose}>Back to your room<Icon name="arrow"/></Button>
  </Dialog>
  return <Dialog title={title} onClose={onClose}>
    <p className="dialog-description">{mode === 'signup' ? 'Create your Roometry account.' : 'Sign in to your Roometry account.'}</p>
    <div className="auth-tabs" role="group" aria-label="Account form">
      <button type="button" disabled={state.pending} aria-pressed={mode === 'signin'} onClick={() => { setMode('signin'); setVisible(false); setPassword(''); setConfirmation(''); change() }}>Sign in</button>
      <button type="button" disabled={state.pending} aria-pressed={mode === 'signup'} onClick={() => { setMode('signup'); setVisible(false); setPassword(''); setConfirmation(''); change() }}>Create account</button>
    </div>
    <form key={mode} className="account-form" onSubmit={event => {
      event.preventDefault()
      if (state.pending) return
      if (mode === 'signup' && password !== confirmation) { setError('Passwords don’t match.'); return }
      setError(null)
      void controller.authenticate(mode, username, password).then(success => {
        if (success && mounted.current) { setPassword(''); setConfirmation(''); onClose() }
      })
    }}>
      <label>Username<input data-initial-focus name="username" autoComplete="username" autoCapitalize="none" spellCheck={false} required minLength={3} maxLength={32} pattern="[A-Za-z0-9_]{3,32}"
        value={username} disabled={state.pending} onChange={event => { setUsername(event.target.value); change() }} placeholder="Letters, numbers or underscores" /></label>
      <div className="account-field"><label htmlFor={passwordId}>Password</label><span className="password-field"><input id={passwordId} name="password" type={visible ? 'text' : 'password'} autoComplete={mode === 'signup' ? 'new-password' : 'current-password'} required minLength={12} maxLength={128}
        value={password} disabled={state.pending} onChange={event => { setPassword(event.target.value); change() }} placeholder={mode === 'signup' ? 'At least 12 characters' : 'Your password'} />
        <Button variant="quiet" disabled={state.pending} aria-label={visible ? 'Hide password' : 'Show password'} aria-pressed={visible} onClick={() => setVisible(value => !value)}><Icon name={visible ? 'eyeOff' : 'eye'}/></Button>
      </span></div>
      {mode === 'signup' && <label>Confirm password<input name="confirmation" type={visible ? 'text' : 'password'} autoComplete="new-password" required minLength={12} maxLength={128}
        value={confirmation} disabled={state.pending} onChange={event => { setConfirmation(event.target.value); change() }} placeholder="Repeat your password" /></label>}
      {(error || state.error) && <p ref={notice} className="input-notice error" role="alert" tabIndex={-1}>{error || state.error}</p>}
      <Button variant="primary" type="submit" disabled={state.pending} aria-busy={state.pending}>{state.pending ? mode === 'signup' ? 'Creating account…' : 'Signing in…' : mode === 'signup' ? 'Create account' : 'Sign in'}<Icon name="arrow"/></Button>
    </form>
    <Button variant="quiet" className="guest-action" onClick={onGuest}>Continue as guest<Icon name="arrow"/></Button>
  </Dialog>
}
