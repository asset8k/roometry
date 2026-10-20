import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import ReactThreeTestRenderer from '@react-three/test-renderer'
import { Box3, Mesh, MeshStandardMaterial } from 'three'
import { FURNITURE_CATALOG, getFurnitureAsset } from './furniture-catalog'
import { FurnitureParts } from './FurnitureModel'
import { getModelKind } from './model-registry'
import { SceneObjectView } from './SceneObjects'
import { loadExampleScene } from '../../lib/scene'

type Renderer = Awaited<ReturnType<typeof ReactThreeTestRenderer.create>>
const mounted: Renderer[] = []
beforeAll(() => vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true))
afterAll(() => vi.unstubAllGlobals())
afterEach(async () => { for (const renderer of mounted.splice(0)) await renderer.unmount() })

describe('core furniture models', () => {
  it.each(FURNITURE_CATALOG)('$asset_id has bounded, recolorable geometry and no placeholder', async asset => {
    const renderer = await ReactThreeTestRenderer.create(<FurnitureParts assetId={asset.asset_id} color="#327E57" />)
    mounted.push(renderer)
    const root = renderer.scene.children[0].instance
    const bounds = new Box3().setFromObject(root)
    expect(bounds.isEmpty()).toBe(false)
    for (const [value, min, max] of [
      [bounds.min.x, -.500001, .500001], [bounds.max.x, -.500001, .500001],
      [bounds.min.y, -.000001, 1.000001], [bounds.max.y, -.000001, 1.000001],
      [bounds.min.z, -.500001, .500001], [bounds.max.z, -.500001, .500001],
    ]) {
      expect(value).toBeGreaterThanOrEqual(min)
      expect(value).toBeLessThanOrEqual(max)
    }
    let primary = false
    root.traverse(node => {
      if (node instanceof Mesh && node.material instanceof MeshStandardMaterial && node.material.color.getHexString() === '327e57') primary = true
    })
    expect(primary).toBe(true)
    expect(getModelKind(asset.asset_id)).toBe('furniture')
    expect(root.children).toHaveLength(asset.parts.length)
  })

  it('recolors an existing mesh without rebuilding its geometry', async () => {
    const renderer = await ReactThreeTestRenderer.create(<FurnitureParts assetId="sofa_standard_01" color="#327E57" />)
    mounted.push(renderer)
    const original = renderer.scene.children[0].instance.getObjectByName('seat_0') as Mesh
    const geometry = original.geometry
    await renderer.update(<FurnitureParts assetId="sofa_standard_01" color="#B4A48F" />)
    const updated = renderer.scene.children[0].instance.getObjectByName('seat_0') as Mesh
    expect(updated.geometry).toBe(geometry)
    expect((updated.material as MeshStandardMaterial).color.getHexString()).toBe('b4a48f')
  })

  it('shows only declared solids in geometry mode', async () => {
    const asset = getFurnitureAsset('chair_standard_01')!
    const renderer = await ReactThreeTestRenderer.create(<FurnitureParts assetId={asset.asset_id} color={asset.color} collisionOnly />)
    mounted.push(renderer)
    const root = renderer.scene.children[0].instance
    expect(root.getObjectByName('back_seam')).toBeUndefined()
    expect(root.children).toHaveLength(asset.parts.filter(part => part.collision).length)
    expect(getFurnitureAsset('__proto__')).toBeUndefined()
  })

  it('applies scene dimensions, translation, and rotation to a sectional', async () => {
    const object = {
      ...loadExampleScene().objects[0], type: 'sofa' as const, asset_id: 'sofa_sectional_left_01' as const,
      dimensions_m: { width: 2.8, height: .88, depth: 1.65 },
      position: { x: 3, y: 0, z: 2 }, rotation_y_rad: Math.PI / 2,
    }
    const renderer = await ReactThreeTestRenderer.create(<SceneObjectView object={object} />)
    mounted.push(renderer)
    const bounds = new Box3().setFromObject(renderer.scene.children[0].instance)
    expect(bounds.min.x).toBeCloseTo(3 - 1.65 / 2)
    expect(bounds.max.x).toBeCloseTo(3 + 1.65 / 2)
    expect(bounds.min.z).toBeCloseTo(2 - 2.8 / 2)
    expect(bounds.max.z).toBeCloseTo(2 + 2.8 / 2)
    expect(bounds.min.y).toBeCloseTo(0)
    expect(bounds.max.y).toBeCloseTo(.88)
  })
})
