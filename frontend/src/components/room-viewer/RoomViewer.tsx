import { Component, useCallback, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Canvas, useThree } from '@react-three/fiber'
import type { RoomScene } from '../../lib/scene.generated'
import RoomShell from './RoomShell'
import SceneObjects from './SceneObjects'
import CameraControls from './CameraControls'
import type { ViewCommand } from './CameraControls'
import { WallModeContext } from './view-settings'
import type { WallMode } from './view-settings'

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

export default function RoomViewer({ scene }: { scene: RoomScene }) {
  const [command, setCommand] = useState<ViewCommand>({ mode: 'perspective', revision: 0 })
  const [zoom, setZoom] = useState({ factor: 1, revision: 0 })
  const [wallMode, setWallMode] = useState<WallMode>('cutaway')
  const [attempt, setAttempt] = useState(0)
  const [contextLost, setContextLost] = useState(false)
  const onLost = useCallback(() => setContextLost(true), [])
  const retry = () => {
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
          <button type="button" aria-pressed={command.mode === 'perspective'} onClick={() => setView('perspective')}>Perspective</button>
          <button type="button" aria-pressed={command.mode === 'top'} onClick={() => setView('top')}>Top-down</button>
        </div>
        <div className="view-actions">
          <button type="button" aria-label="Zoom in" onClick={() => zoomBy(0.8)}>+</button>
          <button type="button" aria-label="Zoom out" onClick={() => zoomBy(1.25)}>−</button>
          <button type="button" onClick={() => setView(command.mode)}>Reset view</button>
        </div>
        <label className="wall-control">Walls
          <select value={wallMode} onChange={event => setWallMode(event.target.value as WallMode)}>
            <option value="cutaway">Cutaway</option>
            <option value="transparent">Transparent</option>
            <option value="hidden">Hidden</option>
          </select>
        </label>
      </div>
      <p className="view-hint" role="status">
        {command.mode === 'top' ? 'North ↑ · Overhead perspective · Drag with two fingers to pan.' : 'Drag to orbit · Pinch or scroll to zoom · Right-drag to pan.'}
        {wallMode !== 'cutaway' && ' Wall-mounted objects and opening markers stay visible.'}
      </p>
      <div className="renderer-canvas" aria-label={`${command.mode === 'top' ? 'Top-down' : 'Perspective'} 3D room`}>
        <ViewerErrorBoundary key={attempt} onRetry={retry}>
          {contextLost ? <ViewerUnavailable onRetry={retry} /> : (
            <Canvas
              frameloop="demand"
              dpr={[1, 1.75]}
              camera={{ fov: 42, near: 0.01, far: 2000 }}
              fallback={<span aria-hidden="true">A WebGL browser is required to display the room.</span>}
            >
              <ContextMonitor onLost={onLost} />
              <color attach="background" args={['#eeece4']} />
              <ambientLight intensity={1.5} />
              <directionalLight position={[scene.room.width_m + 2, 8, scene.room.length_m + 2]} intensity={2} />
              <WallModeContext.Provider value={wallMode}>
                <RoomShell scene={scene} />
                <SceneObjects scene={scene} />
              </WallModeContext.Provider>
              <CameraControls room={scene.room} command={command} zoom={zoom} />
            </Canvas>
          )}
        </ViewerErrorBoundary>
      </div>
    </div>
  )
}
