import { useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import { LayoutAssessmentController } from '../../lib/layout-assessment'
import type { AssessmentState } from '../../lib/layout-assessment'
import type { ScoreLayoutRequest } from '../../lib/layout-scoring'
import type { RoomScene } from '../../lib/scene.generated'

const labels = { openness: 'Free floor space', storage: 'Storage size proxy', workspace: 'Desk usability', aesthetics: 'Style' }
const routeLabels = { clear: 'Routes found', narrow: 'Narrow passages', blocked: 'Routes blocked', unassessed: 'Routes not assessed' }

export function LayoutAssessmentDetails({ state, scene, retry }: { state: AssessmentState; scene: RoomScene; retry: () => void }) {
  const report = state.report
  const name = (id: string) => scene.objects.find(obj => obj.id === id)?.type.replaceAll('_', ' ')
    ?? (scene.openings.some(opening => opening.id === id) ? `${scene.openings.find(opening => opening.id === id)!.wall} door` : id.replaceAll('_', ' '))
  const geometry = report?.geometry
  const walking = geometry?.walking_space
  const doors = geometry?.door_clearance
  return <section className="layout-assessment" aria-label="Layout checks" aria-live="polite">
    <h3>Layout checks</h3>
    {state.status === 'loading' && <p role="status">Checking this layout’s clearance and goal measures…</p>}
    {state.status === 'error' && <p>Layout checks are unavailable. Your room is still available. <button type="button" onClick={retry}>Retry checks</button></p>}
    {report && <>
      <p>Existing clearance problems remain visible. Edits that add obstructions or reduce checked routes are rejected.</p>
      <ul>
        {doors?.doors.map(door => <li key={door.door_id}>
          <strong>{name(door.door_id)}</strong>: {door.access_status === 'clear' ? 'Access zone clear' : door.access_status === 'blocked' ? 'Access zone blocked' : 'Access not assessed'}
          {(door.access_object_ids ?? []).length > 0 && ` (${(door.access_object_ids ?? []).map(name).join(', ')})`}.{' '}
          {door.swing_status === 'unknown' ? 'Swing not assessed: hinge or swing direction unknown.'
            : door.swing_status === 'clear' ? 'Inward swing clear.' : door.swing_status === 'blocked' ? `Inward swing blocked${(door.swing_object_ids ?? []).length ? ` (${(door.swing_object_ids ?? []).map(name).join(', ')})` : ''}.`
            : door.swing_status === 'outside_room' ? 'Outward swing is outside this room’s checks.'
            : door.swing_status === 'not_applicable' ? 'No swing area needed.' : 'Swing not assessed.'}
        </li>)}
        {walking && <li><strong>{routeLabels[walking.status]}</strong> · minimum {walking.policy.minimum_width_m} m, preferred {walking.policy.preferred_width_m} m.
          {walking.entrances.map(entry => <span key={entry.door_id}> {name(entry.door_id)}: {routeLabels[entry.status].toLowerCase()}.{(entry.unreachable_door_ids ?? []).length > 0 && ` No sampled route to ${(entry.unreachable_door_ids ?? []).map(name).join(', ')}.`}</span>)}
        </li>}
      </ul>
      <details><summary>Goal measures and check details</summary>
        <p>Approximate planning checks, based on this model. Scores describe the current layout; they do not measure photo accuracy or produce an improved layout.</p>
        <dl>{report.selected_criteria.map(key => <div key={key}><dt>{labels[key]}</dt><dd>{report[key].score === null ? 'Not assessed' : `${Math.round(report[key].score)} / 100`} — {report[key].explanation}</dd></div>)}</dl>
        {report.overall_score !== null && <p>Selected goal average: {Math.round(report.overall_score)} / 100.</p>}
        {report.overall_score === null && <p>No combined score: at least one selected goal is not assessed.</p>}
        {walking?.grid_step_x_m && <p>Walking grid: {walking.grid_step_x_m.toFixed(2)} × {walking.grid_step_z_m?.toFixed(2)} m. A sampled failure may miss a path between grid points.</p>}
        <ul>{[...(doors?.issues ?? []), ...(walking?.issues ?? [])].map((issue, i) => <li key={i}>{issue.entity_ids.map(name).join(', ')}{issue.entity_ids.length > 0 && ': '}{issue.message}</li>)}</ul>
        {report.workstations.map(desk => <p key={desk.object_id}>{name(desk.object_id)}: approach {desk.approach_inside_room && desk.approach_clear ? 'clear' : 'obstructed or outside the room'}{desk.blocking_object_ids.length > 0 && ` (${desk.blocking_object_ids.map(name).join(', ')})`}.</p>)}
        <ul>{report.warnings.map((warning, i) => <li key={i}>{warning}</li>)}</ul>
      </details>
    </>}
  </section>
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
