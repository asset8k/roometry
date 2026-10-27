import { Environment, Lightformer } from '@react-three/drei'
import { memo, useMemo } from 'react'
import { Object3D } from 'three'
import type { RoomDimensions } from '../../lib/scene.generated'

export function lightingBounds(room: RoomDimensions) {
  const span = Math.max(room.width_m, room.length_m, room.height_m)
  const radius = Math.hypot(room.width_m / 2, room.length_m / 2, room.height_m * .75) + .5
  return { span, radius, center: [room.width_m / 2, room.height_m * .25, room.length_m / 2] as [number, number, number] }
}

/** One shadow map, captured studio reflections, no external HDR or frame loop. */
function StudioLighting({ room }: { room: RoomDimensions }) {
  const { span, radius, center } = lightingBounds(room)
  const target = useMemo(() => {
    const object = new Object3D()
    object.position.set(...center)
    return object
  }, [center[0], center[1], center[2]])
  return <>
    <ambientLight intensity={.18} />
    <hemisphereLight args={['#f1f5ff', '#b9a991', .55]} />
    <primitive object={target} />
    <directionalLight name="studio_key" target={target}
      position={[center[0] - span * .65, span * 1.5, center[2] + span * .7]}
      color="#fff9f0" intensity={2.4} castShadow
      shadow-mapSize={[2048, 2048]} shadow-radius={3} shadow-bias={-.0001} shadow-normalBias={.008}
      shadow-camera-left={-radius} shadow-camera-right={radius}
      shadow-camera-top={radius} shadow-camera-bottom={-radius}
      shadow-camera-near={.1} shadow-camera-far={span * 5 + 2} />
    <directionalLight position={[center[0] + span, span * .8, center[2] - span]} color="#e5edff" intensity={.6} />
    <Environment resolution={128} frames={1} environmentIntensity={.55}>
      <color attach="background" args={['#a9afb4']} />
      <Lightformer form="rect" color="#ffffff" intensity={3} position={[-4, 4, 2]} scale={[5, 5, 1]} target={[0, 0, 0]} />
      <Lightformer form="rect" color="#e5edff" intensity={1.5} position={[4, 2, -3]} scale={[3, 5, 1]} target={[0, 0, 0]} />
      <Lightformer form="rect" color="#fff0d8" intensity={1} position={[0, 5, 0]} rotation={[Math.PI / 2, 0, 0]} scale={[6, 4, 1]} />
    </Environment>
  </>
}

// Selection, edits and streamed status updates must not recapture the cube map.
export default memo(StudioLighting, (before, after) =>
  before.room.width_m === after.room.width_m && before.room.length_m === after.room.length_m
  && before.room.height_m === after.room.height_m)
