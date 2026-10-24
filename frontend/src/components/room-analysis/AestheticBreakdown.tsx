export const aestheticCriteria = [
  ['color_harmony', 'Color harmony'], ['material_cohesion', 'Material cohesion'],
  ['visual_balance', 'Visual balance'], ['visual_order', 'Visual order'],
  ['lighting_composition', 'Lighting composition'],
] as const

export function AestheticBreakdown({ metrics }: { metrics: readonly { key: string; value: number }[] }) {
  const values = new Map(metrics.map(metric => [metric.key, metric.value]))
  if (!aestheticCriteria.every(([key]) => Number.isInteger(values.get(key)) && values.get(key)! >= 0 && values.get(key)! <= 2)) return null
  return <details className="assessment-breakdown"><summary>How aesthetics was rated</summary>
    <p>Each aspect: 0 conflicting, 1 mixed, 2 cohesive. This is a subjective estimate of the visible room.</p>
    <ul>{aestheticCriteria.map(([key, label]) => <li key={key}>{label}: {values.get(key)} / 2</li>)}</ul>
  </details>
}
