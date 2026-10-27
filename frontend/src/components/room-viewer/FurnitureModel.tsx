import { useEffect, useMemo } from 'react'
import { Color, DoubleSide, ExtrudeGeometry, LatheGeometry, Shape, Vector2 } from 'three'
import type { Texture, Side } from 'three'
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js'
import type { ModelProps } from './ObjectModels'
import { getCatalogAsset } from './asset-catalog'
import type { AssetPlacement, FurnitureMaterial, FurniturePart } from './furniture-catalog'
import roleColors from '../../data/material-colors.generated.json'
import { useSurfaceTextures } from './SurfaceTextures'

interface Finish {
  color: string; roughness: number; metalness: number
  emissive?: string; emissiveIntensity?: number
  transparent?: boolean; opacity?: number; depthWrite?: boolean
  clearcoat?: number; clearcoatRoughness?: number; envMapIntensity?: number
}

export function palette(color: string, overrides?: ModelProps['materialColors']): Record<FurnitureMaterial, Finish> {
  const result: Record<FurnitureMaterial, Finish> = {
    primary: { color, roughness: .78, metalness: 0 },
    primary_dark: { color: `#${new Color(color).multiplyScalar(.7).getHexString()}`, roughness: .88, metalness: 0 },
    primary_metal: { color, roughness: .35, metalness: .55 },
    wood: { color: roleColors.wood, roughness: .58, metalness: 0, envMapIntensity: .45 },
    metal: { color: roleColors.metal, roughness: .35, metalness: .65 },
    linen: { color: roleColors.linen, roughness: .95, metalness: 0 },
    inset: { color: roleColors.inset, roughness: .85, metalness: 0 },
    screen: { color: roleColors.screen, roughness: .2, metalness: .25 },
    rubber: { color: roleColors.rubber, roughness: .95, metalness: 0 },
    light: { color: roleColors.light, roughness: .55, metalness: 0, emissive: '#FFD9A3', emissiveIntensity: .65 },
    indicator: { color: roleColors.indicator, roughness: .3, metalness: 0, emissive: roleColors.indicator, emissiveIntensity: .6 },
    glass: { color: roleColors.glass, roughness: .12, metalness: 0, transparent: true, opacity: .25, depthWrite: false, clearcoat: 1, clearcoatRoughness: .08 },
    primary_ceramic: { color, roughness: .2, metalness: 0, clearcoat: .35, clearcoatRoughness: .16 },
    chrome: { color: roleColors.chrome, roughness: .16, metalness: .92 },
    enamel: { color: roleColors.enamel, roughness: .23, metalness: 0, clearcoat: .3, clearcoatRoughness: .18 },
    clear_glass: { color: roleColors.clear_glass, roughness: .08, metalness: 0, transparent: true, opacity: .18, depthWrite: false, clearcoat: 1, clearcoatRoughness: .06 },
    foliage: { color: roleColors.foliage, roughness: .85, metalness: 0 },
    foliage_dark: { color: roleColors.foliage_dark, roughness: .9, metalness: 0 },
    soil: { color: roleColors.soil, roughness: 1, metalness: 0 },
    paper: { color: roleColors.paper, roughness: .9, metalness: 0 },
    mirror: { color: roleColors.mirror, roughness: .06, metalness: 1, envMapIntensity: 1.1 },
  }
  if (overrides) {
    for (const key of ['wood', 'metal', 'linen', 'inset', 'rubber', 'enamel'] as const) {
      const value = overrides[key]
      if (value) result[key] = { ...result[key], color: value }
    }
  }
  return result
}

function PartMaterial({ finish, bump, flat, side }: { finish: Finish; bump?: Texture; flat?: boolean; side?: Side }) {
  const props = { ...finish, bumpMap: bump, bumpScale: .004, envMapIntensity: finish.envMapIntensity ?? .8,
    flatShading: flat, side }
  // Glass uses a cheap reflective clearcoat rather than a screen-space
  // transmission pass for every shelf or shower pane in a large room.
  return finish.clearcoat !== undefined ? <meshPhysicalMaterial {...props} /> : <meshStandardMaterial {...props} />
}

function RoundedGeometry({ part }: { part: FurniturePart }) {
  // Primitive objects need explicit cleanup. Recoloring reuses the geometry.
  const geometry = useMemo(() => new RoundedBoxGeometry(...part.size, 2, part.radius), [part])
  useEffect(() => () => geometry.dispose(), [geometry])
  return <primitive object={geometry} attach="geometry" />
}

function PartGeometry({ part }: { part: FurniturePart }) {
  if (part.shape === 'rounded_box') return <RoundedGeometry part={part} />
  if (part.shape === 'box') return <boxGeometry args={[...part.size]} />
  if (part.shape === 'ellipsoid') return <sphereGeometry args={[.5, 24, 16]} />
  if (part.shape === 'torus') return <torusGeometry args={[.5 - part.radius, part.radius, 8, 40]} />
  if (part.shape === 'basin') return <BasinGeometry part={part} />
  if (part.shape === 'pleated_panel') return <PleatedGeometry />
  if (part.shape === 'arched_panel') return <ArchedGeometry />
  if (part.shape === 'frustum') return <cylinderGeometry args={[Math.SQRT1_2 * part.top_scale, Math.SQRT1_2 * part.bottom_scale, 1, 4, 1, false, Math.PI / 4]} />
  return <cylinderGeometry args={[.5 * part.top_scale, .5 * part.bottom_scale, 1, 20, 1, part.shape === 'tube']} />
}

function BasinGeometry({ part }: { part: FurniturePart }) {
  const geometry = useMemo(() => new LatheGeometry(part.profile.map(([radius, y]) => new Vector2(radius, y - .5)), 48), [part])
  useEffect(() => () => geometry.dispose(), [geometry])
  return <primitive object={geometry} attach="geometry" />
}

function PleatedGeometry() {
  const geometry = useMemo(() => {
    // A closed wavy cross-section gives the fabric real folds on both sides.
    const shape = new Shape()
    for (let i = 0; i <= 64; i++) {
      const x = i / 64 - .5
      const z = Math.sin(i / 64 * Math.PI * 8) * .46 + .02
      if (i === 0) shape.moveTo(x, z)
      else shape.lineTo(x, z)
    }
    for (let i = 64; i >= 0; i--) shape.lineTo(i / 64 - .5, Math.sin(i / 64 * Math.PI * 8) * .46 - .02)
    shape.closePath()
    const result = new ExtrudeGeometry(shape, { depth: 1, steps: 1, bevelEnabled: false })
    result.rotateX(-Math.PI / 2)
    result.translate(0, -.5, 0)
    return result
  }, [])
  useEffect(() => () => geometry.dispose(), [geometry])
  return <primitive object={geometry} attach="geometry" />
}

function ArchedGeometry() {
  const geometry = useMemo(() => {
    const shape = new Shape()
    shape.moveTo(-.5, -.5)
    shape.lineTo(.5, -.5)
    shape.lineTo(.5, .3)
    shape.bezierCurveTo(.5, .41046, .27614, .5, 0, .5)
    shape.bezierCurveTo(-.27614, .5, -.5, .41046, -.5, .3)
    shape.closePath()
    const result = new ExtrudeGeometry(shape, { depth: 1, steps: 1, bevelEnabled: false, curveSegments: 24 })
    result.translate(0, 0, -.5)
    return result
  }, [])
  useEffect(() => () => geometry.dispose(), [geometry])
  return <primitive object={geometry} attach="geometry" />
}

export function FurnitureParts({ assetId, color, materialColors, collisionOnly = false, placement }: {
  assetId: string; color: string; materialColors?: ModelProps['materialColors']; collisionOnly?: boolean; placement?: AssetPlacement
}) {
  const asset = getCatalogAsset(assetId)
  const maps = useSurfaceTextures()
  const materials = useMemo(() => palette(color, materialColors), [color, materialColors])
  if (!asset) return null
  const activePlacement = placement ?? asset.placements[0]

  return <group name="furniture_model">
    {asset.parts.filter(part => (!collisionOnly || part.collision)
      && (!part.placements?.length || part.placements.includes(activePlacement))).map(part => {
      const scaled = !['box', 'rounded_box'].includes(part.shape)
      const scale: [number, number, number] | undefined = !scaled ? undefined
        : part.shape === 'torus' ? [part.size[0], part.size[1], part.size[2] / (2 * part.radius)] : [...part.size]
      const fabric = part.material === 'linen' || (part.material === 'primary' && ['sofa', 'armchair', 'ottoman', 'bed'].includes(asset.object_type))
      const bump = fabric ? maps?.fabric : part.material === 'wood' ? maps?.wood : undefined
      return <group key={part.name} position={[...part.at]} rotation={part.rotation ? [...part.rotation] : undefined} scale={scale}>
        <mesh name={part.name} castShadow={part.material !== 'clear_glass' && part.material !== 'glass'} receiveShadow userData={{ collision: part.collision, shape: part.shape }}>
          <PartGeometry part={part} />
          {collisionOnly
            ? <meshBasicMaterial color="#42796F" wireframe />
            : <PartMaterial finish={materials[part.material]} bump={bump} flat={part.shape === 'frustum'} side={part.shape === 'tube' || part.material === 'clear_glass' || part.material === 'glass' ? DoubleSide : undefined} />}
        </mesh>
        {part.shape === 'tube' && !collisionOnly && <>
          <mesh position={[0, .5, 0]} rotation={[-Math.PI / 2, 0, 0]}>
            <ringGeometry args={[.5 * part.top_scale - part.wall_thickness, .5 * part.top_scale, 20]} />
            <PartMaterial finish={materials[part.material]} bump={bump} side={DoubleSide} />
          </mesh>
          <mesh>
            <cylinderGeometry args={[.5 * part.top_scale - part.wall_thickness, .5 * part.bottom_scale - part.wall_thickness, 1, 20, 1, true]} />
            <PartMaterial finish={materials[part.material]} bump={bump} side={DoubleSide} />
          </mesh>
        </>}
      </group>
    })}
  </group>
}

export default function FurnitureModel({ assetId, color, materialColors, placement }: ModelProps) {
  return <FurnitureParts assetId={assetId} color={color} materialColors={materialColors} placement={placement} />
}
