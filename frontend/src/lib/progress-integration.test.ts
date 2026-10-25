import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { RoomInputController } from './room-input-controller'
import { RoomGenerationController } from './room-generation-controller'
import { OptimizationController } from './optimization-controller'
import type { AgentClient } from './agent-api'
import type { OptimizationClient, OptimizationRun } from './optimization'
import type { ProgressHooks, ProgressObserver, RunProgress } from './run-progress'
import { agentRun, finishedRun, waitingRun } from './test-support/agent-run'
import { preparedImprovement } from './test-support/optimization'
import { limits, photo, prepared } from './test-support/room-input'
const cleanups: (()=>void)[]=[]
beforeEach(()=>vi.useFakeTimers())
afterEach(()=>{cleanups.splice(0).forEach(fn=>fn());vi.useRealTimers()})
function deferred<T>(){let resolve!:(value:T)=>void, reject!:(error:Error)=>void;const promise=new Promise<T>((yes,no)=>{resolve=yes;reject=no});return{promise,resolve,reject}}
function observation(){
 const sessions:{hooks:ProgressHooks;stop:ReturnType<typeof vi.fn>;id:string}[]=[]
 const observe:ProgressObserver=vi.fn((_path,id,_kind,hooks)=>{const stop=vi.fn();sessions.push({hooks,stop,id});hooks.mode('streaming');return stop})
 return{observe,sessions}
}
function progress(id:string,patch:Partial<RunProgress>={}):RunProgress{return{version:1,run_id:id,kind:'reconstruction',sequence:1,stage:'analyzing',status:'running',step:1,candidate:null,closed:false,...patch}}
async function generation(){
 let batch=prepared()
 const inputs=new RoomInputController({limits:async()=>limits,upload:async(files,options)=>{batch={...prepared(files),options};return batch},discard:async()=>{}},{create:()=> 'blob:photo',revoke:()=>{}})
 const client:AgentClient={start:vi.fn(async()=>agentRun(batch)),find:vi.fn(async()=>agentRun(batch)),read:vi.fn(async()=>agentRun(batch)),answer:vi.fn(async()=>finishedRun(batch)),cancel:vi.fn(async()=>agentRun(batch,{status:'cancelled',pending_tool:null}))}
 const watch=observation(), controller=new RoomGenerationController(inputs,client,Date.now,1000,watch.observe)
 cleanups.push(controller.connect());await inputs.loadLimits();inputs.addPhotos([photo()])
 await controller.generate()
 return{controller,client,watch,batch:()=>batch}
}
describe('progress observation and authoritative snapshot integration',()=>{
 it('stops routine polling while connected and opens the final snapshot without another start',async()=>{
  const s=await generation();await vi.advanceTimersByTimeAsync(5000)
  expect(s.client.read).not.toHaveBeenCalled();expect(s.watch.sessions).toHaveLength(1)
  vi.mocked(s.client.read).mockResolvedValue(finishedRun(s.batch()))
  s.watch.sessions[0].hooks.event(progress(s.watch.sessions[0].id,{stage:'finished',status:'completed',closed:true}))
  await vi.advanceTimersByTimeAsync(0)
  expect(s.controller.getSnapshot().phase).toBe('completed')
  expect(s.watch.sessions[0].stop).toHaveBeenCalledTimes(1)
  expect(s.client.start).toHaveBeenCalledTimes(1)
 })
 it('uses read-only polling during reconnect and never restarts the paid request',async()=>{
  const s=await generation();s.watch.sessions[0].hooks.mode('reconnecting')
  await vi.advanceTimersByTimeAsync(1100)
  expect(s.client.read).toHaveBeenCalledTimes(1);expect(s.client.start).toHaveBeenCalledTimes(1)
  s.watch.sessions[0].hooks.mode('streaming');await vi.advanceTimersByTimeAsync(3000)
  expect(s.client.read).toHaveBeenCalledTimes(1)
  s.watch.sessions[0].hooks.mode('polling');await vi.advanceTimersByTimeAsync(1100)
  expect(s.client.read).toHaveBeenCalledTimes(2)
 })
 it('coalesces updates during an in-flight GET without losing the final result',async()=>{
  const s=await generation(), first=deferred<ReturnType<typeof agentRun>>()
  vi.mocked(s.client.read).mockReturnValueOnce(first.promise).mockResolvedValue(finishedRun(s.batch()))
  const session=s.watch.sessions[0]
  session.hooks.event(progress(session.id));session.hooks.event(progress(session.id,{sequence:2,stage:'finished',status:'completed',closed:true}))
  expect(s.client.read).toHaveBeenCalledTimes(1)
  first.resolve(agentRun(s.batch()));await vi.advanceTimersByTimeAsync(0)
  expect(s.client.read).toHaveBeenCalledTimes(2)
  expect(s.controller.getSnapshot().phase).toBe('completed')
 })
 it('pauses and resumes the same run, ignoring callbacks from its old subscription',async()=>{
  const s=await generation(), old=s.watch.sessions[0]
  vi.mocked(s.client.read).mockResolvedValue(waitingRun(s.batch()))
  old.hooks.event(progress(old.id,{stage:'clarifying',status:'awaiting_answer'}));await vi.advanceTimersByTimeAsync(0)
  expect(s.controller.getSnapshot().phase).toBe('awaiting_answer');expect(old.stop).toHaveBeenCalledTimes(1)
  const answer=deferred<ReturnType<typeof finishedRun>>()
  vi.mocked(s.client.answer).mockReturnValue(answer.promise)
  const pending=s.controller.answer({question_id:'q1',choice_id:'leave_unknown'})
  expect(s.watch.sessions).toHaveLength(2)
  const count=vi.mocked(s.client.read).mock.calls.length
  old.hooks.unavailable();old.hooks.event(progress(old.id,{sequence:99,stage:'expired',status:'expired',closed:true}))
  expect(s.client.read).toHaveBeenCalledTimes(count)
  answer.resolve(finishedRun(s.batch()));await pending
  expect(s.controller.getSnapshot().phase).toBe('completed')
  expect(s.client.start).toHaveBeenCalledTimes(1);expect(s.client.answer).toHaveBeenCalledTimes(1)
 })
 it('retains cancellation and stops observation on unmount',async()=>{
  const s=await generation();await s.controller.cancel()
  expect(s.controller.getSnapshot().phase).toBe('cancelled')
  expect(s.watch.sessions[0].stop).toHaveBeenCalledTimes(1)
  expect(s.client.cancel).toHaveBeenCalledTimes(1);expect(s.client.start).toHaveBeenCalledTimes(1)
 })
 it('recovers an optimization result over SSE when the execute response is lost',async()=>{
  const source=finishedRun(prepared()), execution=deferred<OptimizationRun>()
  let child:OptimizationRun
  const client:OptimizationClient={prepare:vi.fn(async(_id,request)=>child=preparedImprovement(source,request)),read:vi.fn(async()=>({...child,status:'no_improvement' as const,stage:'finished' as const})),execute:vi.fn(()=>execution.promise),cancel:vi.fn(async()=>({...child,status:'cancelled' as const,stage:'finished' as const}))}
  const watch=observation(), controller=new OptimizationController(source,client,Date.now,watch.observe)
  cleanups.push(controller.connect());controller.select('openness')
  const pending=controller.start();await vi.advanceTimersByTimeAsync(0)
  expect(watch.sessions).toHaveLength(1);expect(client.execute).toHaveBeenCalledTimes(1)
  await vi.advanceTimersByTimeAsync(4000);expect(client.read).not.toHaveBeenCalled()
  watch.sessions[0].hooks.event(progress(watch.sessions[0].id,{kind:'optimization',stage:'finished',status:'no_improvement',closed:true,candidate:1}))
  await vi.advanceTimersByTimeAsync(0);expect(controller.getSnapshot().phase).toBe('no_improvement')
  execution.reject(new Error('Lost POST response'));await pending
  expect(controller.getSnapshot().phase).toBe('no_improvement')
  expect(client.prepare).toHaveBeenCalledTimes(1);expect(client.execute).toHaveBeenCalledTimes(1)
  expect(watch.sessions[0].stop).toHaveBeenCalledTimes(1)
 })
})
