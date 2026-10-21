import { Component, lazy, Suspense, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Canvas, useThree } from '@react-three/fiber'
import { OrbitControls } from '@react-three/drei'
import { PerspectiveCamera, Vector3 } from 'three'
import { ASSET_CATALOG, dimensionsFor } from './asset-catalog'
import type { AssetCategory, AssetPlacement, FurnitureAsset } from './furniture-catalog'
import { FurnitureParts } from './FurnitureModel'
import './furniture-gallery.css'

const CatalogRoomPreview = lazy(() => import('./CatalogRoomPreview'))

type View = 'perspective' | 'front' | 'top'
type Display = 'model' | 'solids' | 'surfaces'

class PreviewBoundary extends Component<{ children: ReactNode; retry: () => void }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  render() {
    return this.state.failed ? <div className="viewer-message" role="alert">
      <h3>3D preview unavailable</h3>
      <p>Use a browser with WebGL enabled to inspect the models.</p>
      <button onClick={this.props.retry}>Retry preview</button>
    </div> : this.props.children
  }
}

function PreviewCamera({ asset, view, revision, placement }: { asset: FurnitureAsset; view: View; revision: number; placement: AssetPlacement }) {
  const { camera, invalidate, size: viewport } = useThree()
  const [width, height, depth] = asset.dimensions_m
  const size = Math.max(width, height, depth)
  useEffect(() => {
    const center = height / 2
    const direction = new Vector3(1.45, placement === 'ceiling' ? -.8 : 1, 1.8)
    if (view === 'top') direction.set(0, 1, .0001)
    else if (view === 'front') direction.set(0, 0, 1)
    direction.normalize()
    camera.up.set(0, 1, 0)
    const right = new Vector3().crossVectors(camera.up, direction).normalize()
    const up = new Vector3().crossVectors(direction, right)
    const tanV = Math.tan((camera instanceof PerspectiveCamera ? camera.fov : 36) * Math.PI / 360)
    const tanH = tanV * viewport.width / viewport.height
    let distance = 0
    // Fit the eight corners in both axes, including wide and portrait viewports.
    for (const x of [-width / 2, width / 2]) for (const y of [-height / 2, height / 2]) for (const z of [-depth / 2, depth / 2]) {
      const corner = new Vector3(x, y, z)
      distance = Math.max(distance, corner.dot(direction) + Math.max(Math.abs(corner.dot(right)) / tanH, Math.abs(corner.dot(up)) / tanV) * 1.3)
    }
    camera.position.copy(direction.multiplyScalar(distance)).add(new Vector3(0, center, 0))
    camera.lookAt(0, center, 0)
    camera.updateProjectionMatrix()
    invalidate()
  }, [camera, width, height, depth, view, revision, placement, asset.asset_id, viewport.width, viewport.height, invalidate])
  return <OrbitControls key={`${asset.asset_id}-${placement}-${view}-${revision}`} makeDefault target={[0, height / 2, 0]}
    minDistance={size * .65} maxDistance={size * 7} maxPolarAngle={Math.PI * .9} enablePan={false} />
}

function FurniturePreview({ asset, color, view, display, revision, placement }: {
  asset: FurnitureAsset; color: string; view: View; display: Display; revision: number; placement: AssetPlacement
}) {
  const [width, height, depth] = asset.dimensions_m
  const extent = Math.max(width, height, depth) * 2
  return <>
    <color attach="background" args={['#EEEDE6']} />
    <hemisphereLight args={['#FFFFFF', '#B8AB94', 2.2]} />
    <directionalLight position={[3, 5, 4]} intensity={2.8} castShadow
      shadow-mapSize={[1024, 1024]} shadow-camera-left={-extent} shadow-camera-right={extent}
      shadow-camera-top={extent} shadow-camera-bottom={-extent} shadow-normalBias={.025} />
    <directionalLight position={[-3, 2, -2]} intensity={1} />
    <group scale={[width, height, depth]}>
      <FurnitureParts assetId={asset.asset_id} color={color} collisionOnly={display === 'solids'} placement={placement} />
      {display === 'surfaces' && asset.surfaces.map(surface => <mesh key={surface.name}
        position={[(surface.x_min + surface.x_max) / 2, surface.y + .003, (surface.z_min + surface.z_max) / 2]}
        rotation={[-Math.PI / 2, 0, 0]} renderOrder={2}>
        <planeGeometry args={[surface.x_max - surface.x_min, surface.z_max - surface.z_min]} />
        <meshBasicMaterial color={surface.purpose === 'objects' ? '#3DAD9B' : '#E2AB52'} transparent opacity={.65} depthWrite={false} />
      </mesh>)}
    </group>
    {(placement === 'floor' || placement === 'surface') && <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -.008, 0]} receiveShadow>
      <planeGeometry args={[extent * 3, extent * 3]} />
      <shadowMaterial transparent opacity={.17} />
    </mesh>}
    <PreviewCamera asset={asset} view={view} revision={revision} placement={placement} />
  </>
}

const swatches = ['#B4A48F', '#80917E', '#947356', '#D3C8B6', '#596672', '#C48665', '#444744']
const categoryLabels: Record<AssetCategory | 'all', string> = {
  all: 'All objects', furniture: 'Core furniture', electronics: 'Electronics', lighting: 'Lighting', climate: 'Climate',
  kitchen: 'Kitchen', bathroom: 'Bathroom', utility: 'Utility', decor: 'Decorations & finishing',
}
const placementLabels: Record<AssetPlacement, string> = {
  floor: 'Floor-standing', surface: 'On a surface', wall: 'Wall-mounted', ceiling: 'Ceiling-mounted',
}

export default function FurnitureGallery() {
  const [assetId, setAssetId] = useState('sofa_standard_01')
  const asset = ASSET_CATALOG.find(item => item.asset_id === assetId)!
  const [color, setColor] = useState(asset.color)
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState<AssetCategory | 'all'>('all')
  const [selectedPlacement, setPlacement] = useState<AssetPlacement>('floor')
  const placement = asset.placements.includes(selectedPlacement) ? selectedPlacement : asset.placements[0]
  const dimensions = dimensionsFor(asset, placement)
  const previewAsset = { ...asset, dimensions_m: dimensions }
  const [view, setView] = useState<View>('perspective')
  const [display, setDisplay] = useState<Display>('model')
  const [attempt, setAttempt] = useState(0)
  const [cameraRevision, setCameraRevision] = useState(0)
  const [showExamples, setShowExamples] = useState(false)
  const [examplesAttempt, setExamplesAttempt] = useState(0)
  const filtered = ASSET_CATALOG.filter(item => (category === 'all' || item.category === category)
    && `${item.label} ${item.object_type}`.toLowerCase().includes(query.toLowerCase()))
  const types = new Set(ASSET_CATALOG.map(item => item.object_type)).size
  const choose = (next: FurnitureAsset) => {
    setAssetId(next.asset_id)
    setColor(next.color)
    setPlacement(next.placements[0])
    setView('perspective')
    setCameraRevision(current => current + 1)
  }
  const changeCategory = (next: AssetCategory | 'all') => {
    setCategory(next)
    if (next !== 'all' && asset.category !== next) {
      const first = ASSET_CATALOG.find(item => item.category === next)
      if (first) choose(first)
    }
  }

  return <><div className="furniture-gallery">
    <aside className="furniture-browser" aria-label="Object collection">
      <p className="eyebrow">The Roometry collection</p>
      <h2>Built for your room.</h2>
      <p className="furniture-count">{types} types · {ASSET_CATALOG.length} models</p>
      <label className="furniture-search">Collection
        <select value={category} onChange={event => changeCategory(event.target.value as AssetCategory | 'all')}>
          {Object.entries(categoryLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <label className="furniture-search">Find a model
        <input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Sofa, TV, lamp…" />
      </label>
      <ul className="furniture-options">
        {filtered.map(item => <li key={item.asset_id}>
          <button type="button" aria-pressed={item.asset_id === assetId} onClick={() => choose(item)}>
            <span className="furniture-swatch" style={{ background: item.color }} aria-hidden="true" />
            <span><strong>{item.label}</strong><small>{item.dimensions_m[0]} × {item.dimensions_m[2]} × {item.dimensions_m[1]} m</small></span>
          </button>
        </li>)}
      </ul>
      {!filtered.length && <p role="status">No matching objects.</p>}
    </aside>
    <div className="furniture-stage">
      <div className="furniture-stage-heading">
        <div><p className="eyebrow">Model preview</p><h2>{asset.label}</h2></div>
        <span>{placementLabels[placement]}</span>
      </div>
      <div className="furniture-preview-toolbar">
        <div role="group" aria-label="Model camera">
          {(['perspective', 'front', 'top'] as const).map(value => <button key={value} type="button"
            aria-pressed={view === value} onClick={() => { setView(value); setCameraRevision(current => current + 1) }}>
            {value === 'perspective' ? 'Perspective' : value === 'front' ? 'Front' : 'Top'}
          </button>)}
        </div>
        {asset.placements.length > 1 && <label>Placement <select value={placement}
          onChange={event => { setPlacement(event.target.value as AssetPlacement); setCameraRevision(current => current + 1) }}>
          {asset.placements.map(value => <option key={value} value={value}>{placementLabels[value]}</option>)}
        </select></label>}
        <label>Show <select value={display} onChange={event => setDisplay(event.target.value as Display)}>
          <option value="model">Finished model</option><option value="solids">Solid parts</option><option value="surfaces">Support surfaces</option>
        </select></label>
      </div>
      <div className="furniture-preview-canvas" aria-label={`3D preview of ${asset.label}`}>
        <PreviewBoundary key={attempt} retry={() => setAttempt(current => current + 1)}>
          <Canvas key={attempt} shadows frameloop="demand" dpr={[1, 1.75]} camera={{ fov: 36, near: .01, far: 100 }}
            fallback={<div className="viewer-message">WebGL is required for the model preview.</div>}>
            <FurniturePreview asset={previewAsset} color={color} view={view} display={display} revision={cameraRevision} placement={placement} />
          </Canvas>
        </PreviewBoundary>
      </div>
      <div className="furniture-finish">
        <div><strong>Make it yours</strong><p>Change the main finish. Frame and hardware keep their material contrast.</p></div>
        <div className="furniture-swatches" role="group" aria-label="Model finish">
          {swatches.map(value => <button key={value} type="button" aria-label={`Use ${value} finish`} aria-pressed={color === value}
            style={{ background: value }} onClick={() => setColor(value)} />)}
          <label className="furniture-custom-color">Custom color<input aria-label="Custom model color" type="color" value={color} onChange={event => setColor(event.target.value.toUpperCase())} /></label>
        </div>
      </div>
      <dl className="furniture-specs">
        <div><dt>Width</dt><dd>{dimensions[0]} m</dd></div>
        <div><dt>Depth</dt><dd>{dimensions[2]} m</dd></div>
        <div><dt>Height</dt><dd>{dimensions[1]} m</dd></div>
        <div><dt>Supports</dt><dd>{asset.surfaces.filter(surface => surface.purpose === 'objects').length} object surfaces</dd></div>
      </dl>
      <p className="furniture-preview-note">Drag to orbit · Scroll to zoom · Preview sizes are examples; room objects use their own dimensions.</p>
      {display === 'surfaces' && <p className="furniture-preview-note">Green: objects · Amber: seating or sleeping. Inner shelves have separate surfaces.</p>}
      {display === 'solids' && <p className="furniture-preview-note">Collision checks approximate these solid parts with conservative boxes. Curved parts and hollow interiors remain approximate.</p>}
    </div>
  </div>
    {(category === 'all' || category === 'kitchen' || category === 'bathroom' || category === 'utility' || category === 'decor') && <section className="catalog-room-examples" aria-label="Catalog room examples">
      <button className="catalog-examples-toggle" type="button" aria-expanded={showExamples} aria-controls="catalog-room-examples"
        onClick={() => setShowExamples(value => !value)}>{showExamples ? 'Hide room examples' : 'View room examples'}</button>
      {showExamples && <div id="catalog-room-examples"><PreviewBoundary key={examplesAttempt} retry={() => setExamplesAttempt(value => value + 1)}>
        <Suspense fallback={<p role="status">Loading room examples…</p>}><CatalogRoomPreview /></Suspense>
      </PreviewBoundary></div>}
    </section>}
  </>
}
