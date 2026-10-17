import { lazy, Suspense, useCallback, useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import type { RoomScene } from '../../lib/scene.generated'
import { applyColorPreviews } from '../../lib/color-previews'
import { SceneEditor } from '../../lib/scene-editor'
import ObjectList from './ObjectList'
import ObjectInspector from './ObjectInspector'

const RoomViewer = lazy(() => import('./RoomViewer'))

export default function SceneWorkspace({ initialScene, onConnectionChange }: { initialScene: RoomScene; onConnectionChange: (online: boolean) => void }) {
  const editor = useMemo(() => new SceneEditor(initialScene), [initialScene])
  const state = useSyncExternalStore(editor.subscribe, editor.getSnapshot)
  const [detailsView, setDetailsView] = useState<'room' | 'objects'>('room')
  const [previewColors, setPreviewColors] = useState<Record<string, string>>({})
  const scene = useMemo(() => applyColorPreviews(state.draft ?? state.scene, previewColors), [state.draft, state.scene, previewColors])
  const selected = state.scene.objects.find(object => object.id === state.selectedId)
  const select = useCallback((id: string | null) => {
    editor.select(id)
    if (id !== null) setDetailsView('objects')
  }, [editor])

  useEffect(() => () => editor.cancel(false), [editor])
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
          <h2>Example bedroom</h2>
          <p>A simple room to explore from every angle.</p>
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
              <p>Open Objects to select furniture, edit its layout, and try colors. Changes stay here until you refresh.</p>
            </div>
            <p className="approximation-note">An approximate model. Opening positions are estimated; verify measurements in your room.</p>
          </>
        )}

        {detailsView === 'objects' && selected && <ObjectInspector scene={state.scene} object={selected} editor={editor} state={state} />}
        {detailsView === 'objects' && (
          <ObjectList
            scene={scene}
            hasColorPreviews={Object.keys(previewColors).length > 0}
            selectedId={state.selectedId}
            disabledSelection={state.busy || state.dragging}
            onSelect={select}
            onColorChange={(id, color) => setPreviewColors(current => ({ ...current, [id]: color }))}
            onResetColors={() => setPreviewColors({})}
          />
        )}
      </aside>

      <section className="viewer-card" aria-label="3D room viewer">
        <div className="viewer-heading"><h2>Room overview</h2><span>{scene.objects.length} objects</span></div>
        <div className="room-canvas">
          <Suspense fallback={<div className="viewer-message" role="status">Loading your room…</div>}>
            <RoomViewer scene={scene} editor={editor} state={state} onSelect={select} />
          </Suspense>
        </div>
        <div className="viewer-footer">
          <p>Layout changes are checked before applying. Edits reset on refresh.</p>
          <span>Openings shown as markers</span>
        </div>
      </section>
    </div>
  )
}
