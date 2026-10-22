import { Line } from '@react-three/drei'
import { DoubleSide, FrontSide } from 'three'
import type { Opening, RoomScene } from '../../lib/scene.generated'
import { getOpeningTransform, getWallFrame, WALLS } from './coordinates'
import { useWallCutaway } from './useWallCutaway'
import { WallModeContext } from './view-settings'

function OpeningMarker({ scene, opening }: { scene: RoomScene; opening: Opening }) {
  const group = useWallCutaway(scene.room, opening.wall)
  const { position, rotationY } = getOpeningTransform(scene.room, opening)
  const halfWidth = opening.width_m / 2
  const halfHeight = opening.height_m / 2
  const isWindow = opening.type === 'window'

  return (
    <group ref={group} position={position} rotation={[0, rotationY, 0]}>
      <mesh>
        <planeGeometry args={[opening.width_m, opening.height_m]} />
        <meshStandardMaterial color={isWindow ? '#b2c9cb' : '#a1ad94'} side={DoubleSide} roughness={isWindow ? 0.3 : 0.9} />
      </mesh>
      <Line
        points={[
          [-halfWidth, -halfHeight, 0.005], [halfWidth, -halfHeight, 0.005],
          [halfWidth, halfHeight, 0.005], [-halfWidth, halfHeight, 0.005],
          [-halfWidth, -halfHeight, 0.005],
        ]}
        color={isWindow ? '#f7f5ee' : '#788569'}
        lineWidth={3}
      />
      {isWindow && (
        <>
          <Line points={[[0, -halfHeight, 0.006], [0, halfHeight, 0.006]]} color="#f7f5ee" lineWidth={3} />
          <Line points={[[-halfWidth, 0, 0.006], [halfWidth, 0, 0.006]]} color="#f7f5ee" lineWidth={3} />
        </>
      )}
      {!isWindow && (
        <mesh position={[halfWidth * 0.65, 0, 0.02]}>
          <sphereGeometry args={[0.025, 12, 8]} />
          <meshStandardMaterial color="#eee3c9" metalness={0.4} roughness={0.4} />
        </mesh>
      )}
    </group>
  )
}

export default function RoomShell({ scene }: { scene: RoomScene }) {
  const wallMode = useContext(WallModeContext)
  const { width_m: width, length_m: length, height_m: height } = scene.room

  const finishes = scene.finishes
  return (
    <group>
      <mesh name="room_floor" position={[width / 2, -0.06, length / 2]} receiveShadow>
        <boxGeometry args={[width, 0.12, length]} />
        <meshStandardMaterial color={finishes?.floor_color ?? '#d6bea0'} roughness={0.95} />
      </mesh>

      {(finishes?.floor_pattern ?? 'wood') === 'wood' && Array.from({ length: Math.ceil(width / 0.3) - 1 }, (_, index) => (
        <Line
          key={index}
          points={[[(index + 1) * 0.3, 0.001, 0], [(index + 1) * 0.3, 0.001, length]]}
          color="#bfa88c"
          lineWidth={0.5}
        />
      ))}

      {finishes?.floor_pattern === 'tile' && <group name="tile_seams">
        {Array.from({ length: Math.ceil(width / .6) - 1 }, (_, i) => <Line key={`x${i}`} points={[[((i + 1) * .6), .001, 0], [((i + 1) * .6), .001, length]]} color="#777777" lineWidth={.4} />)}
        {Array.from({ length: Math.ceil(length / .6) - 1 }, (_, i) => <Line key={`z${i}`} points={[[0, .001, ((i + 1) * .6)], [width, .001, ((i + 1) * .6)]]} color="#777777" lineWidth={.4} />)}
      </group>}
      {wallMode !== 'hidden'  && WALLS.map(wall => {
        const frame = getWallFrame(scene.room, wall)
        return (
          <mesh key={wall} name={`wall_${wall}`} position={frame.position} rotation={[0, frame.rotationY, 0]} receiveShadow>
            <planeGeometry args={[frame.width, height]} />
            {/* Inward-facing planes create a cutaway without changing scene data. */}
            <meshStandardMaterial
              color={finishes?.wall_color ?? (wall === 'west' ? '#e7e3d6' : '#f3f0e7')}
              side={wallMode === 'transparent' ? DoubleSide : FrontSide}
              transparent={wallMode === 'transparent'}
              opacity={wallMode === 'transparent' ? 0.18 : 1}
              depthWrite={wallMode !== 'transparent'}
              roughness={1}
            />
          </mesh>
        )
      })}

      {scene.openings.map(opening => <OpeningMarker key={opening.id} scene={scene} opening={opening} />)}
    </group>
  )
}
import { useContext } from 'react'
