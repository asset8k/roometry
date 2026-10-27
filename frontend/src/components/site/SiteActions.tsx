import { createContext, useContext } from 'react'
import type { RoomScene } from '../../lib/scene.generated'

export const SiteActions = createContext<{
  userId?: string | null
  account: (intent?: 'signin' | 'save') => void
  share: (scene: RoomScene, sourceRunId?: string) => void
} | null>(null)
export const useSiteActions = () => useContext(SiteActions)
