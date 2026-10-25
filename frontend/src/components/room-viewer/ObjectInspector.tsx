import { useEffect, useState } from 'react'
import type { RoomScene, SceneObject } from '../../lib/scene.generated'
import { editRestriction, objectLabel } from '../../lib/scene-edits'
import type { SceneEditor, EditorState } from '../../lib/scene-editor'

export default function ObjectInspector({ scene, object, editor, state, onToggleLock }: {
  scene: RoomScene; object: SceneObject; editor: SceneEditor; state: EditorState
  onToggleLock?: () => void
}) {
  const [x, setX] = useState(String(object.position.x))
  const [z, setZ] = useState(String(object.position.z))
  const [angle, setAngle] = useState(String(object.rotation_y_rad * 180 / Math.PI))
  const restriction = state.view === 'before' ? 'This layout is read-only. Switch to the editable layout to make changes.' : editRestriction(scene, object)
  const disabled = Boolean(restriction) || state.busy || state.dragging

  useEffect(() => {
    if (state.busy) return
    setX(String(Number(object.position.x.toFixed(6))))
    setZ(String(Number(object.position.z.toFixed(6))))
    setAngle(String(Number((object.rotation_y_rad * 180 / Math.PI).toFixed(3))))
  }, [object.id, object.position.x, object.position.z, object.rotation_y_rad, state.busy])

  return (
    <section className="object-inspector" id="object-inspector" tabIndex={-1} aria-label="Selected object">
      <div className="inspector-heading"><h3>{objectLabel(object)}</h3><button type="button" disabled={state.busy || state.dragging} onClick={() => editor.select(null)}>Clear</button></div>
      <button type="button" className="object-lock" aria-pressed={object.locked} disabled={!editor.canToggleLock(object.id)} onClick={onToggleLock ?? (() => editor.toggleLock(object.id))}>{object.locked ? 'Unlock object' : 'Lock object'}</button>
      {restriction ? <p className="edit-restriction">{restriction}</p> : (
        <p className="inspector-help">Drag the handles or enter values. Supporting furniture carries its objects along.</p>
      )}
      <div className="edit-mode" role="group" aria-label="Object handle mode">
        <button type="button" disabled={disabled} aria-pressed={state.mode === 'move'} onClick={() => editor.setMode('move')}>Move</button>
        <button type="button" disabled={disabled} aria-pressed={state.mode === 'rotate'} onClick={() => editor.setMode('rotate')}>Rotate</button>
      </div>
      <form onSubmit={event => {
        event.preventDefault()
        void editor.submit({ operation: 'move', object_id: object.id, position: { x: Number(x), z: Number(z) } })
      }}>
        <div className="position-fields">
          <label>X (m)<input aria-label="Object X position" type="number" required step="any" min={-100} max={100} value={x} disabled={disabled} onChange={event => setX(event.target.value)} /></label>
          <label>Z (m)<input aria-label="Object Z position" type="number" required step="any" min={-100} max={100} value={z} disabled={disabled} onChange={event => setZ(event.target.value)} /></label>
        </div>
        <button className="apply-edit" type="submit" disabled={disabled}>Apply position</button>
      </form>
      <form onSubmit={event => {
        event.preventDefault()
        void editor.submit({ operation: 'rotate', object_id: object.id, rotation_y_rad: Number(angle) * (Math.PI / 180) })
      }}>
        <label>Angle (°)<input aria-label="Object angle in degrees" type="number" required step="any" value={angle} disabled={disabled} onChange={event => setAngle(event.target.value)} /></label>
        <button className="apply-edit" type="submit" disabled={disabled}>Apply rotation</button>
      </form>
    </section>
  )
}
