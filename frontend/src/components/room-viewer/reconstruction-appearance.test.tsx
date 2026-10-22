import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import ReactThreeTestRenderer from '@react-three/test-renderer'
import { Mesh, MeshStandardMaterial } from 'three'
import { FurnitureParts, palette } from './FurnitureModel'
import RoomShell from './RoomShell'
import { loadExampleScene } from '../../lib/test-support/scenes'

const mounted: Awaited<ReturnType<typeof ReactThreeTestRenderer.create>>[] = []
beforeAll(() => vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true))
afterAll(() => vi.unstubAllGlobals())
afterEach(async () => { for (const r of mounted.splice(0)) await r.unmount() })

describe('photo appearance', () => {
  it('changes material roles independently while preserving the primary object color', () => {
    const materials = palette('#28558A', { metal: '#17191B', wood: '#AA8254', linen: '#252525' })
    expect(materials.primary.color).toBe('#28558A')
    expect(materials.metal.color).toBe('#17191B')
    expect(materials.wood.color).toBe('#AA8254')
    expect(materials.linen.color).toBe('#252525')
    expect(materials.metal.metalness).toBe(.65)
  })

  it('renders black uprights and wood shelves using photo overrides', async () => {
    const r = await ReactThreeTestRenderer.create(<FurnitureParts assetId="shelf_metal_01" color="#242529" materialColors={{ metal: '#17191B', wood: '#AA8254' }} />)
    mounted.push(r)
    const colors = new Set<string>()
    r.scene.children[0].instance.traverse(node => { if (node instanceof Mesh && node.material instanceof MeshStandardMaterial) colors.add(node.material.color.getHexString()) })
    expect(colors.has('17191b')).toBe(true)
    expect(colors.has('aa8254')).toBe(true)
  })

  it('uses observed floor and wall colors without wood seams on a plain floor', async () => {
    const scene = loadExampleScene()
    scene.openings = []
    scene.finishes = { floor_color: '#B5B3AE', wall_color: '#999B9C', floor_pattern: 'plain' }
    const r = await ReactThreeTestRenderer.create(<RoomShell scene={scene} />)
    mounted.push(r)
    const root = r.scene.children[0].instance
    const floor = root.getObjectByName('room_floor') as Mesh
    expect((floor.material as MeshStandardMaterial).color.getHexString()).toBe('b5b3ae')
    const wall = root.getObjectByName('wall_north') as Mesh
    expect((wall.material as MeshStandardMaterial).color.getHexString()).toBe('999b9c')
    expect(root.children).toHaveLength(5) // Floor plus four walls, no plank seams.
  })
})
