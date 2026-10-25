import { lazy, Suspense, useCallback, useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import type { RoomScene } from '../../lib/scene.generated'
import { applyColorPreviews } from '../../lib/color-previews'
import { SceneComparison } from '../../lib/scene-comparison'
import type { ComparisonState } from '../../lib/scene-comparison'
import { improvementLabels } from '../../lib/optimization'
import type { OptimizationRun } from '../../lib/optimization'
import ImprovementComparison from '../room-analysis/ImprovementComparison'
import LayoutAssessment from './LayoutAssessment'
import type { ScoreLayoutRequest } from '../../lib/layout-scoring'
import ObjectList from './ObjectList'
import { useSiteActions } from '../site/SiteActions'
import { Button, Icon } from '../ui/primitives'
import ObjectInspector from './ObjectInspector'
import GlassSelect from '../ui/GlassSelect'

const RoomViewer = lazy(() => import('./RoomViewer'))

export function ComparisonControls({ comparison, views }: { comparison: SceneComparison; views: ComparisonState }) {
  const saved = views.improvements.find(run => run.run_id === views.selectedImprovementId)
  const editing = views.improvements.find(run => run.run_id === views.editingId)
  const label = editing ? `${improvementLabels[editing.focus].toLowerCase()} · candidate ${views.improvements.indexOf(editing) + 1}` : 'original'
  return <div className="comparison-bar">
    <div className="comparison-switch" role="group" aria-label="Layout comparison">
      <button type="button" aria-pressed={views.view === 'original'} onClick={() => comparison.show('original')}>Original <span>Read-only</span></button>
      <button type="button" aria-pressed={views.view === 'improved'} disabled={!saved} onClick={() => comparison.show('improved')}>Improved <span>{saved ? 'Read-only candidate' : 'No candidate yet'}</span></button>
      <button type="button" aria-pressed={views.view === 'editing'} onClick={() => comparison.show('editing')}>Your layout <span>Editable copy</span></button>
    </div>
    {views.view !== 'editing' && <button className="edit-copy" type="button" onClick={comparison.editCopy}>Edit {views.view === 'improved' ? 'improved' : 'original'} copy</button>}
    <button className="reset-layout" type="button" disabled={views.view !== 'editing'} onClick={views.editor.resetLayout}>Reset layout</button>
    {views.improvements.length > 1 && <GlassSelect className="improvement-version" label="Improvement" value={views.selectedImprovementId ?? ''}
      onChange={comparison.selectImprovement} options={views.improvements.map((run, i) => ({ value: run.run_id,
        label: `${i + 1}. ${improvementLabels[run.focus]}${run.status !== 'completed' ? ' (run stopped early)' : ''}` }))} />}
    <p>{views.view !== 'editing' ? 'Compare the saved scenes, or edit a separate copy. Manual edits stay when you switch.' : `Editing a copy of ${label}. Reset layout restores that copy’s starting arrangement and keeps your color previews.`}</p>
  </div>
}

export default function SceneWorkspace({ initialScene, onConnectionChange, goals, improvement = null, objectLabels }: {
  goals?: ScoreLayoutRequest['goals']; initialScene: RoomScene; onConnectionChange: (online: boolean) => void
  improvement?: OptimizationRun | null; objectLabels?: Readonly<Record<string, string>>
}) {
  const comparison = useMemo(() => new SceneComparison(initialScene), [initialScene])
  const views = useSyncExternalStore(comparison.subscribe, comparison.getSnapshot, comparison.getSnapshot)
  const editor = views.editor
  const state = useSyncExternalStore(editor.subscribe, editor.getSnapshot, editor.getSnapshot)
  const siteActions = useSiteActions()
  const [detailsView, setDetailsView] = useState<'room' | 'objects'>('room')
  const previewColors = views.colors
  const readOnly = state.view === 'before'
  const selectedImprovement = views.improvements.find(run => run.run_id === views.selectedImprovementId)
  const editingImprovement = views.improvements.find(run => run.run_id === views.editingId)
  const editingLabel = editingImprovement ? `${improvementLabels[editingImprovement.focus]} · candidate ${views.improvements.indexOf(editingImprovement) + 1}` : 'Original'
  const scene = useMemo(() => readOnly ? state.scene : applyColorPreviews(state.draft ?? state.scene, previewColors), [readOnly, state.draft, state.scene, previewColors])
  const selected = state.scene.objects.find(object => object.id === state.selectedId)
  const select = useCallback((id: string | null) => {
    editor.select(id)
    if (id !== null) setDetailsView('objects')
  }, [editor])

  useEffect(() => () => editor.cancel(false), [editor])
  useEffect(() => () => comparison.cancelEdits(), [comparison])
  useEffect(() => { comparison.offer(improvement) }, [comparison, improvement])
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
          <h2>{`Your ${scene.room.room_type.replaceAll('_', ' ')}`}</h2>
          <p>{readOnly ? `${views.view === 'improved' ? 'Improved candidate' : 'Original reconstruction'} · Read-only.` : `Your layout · Copy of ${editingLabel.toLowerCase()}.`}</p>
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
              <p>{readOnly ? 'Explore this saved scene. Edit a copy to move furniture and try colors.' : 'Open Objects to select furniture, edit its layout, and try colors. Changes stay here until you refresh.'}</p>
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
            onColorChange={comparison.setColor}
            onResetColors={comparison.resetColors}
          />
        )}
      </aside>

      <section className="viewer-card" aria-label="3D room viewer">
        <div className="viewer-heading"><h2>Room overview</h2><div className="viewer-actions"><span>{scene.objects.length} objects</span>{siteActions && <><Button onClick={() => siteActions.account('save')}><Icon name="save"/>Save</Button><Button onClick={() => siteActions.share(scene)}><Icon name="share"/>Share</Button></>}</div></div>
        <ComparisonControls comparison={comparison} views={views} />
        {improvement && selectedImprovement && improvement.run_id !== selectedImprovement.run_id && <p className="comparison-history-note" role="status">
          {['prepared', 'running'].includes(improvement.status) ? 'A new improvement is in progress.' : 'This request produced no new retained candidate.'} The comparison still shows your earlier {improvementLabels[selectedImprovement.focus].toLowerCase()} candidate.
        </p>}
        <div className="room-canvas">
          <Suspense fallback={<div className="viewer-message" role="status">Loading your room…</div>}>
            <RoomViewer scene={scene} editor={editor} state={state} onSelect={select} />
          </Suspense>
        </div>
        {selectedImprovement && <ImprovementComparison run={selectedImprovement} labels={objectLabels} />}
        <LayoutAssessment key={JSON.stringify(state.scene)} scene={state.scene} goals={goals} />
        <div className="viewer-footer">
          <p>{readOnly ? 'Saved scenes are read-only. Edit a copy to make changes.' : 'Layout changes are checked before applying. Reset layout keeps your colors.'} Edits reset on refresh.</p>
          <span>Openings shown as markers</span>
        </div>
      </section>
    </div>
  )
}
