import { createContext, useContext, useEffect, useRef } from 'react'
import type { WorkspaceGuard, WorkspaceNavigation } from '../../lib/workspace-navigation'

export const WorkspaceNavigationContext = createContext<WorkspaceNavigation | null>(null)

export function useWorkspaceGuard(guard: WorkspaceGuard) {
  const navigation = useContext(WorkspaceNavigationContext)
  const id = useRef(Symbol('workspace')), current = useRef(guard)
  current.current = guard
  useEffect(() => navigation?.register(id.current, () => current.current), [navigation])
  useEffect(() => { navigation?.refresh() }, [navigation, guard.dirty, guard.active, guard.saving, guard.hasWork])
}
