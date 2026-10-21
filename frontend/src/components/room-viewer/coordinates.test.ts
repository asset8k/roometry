import { describe, expect, it } from 'vitest'
import { loadExampleScene } from '../../lib/test-support/scenes'
import { getOpeningTransform, getWallFrame, isWallVisibleFrom } from './coordinates'
import type { Wall } from './coordinates'

const room = { ...loadExampleScene().room, width_m: 5, length_m: 3, height_m: 2.8 }

describe('room wall frames', () => {
  it.each<[Wall, [number, number, number], boolean]>([
    ['north', [2, 2, -1], false], ['north', [2, 2, 1], true],
    ['south', [2, 2, 4], false], ['south', [2, 2, 1], true],
    ['west', [-1, 2, 1], false], ['west', [1, 2, 1], true],
    ['east', [6, 2, 1], false], ['east', [1, 2, 1], true],
  ])('matches cutaway visibility for the %s wall at %s', (wall, camera, visible) => {
    expect(isWallVisibleFrom(room, wall, camera)).toBe(visible)
  })

  it.each<[Wall, number[], number]>([
    ['north', [2.5, 1.4, 0], 5],
    ['south', [2.5, 1.4, 3], 5],
    ['west', [0, 1.4, 1.5], 3],
    ['east', [5, 1.4, 1.5], 3],
  ])('places the %s wall on the interior boundary', (wall, center, width) => {
    const frame = getWallFrame(room, wall)
    expect(frame.position).toEqual(center)
    expect(frame.width).toBe(width)
  })

  it.each<[Wall, number[]]>([
    ['north', [0, 1]], ['south', [0, -1]], ['west', [1, 0]], ['east', [-1, 0]],
  ])('faces the %s wall into the room', (wall, normal) => {
    const angle = getWallFrame(room, wall).rotationY
    expect(Math.sin(angle)).toBeCloseTo(normal[0])
    expect(Math.cos(angle)).toBeCloseTo(normal[1])
  })
})

describe('opening offsets', () => {
  it.each<Wall>(['north', 'south', 'west', 'east'])('keeps offsets increasing in world coordinates on %s', wall => {
    const opening = { ...loadExampleScene().openings[0], wall, offset_m: 0.4, width_m: 0.8 }
    const transform = getOpeningTransform(room, opening)
    const axis = wall === 'north' || wall === 'south' ? 0 : 2
    expect(transform.position[axis]).toBeCloseTo(0.8)
    const moved = getOpeningTransform(room, { ...opening, offset_m: 0.9 })
    expect(moved.position[axis] - transform.position[axis]).toBeCloseTo(0.5)
  })

  it('adds sill height to the window vertical center', () => {
    const opening = { ...loadExampleScene().openings[1], sill_height_m: 0.9, height_m: 1.1 }
    expect(getOpeningTransform(room, opening).position[1]).toBeCloseTo(1.45)
  })

  it('works when width and length are equal', () => {
    expect(getWallFrame({ ...room, length_m: 5 }, 'east').width).toBe(5)
  })
})
