import { useContext, useMemo, useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import { Vector3 } from 'three'
import type { Group } from 'three'
import type { RoomDimensions } from '../../lib/scene.generated'
import { isWallVisibleFrom } from './coordinates'
import type { Wall } from './coordinates'
import { WallModeContext } from './view-settings'

/** Keep wall attachments in the same cutaway as their inward-facing wall. */
export function useWallCutaway(room: RoomDimensions | undefined, wall: Wall | null) {
  const group = useRef<Group>(null)
  const mode = useContext(WallModeContext)
  const cameraPosition = useMemo(() => new Vector3(), [])

  useFrame(({ camera }) => {
    if (!group.current) return
    if (!room || wall === null || mode !== 'cutaway') {
      group.current.visible = true
      return
    }
    camera.getWorldPosition(cameraPosition)
    group.current.visible = isWallVisibleFrom(room, wall, [cameraPosition.x, cameraPosition.y, cameraPosition.z])
  })

  return group
}
