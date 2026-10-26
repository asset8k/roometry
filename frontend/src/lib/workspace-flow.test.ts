/** Whole product journey with deterministic adapters; never contacts a provider. */
import {describe,expect,it,vi} from 'vitest'
import {RoomInputController} from './room-input-controller'
import {RoomGenerationController} from './room-generation-controller'
import {OptimizationController} from './optimization-controller'
import {SceneComparison} from './scene-comparison'
import {previewTransform} from './scene-edits'
import type {AgentClient} from './agent-api'
import type {OptimizationClient,OptimizationRun} from './optimization'
import {finishedRun,waitingRun} from './test-support/agent-run'
import {preparedImprovement} from './test-support/optimization'
import {geometryReport} from './test-support/edit-responses'
import {limits,photo,prepared} from './test-support/room-input'

describe('unified workspace journey',()=>{
 it.each([false,true])('preserves a %s-partial reconstruction through questions, manual edits, improvement, refinement and rejection',async(partial)=>{
  let batch=prepared([photo()])
  const upload=vi.fn(async(files:readonly File[],options:typeof batch.options)=>{batch=prepared(files);batch.options=options;return batch})
  const input=new RoomInputController({limits:async()=>limits,upload,discard:vi.fn(async()=>{})},{create:()=> 'blob:room',revoke:vi.fn()})
  const agent:AgentClient={start:vi.fn(async()=>waitingRun(batch)),read:vi.fn(async()=>finishedRun(batch,partial)),find:vi.fn(async()=>waitingRun(batch)),
   answer:vi.fn(async(_id,answer)=>({...finishedRun(batch,partial),questions:waitingRun(batch).questions,
    submitted_answers:{q1:{...answer,dimensions:{width_m:null,length_m:null,height_m:2.6}}},clarification_answers:{q1:answer.choice_id}})),cancel:vi.fn()}
  const generation=new RoomGenerationController(input,agent)
  const disconnect=generation.connect()
  try{
   await input.loadLimits();input.addPhotos([photo()]);input.change({width:'4',length:'4',height:''})
   await generation.generate()
   expect(generation.getSnapshot().phase).toBe('awaiting_answer')
   await generation.answer({question_id:'q1',choice_id:'enter_measurements',dimensions:{height_m:2.6}})
   const source=generation.getSnapshot().run!
   expect(generation.getSnapshot().phase).toBe(partial?'partial':'completed')
   const original=structuredClone(source.working_scene!)
   const comparison=new SceneComparison(source.working_scene!,async(scene,edit)=>({accepted:true,scene:previewTransform(scene,edit),changed_object_ids:['desk_observed','computer_observed'],edit_errors:[],base_validation:geometryReport(),candidate_validation:geometryReport()}))
   comparison.editCopy()
   const editor=comparison.getSnapshot().editor
   editor.select('desk_observed')
   expect(await editor.submit({operation:'move',object_id:'desk_observed',position:{x:2.8,z:.6}})).toBe(true)
   comparison.setColor('chair_a','#665544');comparison.toggleLock('desk_observed')
   let run:OptimizationRun,requests=0
   const client:OptimizationClient={
    prepare:vi.fn(async(_,request)=>{
     requests++
     run=preparedImprovement(source,request)
     run.run_id=requests===1?'88888888-8888-4888-8888-888888888888':requests===2?'99999999-9999-4999-8999-999999999999':'77777777-7777-4777-8777-777777777777'
     run.source_scene=structuredClone(request.context!.scene);run.baseline_assessment=null
     return structuredClone(run)
    }),
    execute:vi.fn(async()=>{
     run.stage='finished';run.usage.model_calls=2
     if(requests===3){run.status='no_improvement';run.message='The placement was rejected.';return structuredClone(run)}
     run.status='completed';run.result_scene=structuredClone(run.source_scene)
     run.result_scene.objects.find(o=>o.id==='chair_a')!.color=requests===1?'#887766':'#556677'
     run.attempts=[{number:1,outcome:'accepted',before_score:null,after_score:null,changed_object_ids:['chair_a'],issues:[],summary:'Update the chair palette.',review:null}]
     return structuredClone(run)
    }),read:vi.fn(async()=>structuredClone(run)),cancel:vi.fn(),
   }
   const optimizer=new OptimizationController(source,client)
   optimizer.select('aesthetics');optimizer.setContext(comparison.currentContext());await optimizer.start()
   let result=optimizer.getSnapshot().result!
   expect(comparison.offer(result,result.source_scene)).toBe(true)
   expect(comparison.getSnapshot().view).toBe('improved')
   const firstId=result.run_id
   await optimizer.refine('Use a cooler chair color.',comparison.currentContext())
   expect(vi.mocked(client.prepare).mock.calls[1][1].context!.parent_run_id).toBe(firstId)
   result=optimizer.getSnapshot().result!
   expect(comparison.offer(result,result.source_scene)).toBe(true)
   const retained=structuredClone(comparison.getSnapshot().editor.getSnapshot().scene)
   await optimizer.refine('Try an invalid placement.',comparison.currentContext())
   result=optimizer.getSnapshot().result!
   expect(comparison.offer(result,result.source_scene)).toBe(false)
   expect(comparison.getSnapshot().editor.getSnapshot().scene).toEqual(retained)
   expect(retained.objects.find(o=>o.id==='desk_observed')).toMatchObject({locked:true,position:{x:2.8,z:.6}})
   expect(retained.objects.find(o=>o.id==='computer_observed')!.position.x).toBe(2.8)
   comparison.show('original');expect(comparison.getSnapshot().editor.getSnapshot().scene).toEqual(original)
   comparison.show('editing');expect(comparison.getSnapshot().colors.chair_a).toBe('#665544')
   expect(source.working_scene).toEqual(original)
   expect(upload).toHaveBeenCalledTimes(1);expect(agent.start).toHaveBeenCalledTimes(1);expect(agent.answer).toHaveBeenCalledTimes(1)
   expect(client.prepare).toHaveBeenCalledTimes(3);expect(client.execute).toHaveBeenCalledTimes(3)
  }finally{disconnect();input.release()}
 })
})
