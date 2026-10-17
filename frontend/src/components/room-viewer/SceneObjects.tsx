import type { RoomDimensions, RoomScene, SceneObject } from '../../lib/scene.generated'
import { getModelKind } from './model-registry'
import { OBJECT_MODELS } from './ObjectModels'
import { useWallCutaway } from './useWallCutaway'

export function SceneObjectView({ object, room, selected = false, onSelect }: { object: SceneObject; room?: RoomDimensions; selected?: boolean; onSelect?: (id: string) => void }) {
  const group = useWallCutaway(room, object.placement === 'wall' ? object.wall : null)
  const modelKind = getModelKind(object.asset_id)
  const Model = OBJECT_MODELS[modelKind]
  const { position, dimensions_m: dimensions } = object

  return (
    <group
      ref={group}
      name={object.id}
      position={[position.x, position.y, position.z]}
      rotation={[0, object.rotation_y_rad, 0]}
      scale={[dimensions.width, dimensions.height, dimensions.depth]}
      userData={{ assetId: object.asset_id, modelKind }}
      onClick={event => { event.stopPropagation(); if (event.delta <= 4) onSelect?.(object.id) }}
    >
      <Model color={object.color} placement={object.placement} />
      {selected && <mesh name="selection_bounds" position={[0, 0.5, 0]} raycast={() => undefined} renderOrder={10}>
        <boxGeometry args={[1.04, 1.04, 1.04]} />
        <meshBasicMaterial color="#496751" wireframe depthTest={false} />
      </mesh>}
    </group>
  )
}

export default function SceneObjects({ scene, selectedId = null, onSelect }: { scene: RoomScene; selectedId?: string | null; onSelect?: (id: string) => void }) {
  return (
    <group name="scene_objects">
      {scene.objects.map(object => <SceneObjectView key={object.id} object={object} room={scene.room} selected={object.id === selectedId} onSelect={onSelect} />)}
    </group>
  )
}
