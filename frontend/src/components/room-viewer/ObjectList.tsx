import type { RoomScene } from '../../lib/scene.generated'
import { getModelKind } from './model-registry'

interface ObjectListProps {
  scene: RoomScene
  hasColorPreviews: boolean
  onColorChange: (id: string, color: string) => void
  onResetColors: () => void
  selectedId: string | null
  disabledSelection: boolean
  readOnly: boolean
  onSelect: (id: string) => void
}

export default function ObjectList({ scene, hasColorPreviews, onColorChange, onResetColors, selectedId, disabledSelection, readOnly, onSelect }: ObjectListProps) {
  return (
    <section className="object-colors" aria-label="Object color previews">
      <div className="objects-heading">
        <h3>Objects & colors</h3>
        <button type="button" disabled={readOnly || !hasColorPreviews} onClick={onResetColors}>Reset colors</button>
      </div>
      <ul className="object-list">
        {scene.objects.map(object => {
          const label = object.type.replaceAll('_', ' ')
          return (
            <li key={object.id}>
              <button className="object-info" type="button" aria-label={`Select ${label}`} aria-pressed={selectedId === object.id} disabled={disabledSelection} onClick={() => onSelect(object.id)}>
                <strong>{label}</strong>
                <span>
                  {object.placement}
                  {object.locked ? ' · Locked' : object.fixed ? ' · Fixed' : ''}
                  {getModelKind(object.asset_id) === 'placeholder' ? ' · Placeholder' : ''}
                </span>
              </button>
              <input
                type="color"
                aria-label={`Color for ${label}`}
                title={readOnly ? 'This layout is read-only' : object.locked ? 'This object is locked' : `Preview ${label} color`}
                value={object.color}
                disabled={readOnly || object.locked}
                onChange={event => onColorChange(object.id, event.target.value)}
              />
            </li>
          )
        })}
      </ul>
      <p className="color-preview-note">{readOnly ? 'Original colors are read-only. Switch to the editable layout to try colors; your previews are kept.' : "Color changes are local previews and aren't saved. Fixed objects can be recolored; locked objects stay protected. Orbit the room to see items on the opposite wall."}</p>
    </section>
  )
}
