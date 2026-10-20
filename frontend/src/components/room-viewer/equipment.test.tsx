import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import ReactThreeTestRenderer from '@react-three/test-renderer'
import { Box3, Mesh, MeshStandardMaterial } from 'three'
import { ASSET_CATALOG, EQUIPMENT_CATALOG, dimensionsFor } from './asset-catalog'
import { FurnitureParts } from './FurnitureModel'
import { getModelKind } from './model-registry'

type Renderer = Awaited<ReturnType<typeof ReactThreeTestRenderer.create>>
const mounted: Renderer[] = []
beforeAll(() => vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true))
afterAll(() => vi.unstubAllGlobals())
afterEach(async () => { for (const renderer of mounted.splice(0)) await renderer.unmount() })

const configurations = EQUIPMENT_CATALOG.flatMap(asset => asset.placements.map(placement => ({ asset, placement })))

describe('electronics, lighting and climate models', () => {
  it.each(configurations)('$asset.asset_id / $placement renders bounded, finite, recolorable geometry', async ({ asset, placement }) => {
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
    else if (placement === 'ceiling') expect(bounds.max.y).toBeCloseTo(1)
    else expect(bounds.min.y).toBeCloseTo(0)
    const colors = new Set<string>()
    root.traverse(node => {
      if (!(node instanceof Mesh)) return
      expect(Array.from(node.geometry.getAttribute('position').array).every(Number.isFinite)).toBe(true)
      if (node.material instanceof MeshStandardMaterial) colors.add(node.material.color.getHexString())
    })
    expect(colors.has('327e57')).toBe(true)
    expect(getModelKind(asset.asset_id)).toBe('equipment')
  })

  it('replaces TV feet with a mount without remounting the parent object', async () => {
    const renderer = await ReactThreeTestRenderer.create(<FurnitureParts assetId="tv_standard_01" color="#72797C" placement="surface" />)
    mounted.push(renderer)
    const root = renderer.scene.children[0].instance
    expect(root.getObjectByName('surface_foot_-0.32')).toBeDefined()
    await renderer.update(<FurnitureParts assetId="tv_standard_01" color="#327E57" placement="wall" />)
    const next = renderer.scene.children[0].instance
    expect(next).toBe(root)
    expect(next.getObjectByName('surface_foot_-0.32')).toBeUndefined()
    expect(next.getObjectByName('wall_wall_plate')).toBeDefined()
    const tv = EQUIPMENT_CATALOG.find(asset => asset.object_type === 'tv')!
    expect(dimensionsFor(tv, 'wall')[2]).toBeLessThan(dimensionsFor(tv, 'surface')[2])
  })

  it('keeps a lamp bulb warm while recoloring the shade', async () => {
    const renderer = await ReactThreeTestRenderer.create(<FurnitureParts assetId="table_lamp_standard_01" color="#327E57" />)
    mounted.push(renderer)
    const root = renderer.scene.children[0].instance
    const material = (root.getObjectByName('bulb') as Mesh).material as MeshStandardMaterial
    expect(material.emissiveIntensity).toBeGreaterThan(0)
    const bulbColor = material.color.getHexString()
    await renderer.update(<FurnitureParts assetId="table_lamp_standard_01" color="#C48665" />)
    expect(((root.getObjectByName('bulb') as Mesh).material as MeshStandardMaterial).color.getHexString()).toBe(bulbColor)
  })

  it('has unique asset IDs across both batches', () => {
    expect(new Set(ASSET_CATALOG.map(asset => asset.asset_id)).size).toBe(ASSET_CATALOG.length)
    expect(new Set(EQUIPMENT_CATALOG.map(asset => asset.object_type)).size).toBe(16)
  })
})
