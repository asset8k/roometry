import { improvementLabels } from '../../lib/optimization'
import type { OptimizationRun } from '../../lib/optimization'
import type { RoomScene } from '../../lib/scene.generated'
import { aestheticCriteria } from './AestheticBreakdown'

const number = (value: number) => value.toFixed(1).replace(/\.0$/, '')
const words = (value: string) => value.replaceAll('_', ' ')
const materialLabels: Record<string, string> = { wood: 'Wood', metal: 'Metal', linen: 'Fabric', inset: 'Accent panels', rubber: 'Trim', enamel: 'Enamel' }

export function sceneChanges(original: RoomScene, improved: RoomScene) {
  return improved.objects.flatMap(object => {
    const before = original.objects.find(item => item.id === object.id)
    if (!before) return [{ id: object.id, type: words(object.type), details: ['Added catalog furniture'] }]
    const details: string[] = []
    if (object.asset_id !== before.asset_id || JSON.stringify(object.dimensions_m) !== JSON.stringify(before.dimensions_m)) details.push('Replaced with a catalog model')
    const distance = Math.hypot(object.position.x - before.position.x, object.position.z - before.position.z)
    if (distance > .001) details.push(`Moved ${distance < .01 ? '<0.01' : distance.toFixed(2).replace(/0+$/, '').replace(/\.$/, '')} m${object.support_object_id ? ' with its supporting furniture' : ''}`)
    const angle = Math.abs(Math.atan2(Math.sin(object.rotation_y_rad - before.rotation_y_rad), Math.cos(object.rotation_y_rad - before.rotation_y_rad))) * 180 / Math.PI
    if (angle > .1) details.push(`Turned ${angle < 1 ? number(angle) : Math.round(angle)}°`)
    if (object.color.toUpperCase() !== before.color.toUpperCase()) details.push(`Main color: ${before.color} → ${object.color}`)
    for (const field of ['wood', 'metal', 'linen', 'inset', 'rubber', 'enamel'] as const) {
      const a = before.material_colors?.[field], b = object.material_colors?.[field]
      if ((a ?? '').toUpperCase() !== (b ?? '').toUpperCase()) details.push(`${materialLabels[field]}: ${a ?? 'model default'} → ${b ?? 'model default'}`)
    }
    return details.length ? [{ id: object.id, type: words(object.type), details }] : []
  }).concat(original.objects.filter(object => !improved.objects.some(item => item.id === object.id))
    .map(object => ({ id: object.id, type: words(object.type), details: ['Removed from proposed layout'] })))
}

export default function ImprovementComparison({ run, labels = {} }: { run: OptimizationRun; labels?: Readonly<Record<string, string>> }) {
  if (!run.result_scene) return null
  const changes = sceneChanges(run.source_scene, run.result_scene)
  const compatible = run.baseline_assessment?.policy_version === run.result_assessment?.policy_version
  const review = [...run.attempts].reverse().find(attempt => attempt.outcome === 'accepted')?.review
  return <section className="improvement-comparison" aria-labelledby="comparison-summary-heading">
    <div className="comparison-summary-heading"><h3 id="comparison-summary-heading">What changed</h3><span>{run.instruction ? 'Chat refinement' : `Focus: ${improvementLabels[run.focus]}`}</span></div>
    {run.status !== 'completed' && <p className="analysis-caution">This run stopped early. The comparison shows an earlier validated candidate that was retained.</p>}
    {run.source_status === 'partial' && <p className="analysis-caution">Both views use the same partial reconstruction. Missing or uncertain room elements remain unresolved.</p>}
    <p>These estimates compare the submitted starting scene with this candidate. Later manual edits are not included.</p>
    <div className="comparison-table-scroll"><table className="comparison-scores"><caption>Saved room estimates, out of 10</caption>
      <thead><tr><th scope="col">Category</th><th scope="col">Starting scene</th><th scope="col">Candidate</th><th scope="col">Change</th></tr></thead>
      <tbody>{(['openness', 'storage', 'workspace', 'aesthetics'] as const).map(key => {
        const a = run.baseline_assessment?.[key], b = run.result_assessment?.[key]
        const before = a?.score ?? null, after = b?.score ?? null
        const delta = compatible && before !== null && after !== null ? Math.round((after - before) * 10) / 10 : null
        const score = (card: typeof a) => card?.score != null ? `${number(card.score)} / 10` : card?.status === 'not_applicable' ? 'Not applicable' : 'Not assessed'
        return <tr key={key}><th scope="row">{improvementLabels[key]}{!run.instruction && run.focus === key && <span className="score-focus">Selected focus</span>}</th>
          <td>{score(a)}{a?.confidence === 'low' && before !== null && <span className="score-note">Limited evidence</span>}</td>
          <td>{key === 'aesthetics' && run.focus === 'aesthetics' && after === null && review ? 'Reviewed qualitatively' : score(b)}{b?.confidence === 'low' && after !== null && <span className="score-note">Limited evidence</span>}</td>
          <td>{delta === null ? '—' : delta > 0 ? `+${number(delta)}` : delta === 0 ? 'No change' : number(delta)}</td></tr>
      })}</tbody></table></div>
    {!compatible && run.baseline_assessment && run.result_assessment && <p>The rating policies differ, so numerical changes are not compared.</p>}
    {review && <p><strong>Review:</strong> {review.explanation}{run.result_assessment?.aesthetics.score == null &&
      (review.aesthetics ? ' Rendered rubric details below are separate from the original photo rating.' : ' No new numerical taste rating is implied.')}</p>}
    {run.focus === 'aesthetics' && review?.aesthetics && <details className="assessment-breakdown"><summary>Rendered aesthetics comparison</summary>
      <p>The previously retained layout and this candidate were judged together using the same simplified views. These estimates are separate from the original photo rating.</p>
      {review.aesthetics.confidence === 'low' && <p>Limited evidence; no numerical improvement is claimed.</p>}
      <table><caption>Five-part design rubric, each aspect out of 2</caption><thead><tr><th scope="col">Aspect</th><th scope="col">Previously retained</th><th scope="col">Candidate</th></tr></thead>
        <tbody>{aestheticCriteria.map(([key, label]) => <tr key={key}><th scope="row">{label}</th>
          <td>{review.aesthetics!.retained[key]} / 2</td><td>{review.aesthetics!.candidate[key]} / 2</td></tr>)}</tbody>
        <tfoot><tr><th scope="row">Rendered total</th>
          <td>{aestheticCriteria.reduce((sum, [key]) => sum + review.aesthetics!.retained[key], 0)} / 10</td>
          <td>{aestheticCriteria.reduce((sum, [key]) => sum + review.aesthetics!.candidate[key], 0)} / 10</td></tr></tfoot>
      </table><p>{review.aesthetics.explanation}</p>
    </details>}
    <details className="comparison-changes"><summary>{changes.length} objects changed</summary><ul>{changes.map(change => <li key={change.id}>
      <strong>{labels[change.id] ? words(labels[change.id]) : change.type}</strong>: {change.details.join(' · ')}
    </li>)}</ul></details>
  </section>
}
