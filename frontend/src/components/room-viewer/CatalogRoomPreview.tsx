import { useMemo, useState } from 'react'
import { Canvas } from '@react-three/fiber'
import kitchen from '../../data/kitchen_catalog_scene.json'
import bathroom from '../../data/bathroom_catalog_scene.json'
import living from '../../data/decor_catalog_scene.json'
import { parseRoomScene } from '../../lib/scene'
import CameraControls from './CameraControls'
import type { ViewCommand } from './CameraControls'
import RoomShell from './RoomShell'
import SceneObjects from './SceneObjects'

export default function CatalogRoomPreview() {
  const [room, setRoom] = useState<'kitchen' | 'bathroom' | 'living'>('kitchen')
  const [command, setCommand] = useState<ViewCommand>({ mode: 'perspective', revision: 0 })
  const scene = useMemo(() => parseRoomScene({ kitchen, bathroom, living }[room]), [room])
  const title = { kitchen: 'Kitchen collection', bathroom: 'Bathroom & utility collection', living: 'The finishing touches' }[room]
  const extent = Math.max(scene.room.width_m, scene.room.length_m) * 1.5
  const choose = (value: typeof room) => {
    setRoom(value)
    setCommand(current => ({ mode: 'perspective', revision: current.revision + 1 }))
  }
  return <div className="catalog-room-preview">
    <div className="catalog-room-heading">
      <div><p className="eyebrow">See them together</p><h2>{title}</h2></div>
      <div role="group" aria-label="Catalog room example">
        <button type="button" aria-pressed={room === 'kitchen'} onClick={() => choose('kitchen')}>Kitchen example</button>
        <button type="button" aria-pressed={room === 'bathroom'} onClick={() => choose('bathroom')}>Bathroom example</button>
        <button type="button" aria-pressed={room === 'living'} onClick={() => choose('living')}>Living room example</button>
      </div>
    </div>
    <p className="furniture-preview-note">Authored examples for inspecting models together. Your uploaded room is separate.</p>
    <div className="furniture-preview-toolbar">
      <div role="group" aria-label="Example camera">
        {(['perspective', 'top'] as const).map(mode => <button key={mode} type="button" aria-pressed={command.mode === mode}
          onClick={() => setCommand(current => ({ mode, revision: current.revision + 1 }))}>
          {mode === 'perspective' ? 'Perspective' : 'Top'}</button>)}
      </div>
      <span className="furniture-preview-note">{scene.objects.length} objects · Drag to orbit · Scroll to zoom</span>
    </div>
    <div className="catalog-room-canvas" aria-label={`3D ${room} catalog example`}>
      <Canvas shadows frameloop="demand" dpr={[1, 1.75]} camera={{ fov: 42, near: .01, far: 100 }}
        fallback={<div className="viewer-message">WebGL is required for the room example.</div>}>
        <color attach="background" args={['#EEEDE6']} />
        <hemisphereLight args={['#FFFFFF', '#B8AB94', 2]} />
        <directionalLight position={[scene.room.width_m + 2, 8, scene.room.length_m + 2]} intensity={2.4} castShadow
          shadow-mapSize={[1024, 1024]} shadow-camera-left={-extent} shadow-camera-right={extent}
          shadow-camera-top={extent} shadow-camera-bottom={-extent} shadow-normalBias={.025} />
        <RoomShell scene={scene} />
        <SceneObjects scene={scene} />
        <CameraControls room={scene.room} command={command} zoom={{ revision: 0, factor: 1 }} />
      </Canvas>
    </div>
  </div>
}
