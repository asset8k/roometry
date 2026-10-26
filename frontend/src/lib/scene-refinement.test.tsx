import { describe, expect, it, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { OptimizationController } from './optimization-controller'
import { SceneComparison } from './scene-comparison'
import type { OptimizationClient, OptimizationRun, StartOptimizationRequest } from './optimization'
import { preparedImprovement } from './test-support/optimization'
import { finishedRun } from './test-support/agent-run'
import { prepared } from './test-support/room-input'
import { loadExampleScene } from './test-support/scenes'
import RoomImprovementWorkspace from '../components/room-analysis/RoomImprovementWorkspace'

function setup() {
 const source = finishedRun(prepared())
 source.working_scene = loadExampleScene()
 const comparison = new SceneComparison(source.working_scene)
 comparison.editCopy()
 let run: OptimizationRun
 const client: OptimizationClient = {
  prepare: vi.fn(async (_, request: StartOptimizationRequest) => {
   run = preparedImprovement(source, request)
   run.source_scene = structuredClone(request.context?.scene ?? source.working_scene!)
   run.baseline_assessment = null
   return structuredClone(run)
  }),
  execute: vi.fn(async () => {
   run.status = 'completed'; run.stage = 'finished'; run.result_scene = structuredClone(run.source_scene)
   run.result_scene.objects.find(o => !o.fixed && !o.locked)!.color = '#CC8866'
   run.attempts = [{number:1,outcome:'accepted',before_score:null,after_score:null,changed_object_ids:[],issues:[],summary:'A warmer palette.',review:null}]
   return structuredClone(run)
  }), read: vi.fn(async () => structuredClone(run)), cancel: vi.fn(async () => ({...run,status:'cancelled' as const})),
 }
 const controller = new OptimizationController(source,client)
 return {source,comparison,controller,client}
}

describe('current-scene refinement',()=>{
 it('keeps an object’s manual color when it is locked before a refinement',()=>{
  const {comparison}=setup()
  comparison.setColor('desk_1','#445566')
  comparison.toggleLock('desk_1')
  const object=comparison.currentContext().scene.objects.find(o=>o.id==='desk_1')!
  expect(object.locked).toBe(true)
  expect(object.color).toBe('#445566')
  comparison.toggleLock('desk_1')
  expect(comparison.currentContext().scene.objects.find(o=>o.id==='desk_1')!.color).toBe('#445566')
 })
 it('captures colors and user locks once and reuses the bounded improvement transport',async()=>{
  const {comparison,controller,client,source}=setup()
  comparison.setColor('rug_1','#AA9988')
  const editor = comparison.getSnapshot().editor
  editor.toggleLock('desk_1')
  const context=comparison.currentContext(), original=structuredClone(source.working_scene)
  await controller.refine('  Use warmer colors.  ',context)
  const request=vi.mocked(client.prepare).mock.calls[0][1]
  expect(request.instruction).toBe('Use warmer colors.')
  expect(request.context?.scene.objects.find(o=>o.id==='desk_1')?.locked).toBe(true)
  expect(request.context?.scene.objects.find(o=>o.id==='rug_1')?.color).toBe('#AA9988')
  expect(client.execute).toHaveBeenCalledTimes(1)
  expect(source.working_scene).toEqual(original)
  const result=controller.getSnapshot().result!
  expect(comparison.offer(result,result.source_scene)).toBe(true)
  expect(comparison.getSnapshot().view).toBe('improved')
  expect(comparison.currentContext().parent_run_id).toBe(result.run_id)
  expect(comparison.currentContext().scene.objects.find(o=>o.id==='desk_1')?.locked).toBe(true)
 })
 it('retains newer manual colors while making an earlier-snapshot candidate available',async()=>{
  const {comparison,controller}=setup()
  const context=comparison.currentContext()
  await controller.refine('Use warmer colors.',context)
  comparison.setColor('rug_1','#223344')
  const result=controller.getSnapshot().result!
  expect(comparison.offer(result,result.source_scene)).toBe(true)
  expect(comparison.getSnapshot().view).toBe('editing')
  expect(comparison.getSnapshot().colors.rug_1).toBe('#223344')
  expect(comparison.getSnapshot().improvements).toHaveLength(1)
  comparison.show('improved')
  expect(comparison.getSnapshot().view).toBe('improved')
  comparison.show('editing')
  expect(comparison.getSnapshot().colors.rug_1).toBe('#223344')
 })
 it('does not mismatch the viewer and selected version when another saved candidate arrives',async()=>{
  const {comparison,controller}=setup()
  await controller.refine('Use warmer colors.',comparison.currentContext())
  const first=controller.getSnapshot().result!
  comparison.offer(first,first.source_scene)
  const second=structuredClone(first)
  second.run_id='99999999-9999-4999-8999-999999999999'
  second.result_scene!.objects[0].color='#123456'
  // This candidate started from a snapshot other than the now-visible first result.
  expect(comparison.offer(second,second.source_scene)).toBe(true)
  expect(comparison.getSnapshot().selectedImprovementId).toBe(first.run_id)
  expect(comparison.getSnapshot().editor.getSnapshot().scene).toEqual(first.result_scene)
  expect(comparison.getSnapshot().improvements).toHaveLength(2)
 })
 it('recovers an identical scene snapshot and instruction after an interrupted prepare',async()=>{
  const {comparison,controller,client}=setup()
  const prepare=client.prepare
  let first: StartOptimizationRequest | null=null
  client.prepare=vi.fn(async(sourceId,request,signal)=>{
   if (!first) { first=structuredClone(request); throw new Error('connection interrupted') }
   expect(request).toEqual(first)
   return prepare(sourceId,request,signal)
  })
  await controller.refine('Use warmer colors.',comparison.currentContext())
  comparison.setColor('rug_1','#223344')
  expect(controller.getSnapshot().recovery).toBe(true)
  await controller.retry()
  expect(client.prepare).toHaveBeenCalledTimes(2)
  expect(client.execute).toHaveBeenCalledTimes(1)
 })
 it('does not prepare empty or oversized chat requests',async()=>{
  const {comparison,controller,client}=setup()
  await controller.refine(' ',comparison.currentContext())
  await controller.refine('x'.repeat(1001),comparison.currentContext())
  expect(client.prepare).not.toHaveBeenCalled()
 })
 it('keeps the viewer central with compact overview, objects and chat navigation',()=>{
  const {source}=setup()
  const html=renderToStaticMarkup(<RoomImprovementWorkspace run={source} onConnectionChange={()=>{}}/>)
  expect(html).toContain('unified-workspace')
  expect(html).toContain('3D room viewer')
  expect(html).toContain('>Overview</button>')
  expect(html).toContain('>Chat</button>')
  expect(html).toContain('Original')
 })
})
