import type { ComponentType } from 'react'
import type { SceneObject } from '../../lib/scene.generated'
import type { ModelKind } from './model-registry'
import FurnitureModel from './FurnitureModel'

export interface ModelProps {
  assetId: string
  color: string
  materialColors?: SceneObject['material_colors']
  placement: SceneObject['placement']
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
  furniture: FurnitureModel,
  equipment: FurnitureModel,
  fixture: FurnitureModel,
  decor: FurnitureModel,
  placeholder: PlaceholderModel,
}
