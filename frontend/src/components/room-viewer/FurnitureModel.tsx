import { useEffect, useMemo } from 'react'
import { Color, DoubleSide, ExtrudeGeometry, LatheGeometry, Shape, Vector2 } from 'three'
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js'
import type { ModelProps } from './ObjectModels'
import { getCatalogAsset } from './asset-catalog'
import type { AssetPlacement, FurnitureMaterial, FurniturePart } from './furniture-catalog'

interface Finish {
  color: string; roughness: number; metalness: number
  emissive?: string; emissiveIntensity?: number
  transparent?: boolean; opacity?: number; depthWrite?: boolean
}

export function palette(color: string, overrides?: ModelProps['materialColors']): Record<FurnitureMaterial, Finish> {
  const result: Record<FurnitureMaterial, Finish> = {
    primary: { color, roughness: .78, metalness: 0 },
    primary_dark: { color: `#${new Color(color).multiplyScalar(.7).getHexString()}`, roughness: .88, metalness: 0 },
    primary_metal: { color, roughness: .35, metalness: .55 },
    wood: { color: '#70543D', roughness: .68, metalness: 0 },
    metal: { color: '#4F514D', roughness: .35, metalness: .65 },
    linen: { color: '#E8E2D6', roughness: .95, metalness: 0 },
    inset: { color: '#383D38', roughness: .85, metalness: 0 },
    screen: { color: '#15272F', roughness: .2, metalness: .25 },
    rubber: { color: '#252A28', roughness: .95, metalness: 0 },
    light: { color: '#FFF0CB', roughness: .55, metalness: 0, emissive: '#FFD9A3', emissiveIntensity: .65 },
    indicator: { color: '#7CBEBC', roughness: .3, metalness: 0, emissive: '#7CBEBC', emissiveIntensity: .6 },
    glass: { color: '#E3D3B3', roughness: .2, metalness: 0, transparent: true, opacity: .27, depthWrite: false },
    primary_ceramic: { color, roughness: .22, metalness: 0 },
    chrome: { color: '#AEB7B4', roughness: .22, metalness: .8 },
    enamel: { color: '#E3E4DC', roughness: .25, metalness: 0 },
    clear_glass: { color: '#CADDDC', roughness: .16, metalness: .05, transparent: true, opacity: .18, depthWrite: false },
    foliage: { color: '#567344', roughness: .85, metalness: 0 },
    foliage_dark: { color: '#354D32', roughness: .9, metalness: 0 },
    soil: { color: '#44372A', roughness: 1, metalness: 0 },
    paper: { color: '#F0ECDD', roughness: .9, metalness: 0 },
    mirror: { color: '#B7CCCE', roughness: .08, metalness: .25 },
  }
  if (overrides) {
    for (const key of ['wood', 'metal', 'linen', 'inset', 'rubber', 'enamel'] as const) {
      const value = overrides[key]
      if (value) result[key] = { ...result[key], color: value }
    }
  }
  return result
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
  const materials = useMemo(() => palette(color, materialColors), [color, materialColors])
  if (!asset) return null
  const activePlacement = placement ?? asset.placements[0]

  return <group name="furniture_model">
    {asset.parts.filter(part => (!collisionOnly || part.collision)
      && (!part.placements?.length || part.placements.includes(activePlacement))).map(part => {
      const scaled = !['box', 'rounded_box'].includes(part.shape)
      const scale: [number, number, number] | undefined = !scaled ? undefined
        : part.shape === 'torus' ? [part.size[0], part.size[1], part.size[2] / (2 * part.radius)] : [...part.size]
      return <group key={part.name} position={[...part.at]} rotation={part.rotation ? [...part.rotation] : undefined} scale={scale}>
        <mesh name={part.name} castShadow={part.material !== 'clear_glass' && part.material !== 'glass'} receiveShadow userData={{ collision: part.collision, shape: part.shape }}>
          <PartGeometry part={part} />
          {collisionOnly
            ? <meshBasicMaterial color="#42796F" wireframe />
            : <meshStandardMaterial {...materials[part.material]} flatShading={part.shape === 'frustum'} side={part.shape === 'tube' || part.material === 'clear_glass' ? DoubleSide : undefined} />}
        </mesh>
        {part.shape === 'tube' && !collisionOnly && <>
          <mesh position={[0, .5, 0]} rotation={[-Math.PI / 2, 0, 0]}>
            <ringGeometry args={[.5 * part.top_scale - part.wall_thickness, .5 * part.top_scale, 20]} />
            <meshStandardMaterial {...materials[part.material]} side={DoubleSide} />
          </mesh>
          <mesh>
            <cylinderGeometry args={[.5 * part.top_scale - part.wall_thickness, .5 * part.bottom_scale - part.wall_thickness, 1, 20, 1, true]} />
            <meshStandardMaterial {...materials[part.material]} side={DoubleSide} />
          </mesh>
        </>}
      </group>
    })}
  </group>
}

export default function FurnitureModel({ assetId, color, materialColors, placement }: ModelProps) {
  return <FurnitureParts assetId={assetId} color={color} materialColors={materialColors} placement={placement} />
}
