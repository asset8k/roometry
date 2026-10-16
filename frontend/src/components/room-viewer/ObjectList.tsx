import type { RoomScene } from '../../lib/scene.generated'
import { getModelKind } from './model-registry'

interface ObjectListProps {
  scene: RoomScene
  hasColorPreviews: boolean
  onColorChange: (id: string, color: string) => void
  onResetColors: () => void
}

export default function ObjectList({ scene, hasColorPreviews, onColorChange, onResetColors }: ObjectListProps) {
  return (
    <section className="object-colors" aria-label="Object color previews">
      <div className="objects-heading">
        <h3>Object colors</h3>
        <button type="button" disabled={!hasColorPreviews} onClick={onResetColors}>Reset colors</button>
      </div>
      <ul className="object-list">
        {scene.objects.map(object => {
          const label = object.type.replaceAll('_', ' ')
          return (
            <li key={object.id}>
              <div className="object-info">
                <strong>{label}</strong>
                <span>
                  {object.placement}
                  {object.locked ? ' · Locked' : object.fixed ? ' · Fixed' : ''}
                  {getModelKind(object.asset_id) === 'placeholder' ? ' · Placeholder' : ''}
                </span>
              </div>
              <input
                type="color"
                aria-label={`Color for ${label}`}
                title={object.locked ? 'This object is locked' : `Preview ${label} color`}
                value={object.color}
                disabled={object.locked}
                onChange={event => onColorChange(object.id, event.target.value)}
              />
            </li>
          )
        })}
      </ul>
      <p className="color-preview-note">Color changes are local previews and aren't saved. Fixed objects can be recolored; locked objects stay protected. Orbit the room to see items on the opposite wall.</p>
    </section>
  )
}
