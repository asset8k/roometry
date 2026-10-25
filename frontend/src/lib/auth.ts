export interface AccountUser { id: string; username: string }
export interface AccountSession { user: AccountUser | null; expires_at: string | null }
export class AuthError extends Error {
  constructor(message: string, readonly status = 0) { super(message) }
}
const baseUrl = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/+$/, '')
export function parseSession(value: unknown): AccountSession {
  if (!value || typeof value !== 'object') throw new AuthError('The sign-in response was interrupted.')
  const data = value as Record<string, unknown>
  if (Object.keys(data).sort().join() !== 'expires_at,user') throw new AuthError('The sign-in response was interrupted.')
  if (data.user === null && data.expires_at === null) return { user: null, expires_at: null }
  const user = data.user as Record<string, unknown> | null
  if (!user || typeof user !== 'object' || Object.keys(user).sort().join() !== 'id,username'
    || typeof user.id !== 'string' || !/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(user.id)
    || typeof user.username !== 'string' || !/^[a-z0-9_]{3,32}$/.test(user.username)
    || typeof data.expires_at !== 'string' || !Number.isFinite(Date.parse(data.expires_at))) throw new AuthError('The sign-in response was interrupted.')
  return { user: { id: user.id, username: user.username }, expires_at: data.expires_at }
}
async function request(path: string, signal: AbortSignal, body?: object): Promise<AccountSession | null> {
  const response = await fetch(`${baseUrl}/auth/${path}`, { method: body ? 'POST' : 'GET', credentials: 'include', cache: 'no-store',
    signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]), ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}) })
  if (response.status === 204 && path === 'logout') return null
  let data: unknown
  try { data = await response.json() } catch { throw new AuthError('The sign-in response was interrupted. Please try again.', response.status) }
  if (!response.ok) {
    const detail = (data as { detail?: { message?: unknown }[] })?.detail?.[0]?.message
    throw new AuthError(typeof detail === 'string' && detail.length <= 240 ? detail : 'Account access is temporarily unavailable.', response.status)
  }
  if (path === 'logout') throw new AuthError('Couldn’t confirm sign-out. Please try again.', response.status)
  return parseSession(data)
}
export const authClient = {
  me: (signal: AbortSignal) => request('me', signal),
  login: (username: string, password: string, signal: AbortSignal) => request('login', signal, { username, password }),
  register: (username: string, password: string, signal: AbortSignal) => request('register', signal, { username, password }),
  logout: (signal: AbortSignal) => request('logout', signal, {}),
}
export type AuthClient = typeof authClient
export interface AccountState { user: AccountUser | null; expiresAt: string | null; pending: boolean; checking: boolean; error: string | null }

/** No JWT or password is retained here; the browser owns the HttpOnly session cookie. */
export class AuthController {
  private state: AccountState = { user: null, expiresAt: null, pending: false, checking: true, error: null }
  private listeners = new Set<() => void>()
  private revision = 0
  private active: AbortController | null = null
  private expiry: ReturnType<typeof setTimeout> | null = null
  constructor(private client: AuthClient = authClient, private now = Date.now) {}
  getSnapshot = () => this.state
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener) } }
  private update(patch: Partial<AccountState>) { this.state = { ...this.state, ...patch }; this.listeners.forEach(listener => listener()) }
  clearError = () => { if (this.state.error) this.update({ error: null }) }
  private accept(session: AccountSession | null) {
    if (this.expiry) clearTimeout(this.expiry)
    const valid = session?.user && session.expires_at && Date.parse(session.expires_at) > this.now()
    this.update({ user: valid ? session.user : null, expiresAt: valid ? session.expires_at : null, pending: false, checking: false, error: null })
    if (valid) this.expiry = setTimeout(() => { this.expiry = null; this.update({ user: null, expiresAt: null }) }, Date.parse(session.expires_at!) - this.now())
  }
  refresh = async () => {
    if (this.state.pending) return
    const revision = ++this.revision, abort = new AbortController()
    this.active?.abort(); this.active = abort
    try {
      const result = await this.client.me(abort.signal)
      if (revision === this.revision) this.accept(result)
    } catch (error) {
      if (revision !== this.revision || abort.signal.aborted) return
      if (error instanceof AuthError && error.status === 401) this.accept(null)
      else this.update({ checking: false, error: 'Couldn’t check your session. Try signing in again.' })
    } finally { if (this.active === abort) this.active = null }
  }
  authenticate = async (mode: 'signin' | 'signup', username: string, password: string) => {
    if (this.state.pending) return false
    const revision = ++this.revision, abort = new AbortController()
    this.active?.abort(); this.active = abort; this.update({ pending: true, checking: false, error: null })
    try {
      const result = await this.client[mode === 'signup' ? 'register' : 'login'](username.trim(), password, abort.signal)
      if (revision !== this.revision || abort.signal.aborted) return false
      if (!result?.user || !result.expires_at || Date.parse(result.expires_at) <= this.now()) throw new AuthError('Your session expired. Please sign in again.')
      this.accept(result); return true
    } catch (error) {
      if (revision === this.revision && !abort.signal.aborted) this.update({ pending: false, error: error instanceof AuthError ? error.message : 'Couldn’t reach sign-in. Please try again.' })
      return false
    } finally { if (this.active === abort) this.active = null }
  }
  logout = async () => {
    if (this.state.pending) return
    const revision = ++this.revision, abort = new AbortController()
    this.active?.abort(); this.active = abort; this.update({ pending: true, error: null })
    try { await this.client.logout(abort.signal); if (revision === this.revision) this.accept(null) }
    catch (error) { if (revision === this.revision && !abort.signal.aborted) this.update({ pending: false, error: error instanceof AuthError ? error.message : 'Couldn’t sign out. Please try again.' }) }
    finally { if (this.active === abort) this.active = null }
  }
  connect = () => {
    void this.refresh()
    const focus = () => { void this.refresh() }
    if (typeof window !== 'undefined') window.addEventListener('focus', focus)
    return () => {
      this.revision++; this.active?.abort(); this.active = null
      if (this.expiry) clearTimeout(this.expiry)
      if (typeof window !== 'undefined') window.removeEventListener('focus', focus)
    }
  }
}
