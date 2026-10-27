import { useEffect } from 'react'
import { afterAll, afterEach, beforeAll, expect, it, vi } from 'vitest'
import ReactThreeTestRenderer from '@react-three/test-renderer'
import { DataTexture, Mesh, MeshPhysicalMaterial, MeshStandardMaterial, Vector3 } from 'three'
import { SurfaceTextureProvider, useSurfaceTextures } from './SurfaceTextures'
import { FurnitureParts } from './FurnitureModel'
import { lightingBounds } from './StudioLighting'
import RoomShell from './RoomShell'
import { loadExampleScene } from '../../lib/test-support/scenes'

type Renderer = Awaited<ReturnType<typeof ReactThreeTestRenderer.create>>
type Maps = NonNullable<ReturnType<typeof useSurfaceTextures>>
const mounted: Renderer[] = []
beforeAll(() => vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true))
afterAll(() => vi.unstubAllGlobals())
afterEach(async () => { for (const renderer of mounted.splice(0)) await renderer.unmount() })

function Probe({ capture }: { capture: (maps: Maps) => void }) {
  const maps = useSurfaceTextures()
  useEffect(() => { if (maps) capture(maps) }, [maps, capture])
  return null
}

it('reuses surface maps on color edits and releases them without affecting another viewer', async () => {
  const bundles: Maps[] = []
  const capture = (maps: Maps) => bundles.push(maps)
  const content = (color: string) => <SurfaceTextureProvider>
    <Probe capture={capture} /><FurnitureParts assetId="sofa_standard_01" color={color} />
  </SurfaceTextureProvider>
  const first = await ReactThreeTestRenderer.create(content('#889988'))
  const second = await ReactThreeTestRenderer.create(content('#556677'))
  mounted.push(second)
  expect(bundles).toHaveLength(2)
  expect(bundles[0].fabric).not.toBe(bundles[1].fabric)
  const firstDispose = vi.fn(), secondDispose = vi.fn()
  bundles[0].fabric.addEventListener('dispose', firstDispose)
  bundles[1].fabric.addEventListener('dispose', secondDispose)
  await first.update(content('#334455'))
  expect(bundles).toHaveLength(2)
  expect(firstDispose).not.toHaveBeenCalled()
  await first.unmount()
  expect(firstDispose).toHaveBeenCalledOnce()
  expect(secondDispose).not.toHaveBeenCalled()
})

it('keeps plank scale in metres, preserves observed colors, and disposes resized floor maps', async () => {
  const scene = loadExampleScene()
  scene.room.width_m = 3; scene.room.length_m = 6
  scene.finishes = { floor_color: '#987651', wall_color: '#B2ABA1', floor_pattern: 'wood' }
  const before = structuredClone(scene)
  const content = () => <SurfaceTextureProvider><RoomShell scene={scene} /></SurfaceTextureProvider>
  const renderer = await ReactThreeTestRenderer.create(content()); mounted.push(renderer)
  const floor = renderer.scene.children[0].instance.getObjectByName('room_floor') as Mesh
  const material = floor.material as MeshStandardMaterial
  const texture = material.map!
  const disposed = vi.fn(); texture.addEventListener('dispose', disposed)
  expect(texture.repeat.toArray()).toEqual([1.25, 2.5])
  expect(material.color.getHexString()).toBe('987651')
  expect(scene).toEqual(before)
  scene.room.width_m = 6
  await renderer.update(content())
  expect(disposed).toHaveBeenCalledOnce()
  expect((floor.material as MeshStandardMaterial).map!.repeat.toArray()).toEqual([2.5, 2.5])
  expect(texture).toBeInstanceOf(DataTexture)
  expect((texture as DataTexture).image.width).toBeLessThanOrEqual(512)
})

it('leaves plain finishes unpatterned and releases the patterned map when switching', async () => {
  const scene = loadExampleScene()
  scene.finishes = { floor_color: '#999999', wall_color: '#EEEEEE', floor_pattern: 'tile' }
  const content = () => <SurfaceTextureProvider><RoomShell scene={scene} /></SurfaceTextureProvider>
  const renderer = await ReactThreeTestRenderer.create(content()); mounted.push(renderer)
  const floor = renderer.scene.children[0].instance.getObjectByName('room_floor') as Mesh
  const texture = (floor.material as MeshStandardMaterial).map!
  const disposed = vi.fn(); texture.addEventListener('dispose', disposed)
  scene.finishes.floor_pattern = 'plain'
  await renderer.update(content())
  expect((floor.material as MeshStandardMaterial).map).toBeNull()
  expect((floor.material as MeshStandardMaterial).bumpMap).toBeNull()
  expect(disposed).toHaveBeenCalledOnce()
})

it('uses reflective glass without treating glass as an opaque shadow caster', async () => {
  const renderer = await ReactThreeTestRenderer.create(<FurnitureParts assetId="coffee_table_round_glass_01" color="#775533" />)
  mounted.push(renderer)
  const panes: Mesh[] = []
  renderer.scene.children[0].instance.traverse(node => {
    if (node instanceof Mesh && node.material instanceof MeshPhysicalMaterial && node.material.transparent) panes.push(node)
  })
  expect(panes.length).toBeGreaterThan(0)
  for (const pane of panes) {
    const material = pane.material as MeshPhysicalMaterial
    expect(pane.castShadow).toBe(false)
    expect(material.depthWrite).toBe(false)
    expect(material.metalness).toBe(0)
    expect(material.transmission).toBe(0) // No repeated offscreen transmission passes.
  }
})

it.each([[3, 6, 3], [12, 2, 2.4], [2, 2, 8]])('fits the shadow volume to a %s × %s × %s room', (width, length, height) => {
  const room = { ...loadExampleScene().room, width_m: width, length_m: length, height_m: height }
  const { radius, center } = lightingBounds(room)
  for (const x of [0, width]) for (const y of [0, height]) for (const z of [0, length]) {
    expect(new Vector3(x, y, z).distanceTo(new Vector3(...center))).toBeLessThan(radius)
  }
})
