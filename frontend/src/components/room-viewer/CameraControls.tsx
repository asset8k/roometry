import { useLayoutEffect, useRef } from 'react'
import type { ComponentRef } from 'react'
import { useThree } from '@react-three/fiber'
import { OrbitControls } from '@react-three/drei'
import { PerspectiveCamera } from 'three'
import type { RoomDimensions } from '../../lib/scene.generated'
import { frameRoom } from './camera'
import type { ViewMode } from './camera'

export interface ViewCommand {
  mode: ViewMode
  revision: number
}

export default function CameraControls({ room, command, zoom }: {
  room: RoomDimensions
  command: ViewCommand
  zoom: { revision: number; factor: number }
}) {
  const controls = useRef<ComponentRef<typeof OrbitControls>>(null)
  const appliedZoom = useRef(0)
  const camera = useThree(state => state.camera)
  const size = useThree(state => state.size)
  const invalidate = useThree(state => state.invalidate)
  const span = Math.max(room.width_m, room.length_m, room.height_m)
  const minDistance = command.mode === 'top' ? room.height_m + 0.1 : span * 0.35
  // Allow narrow screens and very tall rooms to fit without clamping the reset.
  const maxDistance = span * 12 / Math.min(1, size.width / Math.max(1, size.height))

  useLayoutEffect(() => {
    if (!(camera instanceof PerspectiveCamera) || !controls.current) return
    const target = frameRoom(camera, room, command.mode)
    controls.current.target.copy(target)
    controls.current.update()
    invalidate()
  }, [camera, room.width_m, room.length_m, room.height_m, command.mode, command.revision, size.width, size.height, invalidate])

  useLayoutEffect(() => {
    if (!controls.current || zoom.revision === 0 || zoom.revision === appliedZoom.current) return
    appliedZoom.current = zoom.revision
    const offset = camera.position.clone().sub(controls.current.target)
    const distance = Math.min(maxDistance, Math.max(minDistance, offset.length() * zoom.factor))
    camera.position.copy(controls.current.target).add(offset.setLength(distance))
    controls.current.update()
    invalidate()
    // A zoom command runs once. View/reset/resize changes are handled above.
  }, [zoom.revision, zoom.factor, minDistance, maxDistance, camera, invalidate])

  return (
    <OrbitControls
      ref={controls}
      makeDefault
      minDistance={minDistance}
      maxDistance={maxDistance}
      minPolarAngle={command.mode === 'top' ? 0 : 0.1}
      maxPolarAngle={command.mode === 'top' ? Math.PI : Math.PI / 2 - 0.05}
      enableRotate={command.mode !== 'top'}
      enableDamping={false}
    />
  )
}
