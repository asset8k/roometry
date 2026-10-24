import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import ReactThreeTestRenderer from '@react-three/test-renderer'
import { Box3, Mesh, MeshStandardMaterial, Raycaster, Vector3 } from 'three'
import { ASSET_CATALOG, DECOR_CATALOG, dimensionsFor } from './asset-catalog'
import { FurnitureParts } from './FurnitureModel'
import { getModelKind } from './model-registry'
import { loadExampleScene } from '../../lib/test-support/scenes'

type Renderer = Awaited<ReturnType<typeof ReactThreeTestRenderer.create>>
const mounted: Renderer[] = []
beforeAll(() => vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true))
afterAll(() => vi.unstubAllGlobals())
afterEach(async () => { for (const renderer of mounted.splice(0)) await renderer.unmount() })
const configurations = DECOR_CATALOG.flatMap(asset => asset.placements.map(placement => ({ asset, placement })))

describe('decorations and finishing models', () => {
  it('has 106 unique models, covers all 68 types, and resolves every scene-approved ID', () => {
    expect(ASSET_CATALOG).toHaveLength(106)
    expect(new Set(ASSET_CATALOG.map(asset => asset.asset_id)).size).toBe(106)
    expect(new Set(ASSET_CATALOG.map(asset => asset.object_type)).size).toBe(68)
    expect(DECOR_CATALOG).toHaveLength(31)
    for (const asset of ASSET_CATALOG) expect(getModelKind(asset.asset_id)).not.toBe('placeholder')
  })

  it.each(configurations)('$asset.asset_id / $placement renders finite, bounded, recolorable geometry with a mounting contact', async ({ asset, placement }) => {
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
    else expect(bounds.min.y).toBeCloseTo(0)
    expect(dimensionsFor(asset, placement).every(n => Number.isFinite(n) && n > 0)).toBe(true)
    const colors = new Set<string>()
    root.traverse(node => {
      if (!(node instanceof Mesh)) return
      for (const attribute of ['position', 'normal']) expect(Array.from(node.geometry.getAttribute(attribute).array).every(Number.isFinite)).toBe(true)
      if (node.material instanceof MeshStandardMaterial) colors.add(node.material.color.getHexString())
    })
    expect(colors.has('327e57')).toBe(true)
    expect(getModelKind(asset.asset_id)).toBe('decor')
  })

  it.each(['vase_standard_01', 'vase_cylinder_01', 'household_accessory_bowl_01'])('%s has an open interior rather than a solid top', async assetId => {
    const renderer = await ReactThreeTestRenderer.create(<FurnitureParts assetId={assetId} color="#B18468" placement="surface" />)
    mounted.push(renderer)
    const root = renderer.scene.children[0].instance
    root.updateMatrixWorld(true)
    const hit = new Raycaster(new Vector3(.01, 2, 0), new Vector3(0, -1, 0)).intersectObject(root, true)[0]
    expect(hit).toBeDefined()
    expect(hit.point.y).toBeLessThan(.4)
    expect(hit.point.y).toBeGreaterThanOrEqual(0)
  })

  it('keeps an arched mirror’s upper corners empty', async () => {
    const renderer = await ReactThreeTestRenderer.create(<FurnitureParts assetId="mirror_arch_01" color="#A99068" placement="wall" />)
    mounted.push(renderer)
    const root = renderer.scene.children[0].instance
    root.updateMatrixWorld(true)
    const ray = new Raycaster(new Vector3(.47, .98, 1), new Vector3(0, 0, -1))
    expect(ray.intersectObject(root, true)).toHaveLength(0)
    ray.set(new Vector3(0, .9, 1), new Vector3(0, 0, -1))
    expect(ray.intersectObject(root, true).length).toBeGreaterThan(0)
  })

  it('replaces every older example model with a catalog model', () => {
    for (const object of loadExampleScene().objects) expect(ASSET_CATALOG.some(asset => asset.asset_id === object.asset_id)).toBe(true)
  })
})
