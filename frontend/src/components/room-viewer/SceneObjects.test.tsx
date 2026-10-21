import type { ReactNode } from 'react'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import ReactThreeTestRenderer from '@react-three/test-renderer'
import { Box3, Mesh, MeshStandardMaterial, Vector3 } from 'three'
import type { Object3D } from 'three'
import { loadExampleScene } from '../../lib/test-support/scenes'
import type { SceneObject } from '../../lib/scene.generated'
import { getModelKind } from './model-registry'
import SceneObjects, { SceneObjectView } from './SceneObjects'

type Renderer = Awaited<ReturnType<typeof ReactThreeTestRenderer.create>>
const renderers: Renderer[] = []
beforeAll(() => vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true))
afterAll(() => vi.unstubAllGlobals())
afterEach(async () => {
  for (const renderer of renderers.splice(0)) await renderer.unmount()
})

async function mount(element: ReactNode, options?: Parameters<typeof ReactThreeTestRenderer.create>[1]) {
  const renderer = await ReactThreeTestRenderer.create(element, options)
  renderers.push(renderer)
  return renderer
}

function materialColors(root: Object3D): Set<string> {
  const colors = new Set<string>()
  root.traverse(node => {
    if (!(node instanceof Mesh)) return
    const materials = Array.isArray(node.material) ? node.material : [node.material]
    for (const material of materials) {
      if (material instanceof MeshStandardMaterial) colors.add(`#${material.color.getHexString().toUpperCase()}`)
    }
  })
  return colors
}

describe('procedural object models', () => {
  it('selects protected objects, highlights bounds, and ignores orbit drags', async () => {
    const onSelect = vi.fn()
    const stopPropagation = vi.fn()
    const renderer = await mount(<SceneObjects scene={loadExampleScene()} selectedId="bed_1" onSelect={onSelect} />)
    const bed = renderer.scene.findByProps({ name: 'bed_1' })
    expect(bed.instance.getObjectByName('selection_bounds')).toBeDefined()
    await renderer.fireEvent(bed, 'click', { delta: 0, stopPropagation })
    expect(onSelect).toHaveBeenCalledWith('bed_1')
    await renderer.fireEvent(bed, 'click', { delta: 12, stopPropagation })
    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(stopPropagation).toHaveBeenCalledTimes(2)
  })

  it.each(loadExampleScene().objects)('$id stays inside its declared local bounds and uses its primary color', async object => {
    const renderer = await mount(
      <SceneObjectView object={{ ...object, position: { x: 0, y: 0, z: 0 }, rotation_y_rad: 0, dimensions_m: { width: 1, height: 1, depth: 1 } }} />,
    )
    const root = renderer.scene.children[0].instance
    const bounds = new Box3().setFromObject(root)
    expect(bounds.isEmpty()).toBe(false)
    expect(bounds.min.x).toBeGreaterThanOrEqual(-0.500001)
    expect(bounds.min.y).toBeGreaterThanOrEqual(-0.000001)
    expect(bounds.min.z).toBeGreaterThanOrEqual(-0.500001)
    expect(bounds.max.x).toBeLessThanOrEqual(0.500001)
    expect(bounds.max.y).toBeLessThanOrEqual(1.000001)
    expect(bounds.max.z).toBeLessThanOrEqual(0.500001)
    expect(materialColors(root)).toContain(object.color)
    expect(getModelKind(object.asset_id)).not.toBe('placeholder')
  })

  it('renders all fixture instances without moving their source coordinates', async () => {
    const source = loadExampleScene()
    const before = structuredClone(source)
    const renderer = await mount(<SceneObjects scene={source} />)
    const root = renderer.scene.children[0].instance
    expect(root.children.map(child => child.name)).toEqual(source.objects.map(object => object.id))
    const computer = root.getObjectByName('computer_1')
    const chandelier = root.getObjectByName('chandelier_1')
    expect(computer).toBeDefined()
    expect(chandelier).toBeDefined()
    expect(new Box3().setFromObject(computer!).min.y).toBeCloseTo(0.75)
    expect(new Box3().setFromObject(chandelier!).max.y).toBeCloseTo(2.6)
    expect(source).toEqual(before)
  })

  it('applies dimensions and a quarter turn to an unknown asset fallback', async () => {
    const object = {
      ...loadExampleScene().objects[0], id: 'plant_1', type: 'plant' as const,
      asset_id: 'unknown_asset' as SceneObject['asset_id'],
      position: { x: 3, y: 0, z: 2 }, rotation_y_rad: Math.PI / 2,
      dimensions_m: { width: 2, height: 0.7, depth: 1 },
    }
    const renderer = await mount(<SceneObjectView object={object} />)
    const root = renderer.scene.children[0].instance
    const bounds = new Box3().setFromObject(root)
    expect(root.userData.modelKind).toBe('placeholder')
    expect(bounds.min.toArray()).toEqual([2.5, 0, 1])
    expect(bounds.max.toArray()).toEqual([3.5, 0.7, 3])
  })

  it('keeps the wall TV front facing into the room', async () => {
    const object = loadExampleScene().objects.find(item => item.id === 'tv_1')!
    const renderer = await mount(<SceneObjectView object={object} />)
    const root = renderer.scene.children[0].instance
    const front = new Vector3(0, 0, 1).applyQuaternion(root.quaternion)
    expect(front.x).toBeCloseTo(0)
    expect(front.z).toBeCloseTo(-1)
  })

  it.each([
    [[2, 2, 6] as [number, number, number], false],
    [[2, 2, -4] as [number, number, number], true],
  ])('makes the wall TV follow the cutaway at camera %s', async (position, visible) => {
    const renderer = await mount(<SceneObjects scene={loadExampleScene()} />, { camera: { position } })
    await renderer.advanceFrames(1, 1 / 60)
    expect(renderer.scene.children[0].instance.getObjectByName('tv_1')?.visible).toBe(visible)
  })

  it('updates a model color without changing its geometry or source data', async () => {
    const object = loadExampleScene().objects.find(item => item.id === 'rug_1')!
    const renderer = await mount(<SceneObjectView object={object} />)
    const before = new Box3().setFromObject(renderer.scene.children[0].instance)
    await renderer.update(<SceneObjectView object={{ ...object, color: '#327E57' }} />)
    const root = renderer.scene.children[0].instance
    expect(materialColors(root)).toContain('#327E57')
    expect(materialColors(root)).not.toContain('#008000')
    expect(new Box3().setFromObject(root).equals(before)).toBe(true)
    expect(object.color).toBe('#008000')
  })

  it('reuses a table family and falls back safely for missing models', () => {
    expect(getModelKind('desk_standard_01')).toBe('furniture')
    expect(getModelKind('coffee_table_standard_01')).toBe('furniture')
    expect(getModelKind('wardrobe_standard_01')).toBe('furniture')
    expect(getModelKind('unknown_asset')).toBe('placeholder')
    expect(getModelKind('__proto__')).toBe('placeholder')
  })
})
