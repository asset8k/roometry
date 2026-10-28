import type { Focus } from '../../lib/optimization'
import { AestheticBreakdown } from './AestheticBreakdown'
import { readableText } from '../../lib/display-text'
import { formatRating, ratingChange } from '../../lib/assessment-display'
import type { Assessment, RatingLayout } from '../../lib/assessment-display'

const categories = [
  { key: 'openness', label: 'Space & movement', action: 'Improve space' },
  { key: 'storage', label: 'Storage & organization', action: 'Improve storage' },
  { key: 'workspace', label: 'Workspace', action: 'Improve workspace' },
  { key: 'aesthetics', label: 'Aesthetics & cohesion', action: 'Improve aesthetics' },
] as const

export default function RoomAssessmentCards({ assessment, baseline = null, layout = 'original', versionKey = 'original', designReview = false, onImprove, disabled = false }: {
  assessment: Assessment | null; baseline?: Assessment | null; layout?: RatingLayout; versionKey?: string; designReview?: boolean
  onImprove?: (focus: Focus) => void; disabled?: boolean
}) {
  return <section className="room-assessment" aria-labelledby="assessment-heading">
    <h2 id="assessment-heading">Your room at a glance</h2>
    <p>{layout === 'original' ? 'Estimates of your original room.' : layout === 'editing' ? 'Saved ratings · manual edits are not re-rated.' : 'Estimates of this improved layout.'}</p>
    {!assessment ? <p role="status">Room ratings are unavailable for this {layout === 'original' ? 'run' : 'layout'}. Your reconstructed room is still available below.</p> : <>
      <div className="assessment-grid">{categories.map(({ key, label, action }) => {
        const card = assessment[key]
        const recommended = assessment.recommended === key
        const score = card.score === null ? null : formatRating(card.score)
        const change = layout === 'improved' ? ratingChange(baseline?.[key].score, card.score) : null
        return <article key={key} className={`assessment-card${recommended ? ' recommended' : ''}`}>
          <div className="assessment-card-heading"><h3>{label}</h3>{recommended && <span className="assessment-recommendation">Recommended</span>}</div>
          <p className="assessment-rating" aria-label={`${label}: ${score === null ? 'not rated' : `${score} out of 10`}`}>
            {score === null ? <span className="assessment-unrated">{key === 'workspace' && card.status === 'not_applicable' ? 'No dedicated workspace' : 'Not assessed'}</span>
              : <><strong key={`${versionKey}:${score}`} className="assessment-rating-value">{score}</strong><span> / 10</span></>}
          </p>
          {change && change.delta !== 0 && <p className={`assessment-change${change.delta > 0 ? ' assessment-change--up' : ' assessment-change--down'}`} aria-label={`${label}: ${formatRating(change.before)} before, ${formatRating(change.after)} now, ${change.delta > 0 ? '+' : ''}${formatRating(change.delta)} points`}>
            <strong aria-hidden="true">{change.delta > 0 ? '+' : ''}{formatRating(change.delta)}</strong>
          </p>}
          {card.status === 'rated' && <span className="assessment-confidence">{card.confidence === 'low' ? 'Limited evidence' : key === 'aesthetics' && designReview ? 'Design review estimate' : card.source === 'photos' ? 'Photo-based estimate' : 'Model-based estimate'}</span>}
          <details className="assessment-explanation"><summary>Why this rating?</summary>
            {key === 'aesthetics' && layout !== 'original' && <p>{designReview ? 'Design review estimate.' : 'Aesthetics has not been re-rated for this layout.'}</p>}
            <p>{readableText(card.explanation)}</p>{key === 'aesthetics' && <AestheticBreakdown metrics={card.metrics ?? []} />}</details>
          <button type="button" disabled={disabled || !onImprove} onClick={() => onImprove?.(key)} aria-describedby="assessment-actions-note">{key === 'workspace' && card.status === 'not_applicable' ? 'Create a workspace' : action}</button>
        </article>
      })}</div>
      <p id="assessment-actions-note" className="input-help">{onImprove ? 'Choose an area to improve.' : 'Improvement actions are unavailable for this run. You can explore and edit your room below.'}</p>
      <details className="assessment-notes"><summary>About these ratings</summary><p>Recommendations are optional. Ratings follow the selected saved layout; changes compare with its starting layout using the same checks. Manual edits are not re-rated. New requests start from the visible layout, including submitted edits.</p><ul>{assessment.notes.map((note, i) => <li key={i}>{readableText(note)}</li>)}
        <li>Aesthetics uses a subjective five-part rubric for harmony, materials, balance, visual order and lighting.</li>
        <li>A low rating highlights a possible opportunity, not an instruction to change your room.</li>
      </ul></details>
    </>}
  </section>
}
