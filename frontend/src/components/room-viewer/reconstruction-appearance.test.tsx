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

  it('keeps blue gaming-chair panels distinct from a black back and frame', async () => {
    const r = await ReactThreeTestRenderer.create(<FurnitureParts assetId="chair_office_01" color="#28558A" materialColors={{ inset: '#111111', metal: '#17191B' }} />)
    mounted.push(r)
    const root = r.scene.children[0].instance
    const back = root.getObjectByName('high_back') as Mesh
    const panel = root.getObjectByName('back_color_1') as Mesh
    const seat = root.getObjectByName('seat') as Mesh
    expect((back.material as MeshStandardMaterial).color.getHexString()).toBe('111111')
    expect((panel.material as MeshStandardMaterial).color.getHexString()).toBe('28558a')
    expect((seat.material as MeshStandardMaterial).color.getHexString()).toBe('28558a')
  })

  it('renders an integrated desk as one frame with distinct worktop, shelves and metal', async () => {
    const r = await ReactThreeTestRenderer.create(<FurnitureParts assetId="desk_shelved_01" color="#8A6038" materialColors={{ wood: '#AA8254', metal: '#17191B' }} />)
    mounted.push(r)
    const root = r.scene.children[0].instance
    const worktop = root.getObjectByName('worktop') as Mesh
    const board = root.getObjectByName('side_board_0') as Mesh
    const upright = root.getObjectByName('upright_0') as Mesh
    expect((worktop.material as MeshStandardMaterial).color.getHexString()).toBe('8a6038')
    expect((board.material as MeshStandardMaterial).color.getHexString()).toBe('aa8254')
    expect((upright.material as MeshStandardMaterial).color.getHexString()).toBe('17191b')
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
