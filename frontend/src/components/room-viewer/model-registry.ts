import { getFurnitureAsset } from './furniture-catalog'
import { getDecorAsset, getEquipmentAsset, getFixtureAsset } from './asset-catalog'

export type ModelKind = 'furniture' | 'equipment' | 'fixture' | 'decor' | 'placeholder'

// Asset IDs are the same identifiers used by Python and the future MCP catalog.
// Similar catalog entries can reuse a visual family without sharing dimensions.
/** Missing visual models get a box; input approval is still the schema's job. */
export function getModelKind(assetId: string): ModelKind {
  if (getFurnitureAsset(assetId)) return 'furniture'
  if (getEquipmentAsset(assetId)) return 'equipment'
  if (getFixtureAsset(assetId)) return 'fixture'
  if (getDecorAsset(assetId)) return 'decor'
  return 'placeholder'
}
