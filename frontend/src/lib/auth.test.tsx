import {beforeEach,afterEach,describe,it,expect,vi} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import {AuthController,AuthError,authClient,parseSession} from './auth'
import type {AuthClient,AccountSession} from './auth'
import AccountDialog from '../components/site/AccountDialog'

const user={id:'12345678-1234-4234-8234-123456789abc',username:'test_user'}
const session=():AccountSession=>({user,expires_at:new Date(Date.now()+3600000).toISOString()})
function client():AuthClient{return {me:vi.fn(async()=>session()),login:vi.fn(async()=>session()),register:vi.fn(async()=>session()),logout:vi.fn(async()=>null)}}
function deferred<T>(){let resolve!:(value:T)=>void;const promise=new Promise<T>(yes=>{resolve=yes});return {promise,resolve}}
beforeEach(()=>vi.useFakeTimers())
afterEach(()=>{vi.clearAllTimers();vi.useRealTimers();vi.unstubAllGlobals()})

describe('account sessions',()=>{
 it('restores the account from the HttpOnly session and signs out through the server',async()=>{
  const api=client(),auth=new AuthController(api)
  await auth.refresh();expect(auth.getSnapshot().user).toEqual(user)
  await auth.logout();expect(api.logout).toHaveBeenCalledTimes(1);expect(auth.getSnapshot().user).toBeNull()
 })
 it('does not let a stale anonymous bootstrap overwrite a successful sign-in',async()=>{
  const api=client(),old=deferred<AccountSession|null>()
  api.me=vi.fn(()=>old.promise)
  const auth=new AuthController(api),read=auth.refresh()
  expect(await auth.authenticate('signin','test_user','private test password')).toBe(true)
  old.resolve({user:null,expires_at:null});await read
  expect(auth.getSnapshot().user).toEqual(user)
  expect(JSON.stringify(auth.getSnapshot())).not.toContain('private test password')
 })
 it('deduplicates submit attempts and reports a real registration error',async()=>{
  const api=client(),pending=deferred<AccountSession|null>()
  api.register=vi.fn(()=>pending.promise)
  const auth=new AuthController(api),first=auth.authenticate('signup','test_user','private password')
  expect(await auth.authenticate('signup','test_user','private password')).toBe(false)
  expect(api.register).toHaveBeenCalledTimes(1)
  pending.resolve(session());expect(await first).toBe(true)
  const other=client();other.register=vi.fn(async()=>{throw new AuthError('That username is unavailable.',409)})
  const failed=new AuthController(other)
  expect(await failed.authenticate('signup','test_user','private password')).toBe(false)
  expect(failed.getSnapshot().user).toBeNull();expect(failed.getSnapshot().error).toContain('unavailable')
 })
 it('keeps the account visible when the server cannot revoke a session',async()=>{
  const api=client();api.logout=vi.fn(async()=>{throw new AuthError('Couldn’t sign out.',503)})
  const auth=new AuthController(api);await auth.refresh();await auth.logout()
  expect(auth.getSnapshot().user).toEqual(user);expect(auth.getSnapshot().error).toContain('sign out')
 })
 it('clears expired or invalid sessions while keeping guest mode available',async()=>{
  const api=client();api.me=vi.fn(async()=>({...session(),expires_at:new Date(Date.now()+1000).toISOString()}))
  const auth=new AuthController(api);await auth.refresh();await vi.advanceTimersByTimeAsync(1001)
  expect(auth.getSnapshot().user).toBeNull()
  api.me=vi.fn(async()=>{throw new AuthError('expired',401)})
  await auth.refresh();expect(auth.getSnapshot()).toMatchObject({user:null,pending:false,checking:false,error:null})
 })
 it('uses credentialed JSON requests and never expects a JavaScript-readable token',async()=>{
  const fetcher=vi.fn(async()=>new Response(JSON.stringify(session()),{status:200,headers:{'content-type':'application/json'}}))
  vi.stubGlobal('fetch',fetcher)
  await authClient.login('test_user','private test password',new AbortController().signal)
  expect(fetcher).toHaveBeenCalledWith('/api/auth/login',expect.objectContaining({method:'POST',credentials:'include',cache:'no-store',body:JSON.stringify({username:'test_user',password:'private test password'})}))
  expect(()=>parseSession({...session(),access_token:'unexpected-secret'})).toThrow(AuthError)
  for(const invalid of [{...session(),user:{...user,username:'@bad'}},{...session(),expires_at:'not-a-date'},{}]) expect(()=>parseSession(invalid)).toThrow(AuthError)
 })
 it('renders usable account fields, guest access and truthful signed-in save behavior',async()=>{
  const auth=new AuthController(client())
  const html=renderToStaticMarkup(<AccountDialog controller={auth} onClose={()=>{}} onGuest={()=>{}}/>)
  expect(html).toContain('autoComplete="username"');expect(html).toContain('minLength="12"')
  expect(html).toContain('Continue as guest');expect(html).not.toContain('Accounts are coming soon')
  expect(html).not.toMatch(/<button[^>]*type="submit"[^>]*disabled/)
  await auth.refresh()
  const loggedIn=renderToStaticMarkup(<AccountDialog controller={auth} intent="save" onClose={()=>{}} onGuest={()=>{}}/>)
  expect(loggedIn).toContain('test_user');expect(loggedIn).toContain('Room saving is the next step')
  expect(loggedIn).not.toContain('type="password"')
 })
})
