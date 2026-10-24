import type { AgentState } from '../../lib/agent-run'

type Assessment = NonNullable<AgentState['room_assessment']>
const categories = [
  { key: 'openness', label: 'Space & movement', action: 'Improve space' },
  { key: 'storage', label: 'Storage & organization', action: 'Improve storage' },
  { key: 'workspace', label: 'Workspace', action: 'Improve workspace' },
  { key: 'aesthetics', label: 'Aesthetics & cohesion', action: 'Improve aesthetics' },
] as const

export default function RoomAssessmentCards({ assessment }: { assessment: Assessment | null }) {
  return <section className="room-assessment" aria-labelledby="assessment-heading">
    <span className="section-label">Understand your space</span>
    <h2 id="assessment-heading">Your room at a glance</h2>
    <p>Estimated ratings of your original reconstruction. Manual edits are not included in these ratings.</p>
    {!assessment ? <p role="status">Room ratings are unavailable for this run. Your reconstructed room is still available below.</p> : <>
      <div className="assessment-grid">{categories.map(({ key, label, action }) => {
        const card = assessment[key]
        const recommended = assessment.recommended === key
        const score = card.score === null ? null : card.score.toFixed(1).replace(/\.0$/, '')
        return <article key={key} className={`assessment-card${recommended ? ' recommended' : ''}`}>
          <div className="assessment-card-heading"><h3>{label}</h3>{recommended && <span className="assessment-recommendation">Recommended</span>}</div>
          <p className="assessment-rating" aria-label={`${label}: ${score === null ? 'not rated' : `${score} out of 10`}`}>
            {score === null ? <span className="assessment-unrated">{key === 'workspace' && card.status === 'not_applicable' ? 'No dedicated workspace' : 'Not assessed'}</span>
              : <>{score}<span> / 10</span></>}
          </p>
          {card.status === 'rated' && <span className="assessment-confidence">{card.confidence === 'low' ? 'Limited evidence' : card.source === 'photos' ? 'Photo-based estimate' : 'Model-based estimate'}</span>}
          <p className="assessment-explanation">{card.explanation}</p>
          <button type="button" disabled aria-describedby="assessment-actions-note">{key === 'workspace' && card.status === 'not_applicable' ? 'Create a workspace' : action}</button>
        </article>
      })}</div>
      <p id="assessment-actions-note" className="input-help">Improvement actions will be available soon. You can explore and edit your room below.</p>
      <details className="assessment-notes"><summary>About these ratings</summary><ul>{assessment.notes.map((note, i) => <li key={i}>{note}</li>)}
        <li>Aesthetics uses a subjective five-part rubric for harmony, materials, balance, visual order and lighting.</li>
        <li>A low rating highlights a possible opportunity, not an instruction to change your room.</li>
      </ul></details>
    </>}
  </section>
}
