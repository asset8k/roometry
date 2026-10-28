import { afterEach, beforeAll, afterAll, describe, expect, it, vi } from 'vitest'
import ReactThreeTestRenderer from '@react-three/test-renderer'
import { Mesh, Raycaster, Vector3 } from 'three'
import { FurnitureParts } from './FurnitureModel'
import { getCatalogAsset } from './asset-catalog'
import type { AssetPlacement } from './furniture-catalog'

type Renderer = Awaited<ReturnType<typeof ReactThreeTestRenderer.create>>
const mounted: Renderer[] = []
beforeAll(() => vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true))
afterAll(() => vi.unstubAllGlobals())
afterEach(async () => { for (const renderer of mounted.splice(0)) await renderer.unmount() })

async function model(id: string, placement: AssetPlacement) {
  const renderer = await ReactThreeTestRenderer.create(<FurnitureParts assetId={id} color="#8a9c85" placement={placement} />)
  mounted.push(renderer)
  const root = renderer.scene.children[0].instance
  root.updateMatrixWorld(true)
  return root
}

describe('refined models preserve functional geometry', () => {
  it.each(['plant_standard_01', 'plant_monstera_01', 'plant_trailing_surface_01', 'plant_trailing_wall_01'])(
    '%s has visible soil inside an open planter, rather than a solid cap', async id => {
      const asset = getCatalogAsset(id)!
      const placement = asset.placements[0]
      const root = await model(id, placement)
      const pot = asset.parts.find(p => (p.name === 'pot' || p.name === `${placement}_pot`))!
      const meshes: Mesh[] = []
      root.traverse(node => { if (node instanceof Mesh && /(?:pot|soil)/.test(node.name)) meshes.push(node) })
      const ray = new Raycaster(new Vector3(.08, pot.at[1] + pot.size[1] / 2 + .01, pot.at[2] + .05), new Vector3(0, -1, 0))
      expect(ray.intersectObjects(meshes, false)[0]?.object.name).toMatch(/soil$/)
    })

  it.each(['sofa_standard_01', 'sofa_sectional_left_01', 'sofa_modular_corner_right_01', 'armchair_barrel_01'])(
    '%s keeps the declared seating plane on the rendered cushion', async id => {
      const asset = getCatalogAsset(id)!
      const root = await model(id, 'floor')
      for (const support of asset.surfaces.filter(s => s.purpose === 'seating')) {
        const ray = new Raycaster(new Vector3((support.x_min + support.x_max) / 2, support.y + .02,
          (support.z_min + support.z_max) / 2), new Vector3(0, -1, 0), 0, .04)
        const hit = ray.intersectObject(root, true)[0]
        expect(hit?.object.name).toBe(support.part)
        expect(hit?.point.y).toBeCloseTo(support.y, 5)
      }
    })

  it.each(['kitchen_counter_sink_01', 'vanity_inset_single_01'])(
    '%s leaves its bowl open beneath the worktop', async id => {
      const root = await model(id, 'floor')
      const ray = new Raycaster(new Vector3(0, .76, 0), new Vector3(0, -1, 0))
      const hit = ray.intersectObject(root, true)[0]
      // The drain is below the deck, with no invisible support or solid top over it.
      expect(hit?.object.name).toBe('drain')
      expect(hit?.point.y).toBeLessThan(.6)
      const asset = getCatalogAsset(id)!
      expect(asset.surfaces.some(s => s.x_min < 0 && s.x_max > 0 && s.z_min < 0 && s.z_max > 0)).toBe(false)
    })
})
