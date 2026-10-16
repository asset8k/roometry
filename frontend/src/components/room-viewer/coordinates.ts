import type { Opening, RoomDimensions } from '../../lib/scene.generated'

export type Wall = Opening['wall']
export type Point3 = [number, number, number]

export const WALLS: Wall[] = ['north', 'south', 'west', 'east']

/** Match the inward-facing wall planes when hiding attached models. */
export function isWallVisibleFrom(room: RoomDimensions, wall: Wall, camera: Point3): boolean {
  switch (wall) {
    case 'north': return camera[2] >= 0
    case 'south': return camera[2] <= room.length_m
    case 'west': return camera[0] >= 0
    case 'east': return camera[0] <= room.width_m
  }
}

export interface WallFrame {
  position: Point3
  rotationY: number
  width: number
}

/** Wall centers and inward-facing plane normals, in the shared world axes. */
export function getWallFrame(room: RoomDimensions, wall: Wall): WallFrame {
  const { width_m: width, length_m: length, height_m: height } = room

  switch (wall) {
    case 'north': return { position: [width / 2, height / 2, 0], rotationY: 0, width }
    case 'south': return { position: [width / 2, height / 2, length], rotationY: Math.PI, width }
    case 'west': return { position: [0, height / 2, length / 2], rotationY: Math.PI / 2, width: length }
    case 'east': return { position: [width, height / 2, length / 2], rotationY: -Math.PI / 2, width: length }
  }
}

/** Offsets increase along world X/Z on both opposite walls. */
export function getOpeningTransform(room: RoomDimensions, opening: Opening): {
  position: Point3
  rotationY: number
} {
  const alongWall = opening.offset_m + opening.width_m / 2
  const elevation = opening.sill_height_m + opening.height_m / 2
  const inset = 0.012 // Visual separation from wall; not measured geometry.
  const rotationY = getWallFrame(room, opening.wall).rotationY

  switch (opening.wall) {
    case 'north': return { position: [alongWall, elevation, inset], rotationY }
    case 'south': return { position: [alongWall, elevation, room.length_m - inset], rotationY }
    case 'west': return { position: [inset, elevation, alongWall], rotationY }
    case 'east': return { position: [room.width_m - inset, elevation, alongWall], rotationY }
  }
}
