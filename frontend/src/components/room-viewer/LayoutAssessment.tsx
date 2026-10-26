import { useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import { LayoutAssessmentController } from '../../lib/layout-assessment'
import type { AssessmentState } from '../../lib/layout-assessment'
import type { ScoreLayoutRequest } from '../../lib/layout-scoring'
import type { RoomScene } from '../../lib/scene.generated'
import HelpTip from '../ui/HelpTip'

const capitalize = (value: string) => value.charAt(0).toUpperCase() + value.slice(1)

const routeLabels = { clear: 'Walking routes clear', narrow: 'Narrow walking passages', blocked: 'Walking routes blocked', unassessed: 'Walking routes not assessed' }

export function LayoutAssessmentDetails({ state, scene, retry }: { state: AssessmentState; scene: RoomScene; retry: () => void }) {
  const report = state.report, geometry = report?.geometry
  const walking = geometry?.walking_space, doors = geometry?.door_clearance
  const name = (id: string) => capitalize(scene.objects.find(obj => obj.id === id)?.type.replaceAll('_', ' ')
    ?? (scene.openings.some(opening => opening.id === id) ? `${scene.openings.find(opening => opening.id === id)!.wall} door` : 'room item'))
  const blockedDoor = doors?.doors.some(door => door.access_status === 'blocked' || door.swing_status === 'blocked')
  const needsAttention = blockedDoor || walking?.status === 'blocked' || walking?.status === 'narrow' || geometry?.valid === false
  const summary = state.status === 'loading' ? 'Checking…' : state.status === 'error' ? 'Unavailable'
    : blockedDoor ? 'Door access needs attention' : walking ? routeLabels[walking.status] : 'Checks available'
  return <details className="layout-assessment" aria-label="Layout checks">
    <summary><span>Layout checks</span><span className={needsAttention ? 'check-warning' : 'check-summary'} role="status">{summary}</span></summary>
    <div className="layout-check-details">
      <div className="layout-check-heading"><h3>Clearance & access</h3><HelpTip label="About layout checks">Approximate checks of this layout. Existing problems stay visible; edits that worsen checked access are rejected. These checks do not measure photo accuracy.</HelpTip></div>
      {state.status === 'error' && <p>Your room is still available. <button type="button" onClick={retry}>Retry checks</button></p>}
      {report && <>
        <ul>{doors?.doors.map(door => <li key={door.door_id}>
          <strong>{name(door.door_id)}</strong>: {door.access_status === 'clear' ? 'Access clear' : door.access_status === 'blocked' ? 'Access blocked' : 'Access not assessed'}
          {(door.access_object_ids ?? []).length > 0 && ` by ${(door.access_object_ids ?? []).map(name).join(', ')}`}.{' '}
          {door.swing_status === 'unknown' ? 'Door swing unknown.' : door.swing_status === 'clear' ? 'Swing clear.'
            : door.swing_status === 'blocked' ? `Swing blocked${(door.swing_object_ids ?? []).length ? ` by ${(door.swing_object_ids ?? []).map(name).join(', ')}` : ''}.`
            : door.swing_status === 'outside_room' ? 'Outward swing not checked.' : door.swing_status === 'not_applicable' ? 'No swing space needed.' : 'Swing not assessed.'}
        </li>)}</ul>
        {walking && <p>{routeLabels[walking.status]}. Minimum width {walking.policy.minimum_width_m} m; {walking.policy.preferred_width_m} m preferred.</p>}
        {report.workstations.length > 0 && <ul>{report.workstations.map(desk => <li key={desk.object_id}>
          <strong>{name(desk.object_id)}</strong>: working side {desk.approach_inside_room && desk.approach_clear ? 'clear' : 'needs more room'}
          {desk.blocking_object_ids.length > 0 && ` (${desk.blocking_object_ids.map(name).join(', ')})`}.
        </li>)}</ul>}
        {walking?.entrances.filter(entry => (entry.unreachable_door_ids ?? []).length > 0).map(entry => <p key={entry.door_id}>
          No clear route from {name(entry.door_id)} to {(entry.unreachable_door_ids ?? []).map(name).join(', ')}.
        </p>)}
      </>}
    </div>
  </details>
}

export default function LayoutAssessment({ scene, goals }: { scene: RoomScene; goals?: ScoreLayoutRequest['goals'] }) {
  const controller = useMemo(() => new LayoutAssessmentController(), [])
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot)
  const [revision, setRevision] = useState(0)
  const goalKey = JSON.stringify(goals ?? ['general_analysis'])
  useEffect(() => {
    void controller.load(scene, JSON.parse(goalKey) as ScoreLayoutRequest['goals'])
    return controller.cancel
  }, [scene, goalKey, controller, revision])
  return <LayoutAssessmentDetails state={state} scene={scene} retry={() => setRevision(value => value + 1)} />
}
