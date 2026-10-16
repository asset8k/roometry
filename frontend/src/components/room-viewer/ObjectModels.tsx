import type { ComponentType } from 'react'
import { Quaternion, Vector3 } from 'three'
import type { SceneObject } from '../../lib/scene.generated'
import type { Point3 } from './coordinates'
import type { ModelKind } from './model-registry'

interface ModelProps {
  color: string
  placement: SceneObject['placement']
}

interface BoxProps {
  size: Point3
  at: Point3
  color: string
  rotation?: Point3
}

function Box({ size, at, color, rotation }: BoxProps) {
  return (
    <mesh position={at} rotation={rotation} castShadow receiveShadow>
      <boxGeometry args={size} />
      <meshStandardMaterial color={color} roughness={0.8} />
    </mesh>
  )
}

function Rod({ start, end, radius, color }: { start: Point3; end: Point3; radius: number; color: string }) {
  const a = new Vector3(...start)
  const b = new Vector3(...end)
  const direction = b.clone().sub(a)
  const rotation = new Quaternion().setFromUnitVectors(new Vector3(0, 1, 0), direction.clone().normalize())

  return (
    <mesh position={a.add(b).multiplyScalar(0.5)} quaternion={rotation} castShadow>
      <cylinderGeometry args={[radius, radius, direction.length(), 12]} />
      <meshStandardMaterial color={color} roughness={0.4} metalness={0.35} />
    </mesh>
  )
}

// Every family fits x/z = [-0.5, 0.5], y = [0, 1], with local +Z forward.
// SceneObjectView applies the one world transform and dimensional scale.
function BedModel({ color }: ModelProps) {
  return (
    <group>
      <Box size={[1, 0.28, 1]} at={[0, 0.24, 0]} color="#65594b" />
      {[-0.41, 0.41].flatMap(x => [-0.41, 0.41].map(z => (
        <Box key={`${x}_${z}`} size={[0.09, 0.1, 0.09]} at={[x, 0.05, z]} color="#544b41" />
      )))}
      <Box size={[0.96, 0.24, 0.92]} at={[0, 0.5, 0.02]} color="#eee9df" />
      <Box size={[0.96, 0.04, 0.7]} at={[0, 0.64, 0.12]} color={color} />
      <Box size={[1, 0.48, 0.08]} at={[0, 0.76, -0.46]} color={color} />
      {[-0.23, 0.23].map(x => (
        <Box key={x} size={[0.37, 0.15, 0.23]} at={[x, 0.7, -0.29]} color="#f9f6ef" />
      ))}
    </group>
  )
}

function WardrobeModel({ color }: ModelProps) {
  return (
    <group>
      <Box size={[1, 0.94, 0.94]} at={[0, 0.53, -0.03]} color={color} />
      <Box size={[0.92, 0.06, 0.86]} at={[0, 0.03, -0.03]} color="#685c4e" />
      {[-0.25, 0.25].map(x => (
        <Box key={x} size={[0.48, 0.9, 0.04]} at={[x, 0.52, 0.46]} color={color} />
      ))}
      <Box size={[0.01, 0.9, 0.005]} at={[0, 0.52, 0.483]} color="#685c4e" />
      {[-0.045, 0.045].map(x => (
        <Box key={x} size={[0.02, 0.12, 0.02]} at={[x, 0.53, 0.49]} color="#d5c6a8" />
      ))}
    </group>
  )
}

function RugModel({ color }: ModelProps) {
  return (
    <group>
      <Box size={[1, 0.9, 1]} at={[0, 0.45, 0]} color={color} />
      {[-0.48, 0.48].map(edge => (
        <group key={edge}>
          <Box size={[0.04, 0.1, 1]} at={[edge, 0.95, 0]} color="#d9dcc8" />
          <Box size={[0.92, 0.1, 0.04]} at={[0, 0.95, edge]} color="#d9dcc8" />
        </group>
      ))}
    </group>
  )
}

function TableModel({ color }: ModelProps) {
  return (
    <group>
      <Box size={[1, 0.12, 1]} at={[0, 0.94, 0]} color={color} />
      {[-0.42, 0.42].flatMap(x => [-0.4, 0.4].map(z => (
        <Box key={`${x}_${z}`} size={[0.07, 0.88, 0.1]} at={[x, 0.44, z]} color="#685c4e" />
      )))}
    </group>
  )
}

function ComputerModel({ color }: ModelProps) {
  return (
    <group>
      <Box size={[0.78, 0.62, 0.12]} at={[-0.11, 0.69, -0.3]} color={color} />
      <Box size={[0.69, 0.51, 0.008]} at={[-0.11, 0.69, -0.235]} color="#263b3e" />
      <Box size={[0.06, 0.345, 0.06]} at={[-0.11, 0.2075, -0.29]} color={color} />
      <Box size={[0.4, 0.035, 0.25]} at={[-0.11, 0.0175, -0.26]} color={color} />
      <Box size={[0.65, 0.045, 0.22]} at={[-0.17, 0.0225, 0.34]} color={color} />
      {[-0.06, 0, 0.06].map(row => (
        <Box key={row} size={[0.58, 0.004, 0.03]} at={[-0.17, 0.047, 0.34 + row]} color="#424847" />
      ))}
      <Box size={[0.17, 0.62, 0.75]} at={[0.4, 0.31, 0]} color={color} />
      <Box size={[0.02, 0.02, 0.008]} at={[0.4, 0.5, 0.38]} color="#acb7a1" />
    </group>
  )
}

function TVModel({ color, placement }: ModelProps) {
  const onSurface = placement === 'surface'
  const height = onSurface ? 0.82 : 1
  const center = onSurface ? 0.59 : 0.5

  return (
    <group>
      <Box size={[1, height, 0.9]} at={[0, center, -0.05]} color={color} />
      <Box size={[0.91, height * 0.83, 0.03]} at={[0, center, 0.425]} color="#26383c" />
      {onSurface && [-0.29, 0.29].map(x => (
        <group key={x}>
          <Box size={[0.035, 0.14, 0.05]} at={[x, 0.11, -0.05]} color={color} />
          <Box size={[0.18, 0.04, 0.6]} at={[x, 0.02, 0.1]} color={color} />
        </group>
      ))}
    </group>
  )
}

function ClockModel({ color }: ModelProps) {
  return (
    <group>
      <mesh position={[0, 0.5, -0.04]} rotation={[Math.PI / 2, 0, 0]} castShadow>
        <cylinderGeometry args={[0.5, 0.5, 0.92, 32]} />
        <meshStandardMaterial color={color} roughness={0.6} />
      </mesh>
      <mesh position={[0, 0.5, 0.425]}>
        <circleGeometry args={[0.43, 32]} />
        <meshStandardMaterial color="#f7f4e8" roughness={1} />
      </mesh>
      {Array.from({ length: 12 }, (_, index) => {
        const angle = index * Math.PI / 6
        return (
          <Box key={index} size={[0.025, 0.06, 0.015]} at={[Math.sin(angle) * 0.36, 0.5 + Math.cos(angle) * 0.36, 0.445]} rotation={[0, 0, -angle]} color="#545b4f" />
        )
      })}
      {[-Math.PI / 3, Math.PI / 3].map((angle, index) => {
        const length = index === 0 ? 0.25 : 0.34
        return (
          <Box key={angle} size={[0.035, length, 0.02]} at={[Math.sin(angle) * length / 2, 0.5 + Math.cos(angle) * length / 2, 0.47]} rotation={[0, 0, -angle]} color="#424c40" />
        )
      })}
    </group>
  )
}

function ChandelierModel({ color }: ModelProps) {
  return (
    <group>
      <Rod start={[0, 0.4, 0]} end={[0, 1, 0]} radius={0.018} color={color} />
      <mesh position={[0, 0.97, 0]}>
        <cylinderGeometry args={[0.09, 0.09, 0.06, 16]} />
        <meshStandardMaterial color={color} roughness={0.4} metalness={0.35} />
      </mesh>
      {Array.from({ length: 5 }, (_, index) => {
        const angle = index * Math.PI * 2 / 5
        const x = Math.cos(angle) * 0.35
        const z = Math.sin(angle) * 0.35
        return (
          <group key={index}>
            <Rod start={[0, 0.4, 0]} end={[x, 0.3, z]} radius={0.015} color={color} />
            <Rod start={[x, 0.3, z]} end={[x, 0.22, z]} radius={0.018} color={color} />
            <mesh position={[x, 0.2, z]} castShadow>
              <cylinderGeometry args={[0.07, 0.12, 0.16, 12, 1, true]} />
              <meshStandardMaterial color={color} roughness={0.6} />
            </mesh>
            <mesh position={[x, 0.08, z]}>
              <sphereGeometry args={[0.08, 12, 8]} />
              <meshStandardMaterial color="#fff0cc" emissive="#ffdf99" emissiveIntensity={0.3} roughness={0.4} />
            </mesh>
          </group>
        )
      })}
    </group>
  )
}

function CurtainModel({ color }: ModelProps) {
  return (
    <group>
      <Rod start={[-0.5, 0.93, 0]} end={[0.5, 0.93, 0]} radius={0.035} color="#756a59" />
      {[-0.46, 0.46].map(x => (
        <Box key={x} size={[0.06, 0.08, 0.6]} at={[x, 0.94, -0.2]} color="#756a59" />
      ))}
      {[-1, 1].flatMap(side => Array.from({ length: 5 }, (_, index) => (
        <Box
          key={`${side}_${index}`}
          size={[0.052, 0.88, 0.16]}
          at={[side * (0.235 + index * 0.052), 0.44, index % 2 === 0 ? 0.39 : 0.23]}
          color={color}
        />
      )))}
    </group>
  )
}

function PlaceholderModel({ color }: ModelProps) {
  return (
    <group>
      <mesh position={[0, 0.5, 0]}>
        <boxGeometry args={[1, 1, 1]} />
        <meshStandardMaterial color={color} transparent opacity={0.35} roughness={1} />
      </mesh>
      <mesh position={[0, 0.5, 0]}>
        <boxGeometry args={[1, 1, 1]} />
        <meshBasicMaterial color="#64705d" wireframe />
      </mesh>
    </group>
  )
}

export const OBJECT_MODELS: Record<ModelKind, ComponentType<ModelProps>> = {
  bed: BedModel,
  wardrobe: WardrobeModel,
  rug: RugModel,
  table: TableModel,
  computer: ComputerModel,
  tv: TVModel,
  clock: ClockModel,
  chandelier: ChandelierModel,
  curtain: CurtainModel,
  placeholder: PlaceholderModel,
}
