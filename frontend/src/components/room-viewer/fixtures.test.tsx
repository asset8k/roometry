import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import ReactThreeTestRenderer from '@react-three/test-renderer'
import { Box3, Mesh, MeshStandardMaterial, Raycaster, Vector3 } from 'three'
import { FIXTURE_CATALOG, dimensionsFor } from './asset-catalog'
import { FurnitureParts } from './FurnitureModel'
import { getModelKind } from './model-registry'
import SceneObjects from './SceneObjects'
import { parseRoomScene } from '../../lib/scene'
import kitchenExample from '../../lib/test-support/fixtures/kitchen_catalog_scene.json'
import bathroomExample from '../../lib/test-support/fixtures/bathroom_catalog_scene.json'
import decorExample from '../../lib/test-support/fixtures/decor_catalog_scene.json'

type Renderer = Awaited<ReturnType<typeof ReactThreeTestRenderer.create>>
const mounted: Renderer[] = []
beforeAll(() => vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true))
afterAll(() => vi.unstubAllGlobals())
afterEach(async () => { for (const renderer of mounted.splice(0)) await renderer.unmount() })
const configurations = FIXTURE_CATALOG.flatMap(asset => asset.placements.map(placement => ({ asset, placement })))

describe('kitchen, bathroom and utility fixture models', () => {
  it.each([kitchenExample, bathroomExample, decorExample])('renders the authored $room.room_type example without placeholders or scene mutations', async raw => {
    const scene = parseRoomScene(raw)
    const original = structuredClone(scene)
    const renderer = await ReactThreeTestRenderer.create(<SceneObjects scene={scene} />)
    mounted.push(renderer)
    expect(renderer.scene.children[0].children).toHaveLength(scene.objects.length)
    for (const object of scene.objects) expect(getModelKind(object.asset_id)).not.toBe('placeholder')
    const bounds = new Box3().setFromObject(renderer.scene.children[0].instance)
    expect(bounds.min.x).toBeGreaterThanOrEqual(-.000001)
    expect(bounds.min.y).toBeGreaterThanOrEqual(-.000001)
    expect(bounds.min.z).toBeGreaterThanOrEqual(-.000001)
    expect(bounds.max.x).toBeLessThanOrEqual(scene.room.width_m + .000001)
    expect(bounds.max.y).toBeLessThanOrEqual(scene.room.height_m + .000001)
    expect(bounds.max.z).toBeLessThanOrEqual(scene.room.length_m + .000001)
    expect(scene).toEqual(original)
  })

  it.each(configurations)('$asset.asset_id / $placement renders finite, bounded and recolorable geometry', async ({ asset, placement }) => {
    const renderer = await ReactThreeTestRenderer.create(<FurnitureParts assetId={asset.asset_id} color="#327E57" placement={placement} />)
    mounted.push(renderer)
    const root = renderer.scene.children[0].instance
    const bounds = new Box3().setFromObject(root)
    expect(bounds.isEmpty()).toBe(false)
    for (const [value, low, high] of [
      [bounds.min.x, -.500001, .500001], [bounds.max.x, -.500001, .500001],
      [bounds.min.y, -.000001, 1.000001], [bounds.max.y, -.000001, 1.000001],
      [bounds.min.z, -.500001, .500001], [bounds.max.z, -.500001, .500001],
    ]) {
      expect(value).toBeGreaterThanOrEqual(low)
      expect(value).toBeLessThanOrEqual(high)
    }
    if (placement === 'wall') expect(bounds.min.z).toBeCloseTo(-.5)
    else if (placement === 'floor' || placement === 'surface') expect(bounds.min.y).toBeCloseTo(0)
    const colors = new Set<string>()
    root.traverse(node => {
      if (!(node instanceof Mesh)) return
      for (const attribute of ['position', 'normal']) expect(Array.from(node.geometry.getAttribute(attribute).array).every(Number.isFinite)).toBe(true)
      if (node.material instanceof MeshStandardMaterial) colors.add(node.material.color.getHexString())
    })
    expect(colors.has('327e57')).toBe(true)
    expect(getModelKind(asset.asset_id)).toBe('fixture')
  })

  it.each([
    ['bathtub_standard_01', 'tub', 0], ['sink_standard_01', 'floor_bowl', .04],
    ['toilet_standard_01', 'floor_bowl', .1], ['vanity_standard_01', 'floor_bowl', .06],
  ])('%s has a visible cavity with a bottom below its rim', async (assetId, name, z) => {
    const renderer = await ReactThreeTestRenderer.create(<FurnitureParts assetId={assetId} color="#D4D8CF" placement="floor" />)
    mounted.push(renderer)
    const root = renderer.scene.children[0].instance
    root.updateMatrixWorld(true)
    const bowl = root.getObjectByName(name as string) as Mesh
    const box = new Box3().setFromObject(bowl)
    const ray = new Raycaster(new Vector3(.01, 2, z as number), new Vector3(0, -1, 0))
    const hit = ray.intersectObject(bowl, false)[0]
    expect(hit).toBeDefined()
    expect(hit.point.y).toBeLessThan(box.max.y - box.getSize(new Vector3()).y * .5)
    expect(hit.point.y).toBeGreaterThan(box.min.y)
  })

  it('switches a toilet to wall-hung geometry without leaving a cistern or pedestal', async () => {
    const renderer = await ReactThreeTestRenderer.create(<FurnitureParts assetId="toilet_standard_01" color="#D4D8CF" placement="floor" />)
    mounted.push(renderer)
    await renderer.update(<FurnitureParts assetId="toilet_standard_01" color="#D4D8CF" placement="wall" />)
    const root = renderer.scene.children[0].instance
    expect(root.getObjectByName('floor_cistern')).toBeUndefined()
    expect(root.getObjectByName('floor_base')).toBeUndefined()
    expect(root.getObjectByName('wall_wall_plate')).toBeDefined()
    const toilet = FIXTURE_CATALOG.find(a => a.object_type === 'toilet')!
    expect(dimensionsFor(toilet, 'wall')[1]).toBeLessThan(dimensionsFor(toilet, 'floor')[1])
  })
})
