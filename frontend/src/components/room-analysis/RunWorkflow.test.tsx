import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import RunWorkflow from './RunWorkflow'
import { waitingRun, finishedRun } from '../../lib/test-support/agent-run'
import { prepared } from '../../lib/test-support/room-input'

describe('public workflow display',()=>{
 it('pauses on a real question without an invented percentage',()=>{
  const run=waitingRun(prepared())
  const html=renderToStaticMarkup(<RunWorkflow kind="reconstruction" run={run} paused message="Waiting for your answer."/>)
  expect(html).toContain('Room generation stages')
  expect(html).toContain('waiting for your answer')
  for (const label of ['Photo Analysis', 'Scene Construction', 'Geometry Checks', 'Visual Review', 'Room Ratings']) expect(html).toContain(label)
  expect(html).not.toContain('<progress')
  expect(html).not.toContain('value="100"')
 })
 it('only marks evidenced work complete and keeps an unavailable review explicit',()=>{
  const run=finishedRun(prepared(),true)
  run.proposal!.visual_review={status:'unavailable',repair:'not_attempted',message:'Review unavailable',reviewed_scene_hash:'a'.repeat(64),result_scene_hash:'a'.repeat(64),judgment:null,applied_findings:[]}
  const html=renderToStaticMarkup(<RunWorkflow kind="reconstruction" run={run} message="Checking your room…" streamMode="reconnecting"/>)
  expect(html).toContain('data-state="limited"')
  expect(html).toContain('Reconnecting… your room keeps running.')
  expect(html).toContain('aria-label="Room generation progress"')
 })
})
