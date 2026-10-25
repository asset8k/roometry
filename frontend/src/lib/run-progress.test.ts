import { afterEach, describe, expect, it, vi } from 'vitest'
import schema from './run-progress.schema.json'
import { progressStages, createProgressObserver, parseProgress, reconstructionEvents } from './run-progress'
import type { ProgressHooks, RunProgress } from './run-progress'
const id = '66666666-6666-4666-8666-666666666666'
const event = (sequence = 1, patch: Partial<RunProgress> = {}): RunProgress => ({version:1,run_id:id,sequence,kind:'reconstruction',stage:'constructing',status:'running',step:3,candidate:null,closed:false,...patch})
class Source {
  listeners = new Map<string, EventListener>()
  onopen: (()=>void) | null = null
  onerror: (()=>void) | null = null
  close = vi.fn()
  addEventListener(name: string, listener: EventListener) { this.listeners.set(name,listener) }
  emit(name: string, value: RunProgress | string = event()) { this.listeners.get(name)?.({data: typeof value === 'string' ? value : JSON.stringify(value),lastEventId:typeof value === 'string'?'':`${value.run_id}:${value.sequence}`} as unknown as Event) }
}
const cleanups: (()=>void)[] = []
afterEach(()=>{cleanups.splice(0).forEach(fn=>fn());vi.useRealTimers()})
function setup() {
 const source = new Source(), factory = vi.fn(()=>source as unknown as EventSource)
 const hooks: ProgressHooks = {event:vi.fn(),mode:vi.fn(),unavailable:vi.fn()}
 cleanups.push(createProgressObserver(factory)(reconstructionEvents(id),id,'reconstruction',hooks))
 return {source,factory,hooks}
}
describe('read-only public SSE transport',()=>{
 it('matches the exported backend public protocol',()=>{
  expect(Object.keys(schema.properties).sort()).toEqual(Object.keys(event()).sort())
  expect(schema.required.sort()).toEqual(Object.keys(event()).sort())
  expect([...schema.properties.stage.enum].sort()).toEqual([...progressStages].sort())
 })
 it('uses credentialed GET observation and deduplicates old/repeated events',()=>{
  const {source,factory,hooks}=setup()
  expect(factory).toHaveBeenCalledWith(`/api/agent-runs/${id}/events`,{withCredentials:true})
  source.onopen?.();source.emit('snapshot',event());source.emit('progress',event());source.emit('progress',event(3));source.emit('progress',event(2))
  expect(hooks.event).toHaveBeenCalledTimes(2)
  source.emit('snapshot',event(3)) // Reconnect always requests authoritative reconciliation.
  expect(hooks.event).toHaveBeenCalledTimes(3)
 })
 it('allows bounded native reconnections then falls back without any mutation',()=>{
  const {source,hooks}=setup()
  source.onerror?.();source.onerror?.()
  expect(hooks.mode).toHaveBeenLastCalledWith('reconnecting')
  expect(source.close).not.toHaveBeenCalled()
  source.onerror?.()
  expect(source.close).toHaveBeenCalledTimes(1)
  expect(hooks.mode).toHaveBeenLastCalledWith('polling')
  source.emit('progress',event(9));expect(hooks.event).not.toHaveBeenCalled()
 })
 it('detects a silent connection, but keepalives extend the observation window',()=>{
  vi.useFakeTimers();const {source,hooks}=setup()
  vi.advanceTimersByTime(20_000);source.emit('heartbeat','{}');vi.advanceTimersByTime(20_000)
  expect(source.close).not.toHaveBeenCalled()
  vi.advanceTimersByTime(5_000)
  expect(hooks.mode).toHaveBeenLastCalledWith('polling')
 })
 it('rejects malformed, oversized, private, and cross-run progress',()=>{
  for(const bad of ['not json',JSON.stringify({...event(),arguments:'private'}),JSON.stringify(event(1,{run_id:'77777777-7777-4777-8777-777777777777'})),'x'.repeat(5000)]){
   const {source,hooks}=setup();source.emit('progress',bad)
   expect(hooks.event).not.toHaveBeenCalled();expect(hooks.mode).toHaveBeenLastCalledWith('polling')
  }
  expect(()=>parseProgress(JSON.stringify(event()),id,`${id}:99`)).toThrow()
 })
 it('refreshes the terminal snapshot and stops updates on unavailable or cleanup',()=>{
  const a=setup();a.source.emit('progress',event(2,{stage:'finished',status:'partial',closed:true}))
  expect(a.hooks.event).toHaveBeenCalled();expect(a.source.close).toHaveBeenCalledTimes(1)
  const b=setup();b.source.emit('unavailable','{}');expect(b.hooks.unavailable).toHaveBeenCalledTimes(1)
  const c=setup();cleanups.pop()!();c.source.emit('snapshot',event());expect(c.hooks.event).not.toHaveBeenCalled()
 })
})
