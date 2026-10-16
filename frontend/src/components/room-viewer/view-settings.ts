import { createContext } from 'react'

export type WallMode = 'cutaway' | 'transparent' | 'hidden'
export const WallModeContext = createContext<WallMode>('cutaway')
