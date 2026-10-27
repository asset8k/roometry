import { createContext, useContext, useEffect, useMemo } from 'react'
import type { ReactNode } from 'react'
import { DataTexture, LinearMipmapLinearFilter, RepeatWrapping, RGBAFormat, SRGBColorSpace } from 'three'

// Neutral maps modulate the observed color, rather than replacing it with a
// stock wood/stone image. All maps are local, deterministic and shared per viewer.
function noise(x: number, y: number, seed = 0) {
  const n = Math.sin(x * 127.1 + y * 311.7 + seed * 74.7) * 43758.5453
  return n - Math.floor(n)
}

export function grayTexture(size: number, sample: (u: number, v: number, x: number, y: number) => number, color = false) {
  const data = new Uint8Array(size * size * 4)
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    const value = Math.round(Math.min(1, Math.max(0, sample(x / size, y / size, x, y))) * 255)
    const i = (y * size + x) * 4
    data[i] = data[i + 1] = data[i + 2] = value; data[i + 3] = 255
  }
  const texture = new DataTexture(data, size, size, RGBAFormat)
  texture.wrapS = texture.wrapT = RepeatWrapping
  texture.generateMipmaps = true
  texture.minFilter = LinearMipmapLinearFilter
  if (color) texture.colorSpace = SRGBColorSpace
  texture.needsUpdate = true
  return texture
}

function plank(u: number, v: number) {
  const row = Math.floor(u * 12)
  const across = u * 12 % 1
  const along = (v * 2 + noise(row, 0) * 2) % 1
  const edge = Math.min(across, 1 - across, along * 6, (1 - along) * 6)
  const grain = Math.sin(u * 1800 + Math.sin(v * Math.PI * 8 + row) * 4) * .009
  return edge < .012 ? .77 : .94 + noise(row, Math.floor(v * 2 + noise(row, 0) * 2) % 2, 3) * .05 + grain
}

function tile(u: number, v: number) {
  const x = u * 4 % 1, y = v * 4 % 1
  return Math.min(x, 1 - x, y, 1 - y) < .008 ? .82 : .985
}

export function createSurfaceTextures() {
  const maps = {
    wood: grayTexture(128, (u, v, x, y) => .5 + Math.sin(u * 170 + Math.sin(v * 12) * 2) * .08 + (noise(x, y) - .5) * .04),
    fabric: grayTexture(128, (_u, _v, x, y) => .5 + ((x % 4 < 2) === (y % 4 < 2) ? .09 : -.09) + (noise(x, y, 2) - .5) * .04),
    plaster: grayTexture(128, (_u, _v, x, y) => .5 + (noise(x, y, 4) - .5) * .16),
    floorWood: grayTexture(512, plank, true),
    floorTile: grayTexture(256, tile, true),
  }
  maps.fabric.repeat.set(4, 4)
  return maps
}

type SurfaceTextures = ReturnType<typeof createSurfaceTextures>
const TextureContext = createContext<SurfaceTextures | null>(null)

export function SurfaceTextureProvider({ children }: { children: ReactNode }) {
  const maps = useMemo(createSurfaceTextures, [])
  useEffect(() => () => Object.values(maps).forEach(texture => texture.dispose()), [maps])
  return <TextureContext.Provider value={maps}>{children}</TextureContext.Provider>
}

export function useSurfaceTextures() { return useContext(TextureContext) }

export function useFloorTexture(pattern: 'wood' | 'tile' | 'plain', width: number, length: number) {
  const maps = useSurfaceTextures()
  const texture = useMemo(() => {
    const source = pattern === 'wood' ? maps?.floorWood : pattern === 'tile' ? maps?.floorTile : null
    if (!source) return null
    const copy = source.clone()
    copy.repeat.set(width / 2.4, length / 2.4)
    copy.needsUpdate = true
    return copy
  }, [maps, pattern, width, length])
  useEffect(() => () => texture?.dispose(), [texture])
  return texture
}
