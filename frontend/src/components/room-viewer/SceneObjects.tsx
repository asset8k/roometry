import type { RoomDimensions, RoomScene, SceneObject } from '../../lib/scene.generated'
import { getModelKind } from './model-registry'
import { OBJECT_MODELS } from './ObjectModels'
import { useWallCutaway } from './useWallCutaway'

export function SceneObjectView({ object, room }: { object: SceneObject; room?: RoomDimensions }) {
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
    >
      <Model color={object.color} placement={object.placement} />
    </group>
  )
}

export default function SceneObjects({ scene }: { scene: RoomScene }) {
  return (
    <group name="scene_objects">
      {scene.objects.map(object => <SceneObjectView key={object.id} object={object} room={scene.room} />)}
    </group>
  )
}
