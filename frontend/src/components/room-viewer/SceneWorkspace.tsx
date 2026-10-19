import { lazy, Suspense, useCallback, useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import type { RoomScene } from '../../lib/scene.generated'
import { applyColorPreviews } from '../../lib/color-previews'
import { SceneEditor } from '../../lib/scene-editor'
import ObjectList from './ObjectList'
import ObjectInspector from './ObjectInspector'

const RoomViewer = lazy(() => import('./RoomViewer'))

export default function SceneWorkspace({ initialScene, beforeScene, onConnectionChange, variant = 'example' }: { initialScene: RoomScene; beforeScene: RoomScene; onConnectionChange: (online: boolean) => void; variant?: 'example' | 'reconstruction' }) {
  const editor = useMemo(() => new SceneEditor(initialScene, undefined, beforeScene), [initialScene, beforeScene])
  const state = useSyncExternalStore(editor.subscribe, editor.getSnapshot, editor.getSnapshot)
  const [detailsView, setDetailsView] = useState<'room' | 'objects'>('room')
  const [previewColors, setPreviewColors] = useState<Record<string, string>>({})
  const readOnly = state.view === 'before'
  const reconstructed = variant === 'reconstruction'
  const scene = useMemo(() => readOnly ? state.scene : applyColorPreviews(state.draft ?? state.scene, previewColors), [readOnly, state.draft, state.scene, previewColors])
  const selected = state.scene.objects.find(object => object.id === state.selectedId)
  const select = useCallback((id: string | null) => {
    editor.select(id)
    if (id !== null) setDetailsView('objects')
  }, [editor])

  useEffect(() => () => editor.cancel(false), [editor])
  useEffect(() => { setPreviewColors({}) }, [editor])
  useEffect(() => {
    if (state.notice?.connection !== undefined) onConnectionChange(state.notice.connection)
  }, [state.notice, onConnectionChange])
  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      const current = editor.getSnapshot()
      if (current.busy || current.dragging) editor.cancel()
      else editor.select(null)
    }
    window.addEventListener('keydown', escape)
    return () => window.removeEventListener('keydown', escape)
  }, [editor])

  return (
    <div className="workspace-grid">
      <aside className="room-details" aria-label="Room details">
        <div className="room-title">
          <span className="section-label">Your space</span>
          <h2>{reconstructed ? `Your ${scene.room.room_type.replaceAll('_', ' ')}` : 'Example bedroom'}</h2>
          <p>{reconstructed ? (readOnly ? 'Base scene · Approximate reconstruction, read-only.' : 'Your layout · Try your own arrangement.') : (readOnly ? 'Before · Original layout, read-only.' : 'After · Try your own arrangement.')}</p>
        </div>

        <div className="details-switch" role="group" aria-label="Details view">
          <button type="button" aria-pressed={detailsView === 'room'} onClick={() => setDetailsView('room')}>Room</button>
          <button type="button" aria-pressed={detailsView === 'objects'} onClick={() => setDetailsView('objects')}>Objects <span>{scene.objects.length}</span></button>
        </div>

        {detailsView === 'room' && (
          <>
            <dl className="room-measurements">
              <div><dt>Width</dt><dd>{scene.room.width_m.toFixed(1)} <span>m</span></dd></div>
              <div><dt>Length</dt><dd>{scene.room.length_m.toFixed(1)} <span>m</span></dd></div>
              <div><dt>Height</dt><dd>{scene.room.height_m.toFixed(1)} <span>m</span></dd></div>
              <div><dt>Floor area</dt><dd>{(scene.room.width_m * scene.room.length_m).toFixed(1)} <span>m²</span></dd></div>
            </dl>

            <section className="openings-summary">
              <h3>Doors & windows</h3>
              <ul>
                {scene.openings.map(opening => (
                  <li key={opening.id}>
                    <span className={`opening-swatch ${opening.type}`} aria-hidden="true" />
                    <div><strong>{opening.type === 'door' ? 'Door' : 'Window'}</strong><span>{opening.wall} wall · {opening.width_m.toFixed(1)} m wide</span></div>
                  </li>
                ))}
              </ul>
            </section>

            <div className="scene-note">
              <strong>Make it yours</strong>
              <p>{readOnly ? `Explore the original room. Switch to ${reconstructed ? 'Your layout' : 'After'} to move furniture and try colors.` : 'Open Objects to select furniture, edit its layout, and try colors. Changes stay here until you refresh.'}</p>
            </div>
            <p className="approximation-note">An approximate model. Opening positions are estimated; verify measurements in your room.</p>
          </>
        )}

        {detailsView === 'objects' && selected && <ObjectInspector scene={state.scene} object={selected} editor={editor} state={state} />}
        {detailsView === 'objects' && (
          <ObjectList
            scene={scene}
            hasColorPreviews={!readOnly && Object.keys(previewColors).length > 0}
            readOnly={readOnly}
            selectedId={state.selectedId}
            disabledSelection={state.busy || state.dragging}
            onSelect={select}
            onColorChange={(id, color) => { if (!readOnly) setPreviewColors(current => ({ ...current, [id]: color })) }}
            onResetColors={() => { if (!readOnly) setPreviewColors({}) }}
          />
        )}
      </aside>

      <section className="viewer-card" aria-label="3D room viewer">
        <div className="viewer-heading"><h2>Room overview</h2><span>{scene.objects.length} objects</span></div>
        <div className="comparison-bar">
          <div className="comparison-switch" role="group" aria-label="Layout comparison">
            <button type="button" aria-pressed={readOnly} onClick={() => editor.switchView('before')}>{reconstructed ? 'Base scene' : 'Before'} <span>Original</span></button>
            <button type="button" aria-pressed={!readOnly} onClick={() => editor.switchView('after')}>{reconstructed ? 'Your layout' : 'After'} <span>Editable</span></button>
          </div>
          <button className="reset-layout" type="button" disabled={readOnly} onClick={editor.resetLayout}>Reset layout</button>
          <p>{reconstructed ? 'Your photo-based base scene and your manual edits. Edits stay when you switch.' : 'Two authored example layouts. After edits stay when you switch.'}</p>
        </div>
        <div className="room-canvas">
          <Suspense fallback={<div className="viewer-message" role="status">Loading your room…</div>}>
            <RoomViewer scene={scene} editor={editor} state={state} onSelect={select} />
          </Suspense>
        </div>
        <div className="viewer-footer">
          <p>{readOnly ? `${reconstructed ? 'Base scene' : 'Before'} is read-only. Switch to ${reconstructed ? 'Your layout' : 'After'} to edit.` : 'Layout changes are checked before applying. Reset layout keeps your colors.'} Edits reset on refresh.</p>
          <span>Openings shown as markers</span>
        </div>
      </section>
    </div>
  )
}
