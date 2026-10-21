import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import ReactThreeTestRenderer from '@react-three/test-renderer'
import { DoubleSide, FrontSide, Mesh, MeshStandardMaterial } from 'three'
import RoomShell from './RoomShell'
import SceneObjects from './SceneObjects'
import { WallModeContext } from './view-settings'
import { loadExampleScene } from '../../lib/test-support/scenes'

type Renderer = Awaited<ReturnType<typeof ReactThreeTestRenderer.create>>
const renderers: Renderer[] = []
beforeAll(() => vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true))
afterAll(() => vi.unstubAllGlobals())
afterEach(async () => {
  for (const renderer of renderers.splice(0)) await renderer.unmount()
})

describe('wall display settings', () => {
  it.each(['cutaway', 'transparent', 'hidden'] as const)('%s keeps shell and wall objects consistent without changing source data', async mode => {
    const scene = loadExampleScene()
    const before = structuredClone(scene)
    const renderer = await ReactThreeTestRenderer.create(
      <WallModeContext.Provider value={mode}>
        <RoomShell scene={scene} />
        <SceneObjects scene={scene} />
      </WallModeContext.Provider>,
      { camera: { position: [2, 2, 6] } },
    )
    renderers.push(renderer)
    await renderer.advanceFrames(1, 1 / 60)
    const shell = renderer.scene.children[0].instance
    const wall = shell.getObjectByName('wall_south')
    const tv = renderer.scene.children[1].instance.getObjectByName('tv_1')!
    expect(tv.visible).toBe(mode !== 'cutaway')
    if (mode === 'hidden') {
      expect(wall).toBeUndefined()
    } else {
      expect(wall).toBeInstanceOf(Mesh)
      const material = (wall as Mesh).material as MeshStandardMaterial
      expect(material.side).toBe(mode === 'cutaway' ? FrontSide : DoubleSide)
      expect(material.transparent).toBe(mode === 'transparent')
      expect(material.depthWrite).toBe(mode === 'cutaway')
    }
    expect(scene).toEqual(before)
  })
})
