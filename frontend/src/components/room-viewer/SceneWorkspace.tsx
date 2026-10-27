import { lazy, Suspense, useCallback, useEffect, useId, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import type { ReactNode } from 'react'
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
import HelpTip from '../ui/HelpTip'
import Dialog from '../ui/Dialog'
import MotionRegion, { useMediaQuery } from '../ui/MotionRegion'

const RoomViewer = lazy(() => import('./RoomViewer'))

export function ComparisonControls({ comparison, views, onEdit, actions }: { comparison: SceneComparison; views: ComparisonState; onEdit?: () => void; actions?: ReactNode }) {
  const saved = views.improvements.find(run => run.run_id === views.selectedImprovementId)
  return <div className="comparison-bar">
    <div className="comparison-switch" role="group" aria-label="Layout comparison">
      <button type="button" aria-pressed={views.view === 'original'} onClick={() => comparison.show('original')}>Original</button>
      {saved && <button type="button" aria-pressed={views.view === 'improved'} onClick={() => comparison.show('improved')}>Improved</button>}
      {views.hasEditableCopy && <button type="button" aria-pressed={views.view === 'editing'} onClick={() => comparison.show('editing')}>Your layout</button>}
    </div>
    {views.view !== 'editing' ? <Button onClick={onEdit ?? comparison.editCopy}>Edit layout</Button>
      : <Button variant="quiet" onClick={views.editor.resetLayout}>Reset layout</Button>}
    {views.improvements.length > 1 && <GlassSelect className="improvement-version" label="Version" value={views.selectedImprovementId ?? ''}
      onChange={comparison.selectImprovement} options={views.improvements.map((run, i) => ({ value: run.run_id,
        label: `${i + 1}. ${run.instruction ? 'Chat refinement' : improvementLabels[run.focus]}${run.status !== 'completed' ? ' (partial)' : ''}` }))} />}
    {actions && <div className="viewer-actions">{actions}</div>}
    <HelpTip label="About layouts">Original and Improved stay unchanged. Edits live in Your layout. Save to keep your work.</HelpTip>
  </div>
}

export default function SceneWorkspace({ initialScene, onConnectionChange, goals, improvement = null, objectLabels, comparison: suppliedComparison, overview, chat, saveAction, sourceRunId }: {
  goals?: ScoreLayoutRequest['goals']; initialScene: RoomScene; onConnectionChange: (online: boolean) => void
  improvement?: OptimizationRun | null; objectLabels?: Readonly<Record<string, string>>
  comparison?: SceneComparison; overview?: ReactNode; chat?: ReactNode; saveAction?: ReactNode; sourceRunId?: string
}) {
  const comparison = useMemo(() => suppliedComparison ?? new SceneComparison(initialScene), [initialScene, suppliedComparison])
  const views = useSyncExternalStore(comparison.subscribe, comparison.getSnapshot, comparison.getSnapshot)
  const editor = views.editor
  const state = useSyncExternalStore(editor.subscribe, editor.getSnapshot, editor.getSnapshot)
  const siteActions = useSiteActions()
  const [detailsView, setDetailsView] = useState<'room' | 'objects' | 'chat'>('room')
  const [mobilePanelOpen, setMobilePanelOpen] = useState(false)
  const mobile = useMediaQuery('(max-width: 800px)')
  const [editPromptOpen, setEditPromptOpen] = useState(false)
  const startEditing = () => {
    const selectedId = views.editor.getSnapshot().selectedId
    comparison.editCopy()
    comparison.getSnapshot().editor.select(selectedId)
    setEditPromptOpen(false)
  }
  const requestEdit = () => views.hasEditableCopy ? startEditing() : setEditPromptOpen(true)
  const panelId = useId(), mobileToggle = useRef<HTMLButtonElement>(null)
  const previewColors = views.colors
  const readOnly = state.view === 'before'
  const selectedImprovement = views.improvements.find(run => run.run_id === views.selectedImprovementId)
  const scene = useMemo(() => readOnly ? state.scene : applyColorPreviews(state.draft ?? state.scene, previewColors), [readOnly, state.draft, state.scene, previewColors])
  const selected = state.scene.objects.find(object => object.id === state.selectedId)
  const select = useCallback((id: string | null) => {
    editor.select(id)
    if (id !== null) { setDetailsView('objects'); setMobilePanelOpen(true) }
  }, [editor])

  useEffect(() => () => editor.cancel(false), [editor])
  useEffect(() => () => comparison.cancelEdits(), [comparison])
  useEffect(() => { if (!suppliedComparison) comparison.offer(improvement) }, [comparison, improvement, suppliedComparison])
  useEffect(() => {
    if (state.notice?.connection !== undefined) onConnectionChange(state.notice.connection)
  }, [state.notice, onConnectionChange])
  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented || document.querySelector('dialog[open]')) return
      if (mobilePanelOpen && window.matchMedia('(max-width: 800px)').matches) {
        setMobilePanelOpen(false); mobileToggle.current?.focus(); return
      }
      const current = editor.getSnapshot()
      if (current.busy || current.dragging) editor.cancel()
      else editor.select(null)
    }
    window.addEventListener('keydown', escape)
    return () => window.removeEventListener('keydown', escape)
  }, [editor, mobilePanelOpen])

  return (
    <div className={`workspace-grid${overview || chat ? ' unified-workspace' : ''}`}>
      <aside id={panelId} className="room-details" data-mobile-open={mobilePanelOpen} inert={mobile && !mobilePanelOpen} aria-hidden={mobile ? !mobilePanelOpen : undefined} aria-label="Room details">
        <div className="workspace-side-header">
        <div className="room-title">
          <h2>{`Your ${scene.room.room_type.replaceAll('_', ' ')}`}</h2>

        </div>

        <div className="details-switch" role="group" aria-label="Details view">
          <button type="button" aria-pressed={detailsView === 'room'} onClick={() => setDetailsView('room')}>{overview ? 'Overview' : 'Room'}</button>
          <button type="button" aria-pressed={detailsView === 'objects'} onClick={() => setDetailsView('objects')}>Objects <span>{scene.objects.length}</span></button>
          {chat && <button type="button" aria-pressed={detailsView === 'chat'} onClick={() => setDetailsView('chat')}>Chat</button>}
        </div>
        </div>

        <MotionRegion className="workspace-side-body" motionKey={`${detailsView}:${detailsView === 'objects' ? state.selectedId : ''}`}>
        {detailsView === 'room' && (
          <>
            {overview}
            <details className="workspace-room-details" open={!overview}><summary>Room details</summary>
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

            <p className="approximation-note">Approximate model · Check measurements before making changes in your room.</p>
            </details>
          </>
        )}

        {detailsView === 'chat' && chat}

        {detailsView === 'objects' && selected && <ObjectInspector scene={state.scene} object={selected} editor={editor} state={state} onToggleLock={() => comparison.toggleLock(selected.id)} onRequestEdit={requestEdit} />}
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
        </MotionRegion>
      </aside>
      {(overview || chat) && <button ref={mobileToggle} type="button" className="mobile-tools-toggle ui-button ui-button--primary" aria-expanded={mobilePanelOpen} aria-controls={panelId}
        onClick={() => setMobilePanelOpen(open => !open)}><Icon name={mobilePanelOpen ? 'close' : 'room'}/>{mobilePanelOpen ? 'Close tools' : 'Room tools'}</button>}

      <section className="viewer-card" aria-label="3D room viewer">
        <h2 className="visually-hidden">Room overview</h2>
        <ComparisonControls comparison={comparison} views={views} onEdit={requestEdit} actions={siteActions && <>{saveAction ?? <Button onClick={() => siteActions.account('save')}><Icon name="save"/>Save</Button>}<Button onClick={() => siteActions.share(scene, sourceRunId)}><Icon name="share"/>Share</Button></>} />
        {improvement && selectedImprovement && improvement.run_id !== selectedImprovement.run_id && <p className="comparison-history-note" role="status">
          {['prepared', 'running'].includes(improvement.status) ? 'A new improvement is in progress.' : 'This request produced no new retained candidate.'} The comparison still shows your earlier {selectedImprovement.instruction ? 'chat refinement' : improvementLabels[selectedImprovement.focus].toLowerCase()} candidate.
        </p>}
        <div className="room-canvas">
          <Suspense fallback={<div className="viewer-message" role="status">Loading your room…</div>}>
            <RoomViewer scene={scene} editor={editor} state={state} onSelect={select} />
          </Suspense>
        </div>
        {selectedImprovement && <details className="candidate-details"><summary>Candidate details</summary><ImprovementComparison run={selectedImprovement} labels={objectLabels} /></details>}
        <LayoutAssessment key={JSON.stringify(state.scene)} scene={state.scene} goals={goals} />

      </section>
      {editPromptOpen && <Dialog title="Create your own layout?" onClose={() => setEditPromptOpen(false)}>{dismiss => <>
        <p>Your edits will create a separate layout. Original and Improved stay unchanged.</p>
        <div className="dialog-actions"><Button variant="quiet" data-initial-focus onClick={() => dismiss()}>Keep viewing</Button>
          <Button variant="primary" onClick={() => dismiss(startEditing)}>Create layout<Icon name="arrow" /></Button></div>
      </>}</Dialog>}
    </div>
  )
}
