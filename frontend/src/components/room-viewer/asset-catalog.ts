import equipment from '../../data/equipment.generated.json'
import fixtures from '../../data/fixtures.generated.json'
import decor from '../../data/decor.generated.json'
import expanded from '../../data/expanded.generated.json'
import { FURNITURE_CATALOG } from './furniture-catalog'
import type { AssetPlacement, FurnitureAsset, FurniturePoint } from './furniture-catalog'

export const EQUIPMENT_CATALOG = equipment as unknown as readonly FurnitureAsset[]
export const FIXTURE_CATALOG = fixtures as unknown as readonly FurnitureAsset[]
export const DECOR_CATALOG = decor as unknown as readonly FurnitureAsset[]
export const EXPANSION_CATALOG = expanded as unknown as readonly FurnitureAsset[]
export const ASSET_CATALOG: readonly FurnitureAsset[] = [...FURNITURE_CATALOG, ...EQUIPMENT_CATALOG, ...FIXTURE_CATALOG, ...DECOR_CATALOG, ...EXPANSION_CATALOG]
const byId = new Map(ASSET_CATALOG.map(asset => [asset.asset_id, asset]))
const equipmentById = new Map(EQUIPMENT_CATALOG.map(asset => [asset.asset_id, asset]))
const fixturesById = new Map(FIXTURE_CATALOG.map(asset => [asset.asset_id, asset]))
const decorById = new Map(DECOR_CATALOG.map(asset => [asset.asset_id, asset]))

export function getCatalogAsset(assetId: string): FurnitureAsset | undefined {
  return byId.get(assetId)
}

export function getEquipmentAsset(assetId: string): FurnitureAsset | undefined {
  return equipmentById.get(assetId)
}

export function getFixtureAsset(assetId: string): FurnitureAsset | undefined {
  return fixturesById.get(assetId)
}

export function getDecorAsset(assetId: string): FurnitureAsset | undefined {
  return decorById.get(assetId)
}

export function dimensionsFor(asset: FurnitureAsset, placement: AssetPlacement): FurniturePoint {
  return asset.placement_dimensions_m[placement] ?? asset.dimensions_m
}
