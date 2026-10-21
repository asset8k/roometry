import { describe, expect, it } from 'vitest'
import { PerspectiveCamera, Vector3 } from 'three'
import { frameRoom } from './camera'
import { loadExampleScene } from '../../lib/test-support/scenes'

const exampleRoom = loadExampleScene().room

describe('camera presets', () => {
  it.each([
    [4, 3.5, 2.6, 2], [4, 4, 2.6, 0.7], [1, 1, 10, 0.5], [30, 1, 2, 0.5], [1, 30, 2, 2],
  ])('fits every room corner for %s × %s × %s at aspect %s', (width, length, height, aspect) => {
    for (const mode of ['perspective', 'top'] as const) {
      const camera = new PerspectiveCamera(42, aspect, 0.01, 2000)
      const room = { ...exampleRoom, width_m: width, length_m: length, height_m: height }
      frameRoom(camera, room, mode)
      camera.updateMatrixWorld()
      for (const x of [0, width]) for (const y of [0, height]) for (const z of [0, length]) {
        const projected = new Vector3(x, y, z).project(camera)
        expect(Math.abs(projected.x)).toBeLessThan(1)
        expect(Math.abs(projected.y)).toBeLessThan(1)
        expect(projected.z).toBeGreaterThan(-1)
        expect(projected.z).toBeLessThan(1)
      }
    }
  })

  it('looks straight down with north at the top of the screen', () => {
    const camera = new PerspectiveCamera(42, 1, 0.01, 2000)
    frameRoom(camera, exampleRoom, 'top')
    camera.updateMatrixWorld()
    expect(camera.getWorldDirection(new Vector3()).y).toBeCloseTo(-1)
    const north = new Vector3(2, 0, 0).project(camera)
    const south = new Vector3(2, 0, 3.5).project(camera)
    expect(north.y).toBeGreaterThan(south.y)
  })

  it('restores a predictable perspective after moving or using top-down', () => {
    const camera = new PerspectiveCamera(42, 1.5, 0.01, 2000)
    const target = frameRoom(camera, exampleRoom, 'perspective')
    const initialPosition = camera.position.clone()
    const initialRotation = camera.quaternion.clone()
    frameRoom(camera, exampleRoom, 'top')
    camera.position.addScalar(20)
    expect(frameRoom(camera, exampleRoom, 'perspective').equals(target)).toBe(true)
    expect(camera.position.equals(initialPosition)).toBe(true)
    expect(camera.quaternion.angleTo(initialRotation)).toBeCloseTo(0)
    expect(camera.up.toArray()).toEqual([0, 1, 0])
  })
})
