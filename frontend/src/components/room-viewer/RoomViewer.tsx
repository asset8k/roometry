import { Component, useCallback, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Canvas, useThree } from '@react-three/fiber'
import type { RoomScene } from '../../lib/scene.generated'
import RoomShell from './RoomShell'
import SceneObjects from './SceneObjects'
import CameraControls from './CameraControls'
import type { ViewCommand } from './CameraControls'
import { WallModeContext } from './view-settings'
import type { WallMode } from './view-settings'
import { editRestriction, objectLabel } from '../../lib/scene-edits'
import type { EditorState, SceneEditor } from '../../lib/scene-editor'
import TransformGizmo from './TransformGizmo'
import GlassSelect from '../ui/GlassSelect'

function ViewerUnavailable({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="viewer-message" role="alert">
      <h3>3D view unavailable</h3>
      <p>Try again or use a browser with WebGL enabled. Your room details and color previews are still available.</p>
      <button type="button" onClick={onRetry}>Retry 3D view</button>
    </div>
  )
}

class ViewerErrorBoundary extends Component<{ children: ReactNode; onRetry: () => void }, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  render() {
    return this.state.failed ? <ViewerUnavailable onRetry={this.props.onRetry} /> : this.props.children
  }
}

function ContextMonitor({ onLost }: { onLost: () => void }) {
  const canvas = useThree(state => state.gl.domElement)
  useEffect(() => {
    const lost = (event: Event) => {
      event.preventDefault()
      onLost()
    }
    canvas.addEventListener('webglcontextlost', lost)
    return () => canvas.removeEventListener('webglcontextlost', lost)
  }, [canvas, onLost])
  return null
}

export default function RoomViewer({ scene, editor, state, onSelect }: {
  scene: RoomScene; editor: SceneEditor; state: EditorState; onSelect: (id: string | null) => void
}) {
  const [command, setCommand] = useState<ViewCommand>({ mode: 'perspective', revision: 0 })
  const [zoom, setZoom] = useState({ factor: 1, revision: 0 })
  const [wallMode, setWallMode] = useState<WallMode>('cutaway')
  const [attempt, setAttempt] = useState(0)
  const [contextLost, setContextLost] = useState(false)
  const suppressMissUntil = useRef(0)
  const selected = scene.objects.find(object => object.id === state.selectedId)
  const editable = state.view === 'after' && selected && !editRestriction(state.scene, selected)
  const onInteraction = () => { suppressMissUntil.current = performance.now() + 250 }
  const onLost = useCallback(() => { editor.cancel(false); setContextLost(true) }, [editor])
  const retry = () => {
    editor.cancel(false)
    setContextLost(false)
    setAttempt(value => value + 1)
    setZoom({ factor: 1, revision: 0 })
  }
  const setView = (mode: ViewCommand['mode']) => setCommand(current => ({ mode, revision: current.revision + 1 }))
  const zoomBy = (factor: number) => setZoom(current => ({ factor, revision: current.revision + 1 }))

  return (
    <div className="room-renderer">
      <div className="viewer-toolbar" aria-label="View controls" role="group">
        <div className="view-presets" role="group" aria-label="Camera view">
          <button type="button" disabled={state.dragging} aria-pressed={command.mode === 'perspective'} onClick={() => setView('perspective')}>Perspective</button>
          <button type="button" disabled={state.dragging} aria-pressed={command.mode === 'top'} onClick={() => setView('top')}>Top-down</button>
        </div>
        <div className="view-actions">
          <button type="button" disabled={state.dragging} aria-label="Zoom in" onClick={() => zoomBy(0.8)}>+</button>
          <button type="button" disabled={state.dragging} aria-label="Zoom out" onClick={() => zoomBy(1.25)}>−</button>
          <button type="button" disabled={state.dragging} onClick={() => setView(command.mode)}>Reset view</button>
        </div>
        <GlassSelect className="wall-control" label="Walls" disabled={state.dragging} value={wallMode}
          options={[{ value: 'cutaway', label: 'Cutaway' }, { value: 'transparent', label: 'Transparent' }, { value: 'hidden', label: 'Hidden' }]}
          onChange={value => setWallMode(value as WallMode)} />
      </div>
      <p className="view-hint" role="status">
        {command.mode === 'top' ? 'North ↑ · Overhead perspective · Drag with two fingers to pan.' : 'Drag to orbit · Pinch or scroll to zoom · Right-drag to pan.'}
        {wallMode !== 'cutaway' && ' Wall-mounted objects and opening markers stay visible.'}
      </p>
      <div className="selection-strip">
        {selected ? <>
          <strong>{objectLabel(selected)}</strong>
          <span>{editable ? `${state.mode === 'move' ? 'Move handles · 5 cm steps' : 'Rotate ring · 15° steps'}` : state.view === 'before' ? 'Inspecting · layout is read-only' : 'Inspecting · position protected'}</span>
          <a href="#object-inspector" onClick={() => onSelect(selected.id)}>{state.view === 'before' ? 'Inspect values' : 'Edit values'}</a>
        </> : <span>Select an object in the room or the Objects list.</span>}
        {(state.busy || state.dragging) && <button type="button" onClick={() => editor.cancel()}>Cancel edit</button>}
      </div>
      {state.notice && <div className={`editor-notice ${state.notice.kind}`} role={state.notice.kind === 'error' || state.notice.kind === 'rejected' ? 'alert' : 'status'}>
        <p>{state.notice.text}</p>
        {state.notice.details && <ul>{state.notice.details.map((detail, index) => <li key={index}>{detail}</li>)}</ul>}
      </div>}
      <div className="renderer-canvas" aria-label={`${command.mode === 'top' ? 'Top-down' : 'Perspective'} 3D room`}>
        <ViewerErrorBoundary key={attempt} onRetry={retry}>
          {contextLost ? <ViewerUnavailable onRetry={retry} /> : (
            <Canvas
              frameloop="demand"
              dpr={[1, 1.75]}
              camera={{ fov: 42, near: 0.01, far: 2000 }}
              fallback={<span aria-hidden="true">A WebGL browser is required to display the room.</span>}
              onPointerMissed={() => {
                const current = editor.getSnapshot()
                if (!current.dragging && !current.busy && performance.now() > suppressMissUntil.current) onSelect(null)
              }}
            >
              <ContextMonitor onLost={onLost} />
              <ambientLight intensity={1.5} />
              <directionalLight position={[scene.room.width_m + 2, 8, scene.room.length_m + 2]} intensity={2} />
              <WallModeContext.Provider value={wallMode}>
                <RoomShell scene={scene} />
                <SceneObjects scene={scene} selectedId={state.selectedId} onSelect={onSelect} />
              </WallModeContext.Provider>
              <CameraControls room={scene.room} command={command} zoom={zoom} enabled={!state.dragging} />
              {selected && editable && <TransformGizmo key={selected.id} object={selected} editor={editor} state={state} onInteraction={onInteraction} />}
            </Canvas>
          )}
        </ViewerErrorBoundary>
      </div>
    </div>
  )
}
