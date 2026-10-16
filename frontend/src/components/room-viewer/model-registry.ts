import type { SceneObject } from '../../lib/scene.generated'

export type ModelKind = 'bed' | 'wardrobe' | 'rug' | 'table' | 'computer' | 'tv' | 'clock' | 'chandelier' | 'curtain' | 'placeholder'

// Asset IDs are the same identifiers used by Python and the future MCP catalog.
// Similar catalog entries can reuse a visual family without sharing dimensions.
const entries: [SceneObject['asset_id'], ModelKind][] = [
  ['bed_standard_01', 'bed'],
  ['wardrobe_standard_01', 'wardrobe'],
  ['rug_standard_01', 'rug'],
  ['desk_standard_01', 'table'],
  ['table_standard_01', 'table'],
  ['coffee_table_standard_01', 'table'],
  ['side_table_standard_01', 'table'],
  ['computer_standard_01', 'computer'],
  ['tv_standard_01', 'tv'],
  ['clock_standard_01', 'clock'],
  ['chandelier_standard_01', 'chandelier'],
  ['window_covering_standard_01', 'curtain'],
  ['window_covering_curtain_01', 'curtain'],
]

const modelByAsset = new Map<string, ModelKind>(entries)

/** Missing visual models get a box; input approval is still the schema's job. */
export function getModelKind(assetId: string): ModelKind {
  return modelByAsset.get(assetId) ?? 'placeholder'
}
