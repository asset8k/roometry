import { Vector3 } from 'three'
import type { Group } from 'three'
import type { SceneObject } from '../../lib/scene.generated'
import type { EditorState } from '../../lib/scene-editor'
import type { SceneEdit } from '../../lib/scene-edits'

/** Extract full yaw from the quaternion; Euler.y folds after a quarter turn. */
export function gizmoEdit(object: SceneObject, proxy: Group, mode: EditorState['mode']): SceneEdit {
  if (mode === 'move') return {
    operation: 'move', object_id: object.id,
    position: { x: proxy.position.x, z: proxy.position.z },
  }
  const front = new Vector3(0, 0, 1).applyQuaternion(proxy.quaternion)
  return { operation: 'rotate', object_id: object.id, rotation_y_rad: Math.atan2(front.x, front.z) }
}

