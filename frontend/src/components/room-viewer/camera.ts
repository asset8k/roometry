import { MathUtils, PerspectiveCamera, Vector3 } from 'three'
import type { RoomDimensions } from '../../lib/scene.generated'

export type ViewMode = 'perspective' | 'top'

/** Fit the entire room even in a portrait viewport; top view keeps north up. */
export function frameRoom(camera: PerspectiveCamera, room: RoomDimensions, mode: ViewMode) {
  const { width_m: width, length_m: length, height_m: height } = room
  const halfFov = MathUtils.degToRad(camera.fov / 2)
  const horizontalHalfFov = Math.atan(Math.tan(halfFov) * camera.aspect)
  const target = new Vector3(width / 2, mode === 'top' ? 0 : height / 2, length / 2)

  if (mode === 'top') {
    const distance = height + Math.max(length / (2 * Math.tan(halfFov)), width / (2 * Math.tan(horizontalHalfFov))) * 1.1
    camera.up.set(0, 0, -1)
    camera.position.set(target.x, distance, target.z)
  } else {
    const radius = Math.hypot(width, height, length) / 2
    const distance = radius / Math.sin(Math.min(halfFov, horizontalHalfFov)) * 1.06
    camera.up.set(0, 1, 0)
    camera.position.copy(target).add(new Vector3(-1.25, 1.2, 1.35).normalize().multiplyScalar(distance))
  }

  camera.lookAt(target)
  camera.updateProjectionMatrix()
  return target
}
