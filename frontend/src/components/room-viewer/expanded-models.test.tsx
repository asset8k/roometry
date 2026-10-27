import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import ReactThreeTestRenderer from '@react-three/test-renderer'
import { Box3, Mesh, MeshStandardMaterial } from 'three'
import { EXPANSION_CATALOG } from './asset-catalog'
import { FurnitureParts } from './FurnitureModel'
import { getModelKind } from './model-registry'

type Renderer = Awaited<ReturnType<typeof ReactThreeTestRenderer.create>>
const mounted: Renderer[] = []
beforeAll(() => vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true))
afterAll(() => vi.unstubAllGlobals())
afterEach(async () => { for (const renderer of mounted.splice(0)) await renderer.unmount() })
const configurations = EXPANSION_CATALOG.flatMap(asset => asset.placements.map(placement => ({ asset, placement })))

describe('expanded catalogue meshes', () => {
  it.each(configurations)('$asset.asset_id / $placement has finite bounded geometry and a real attachment', async ({ asset, placement }) => {
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
    let recolorable = false
    root.traverse(node => {
      if (!(node instanceof Mesh)) return
      expect(Array.from(node.geometry.getAttribute('position').array).every(Number.isFinite)).toBe(true)
      if (node.material instanceof MeshStandardMaterial && node.material.color.getHexString() === '327e57') recolorable = true
    })
    expect(recolorable).toBe(true)
    expect(getModelKind(asset.asset_id)).not.toBe('placeholder')
  })
})
