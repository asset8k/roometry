export interface RoomDestination { hash: string; fresh?: boolean; signOut?: boolean; replace?: boolean; traverse?: number }
export interface WorkspaceGuard {
  dirty: boolean
  active?: boolean
  saving?: boolean
  hasWork?: boolean
  priority?: number
  leave?: () => Promise<void>
}
interface NavigationState {
  pending: RoomDestination | null; leaving: boolean; error: string | null
  dirty: boolean; active: boolean; saving: boolean; hasWork: boolean
}

/** Leaving the workspace never deletes a saved project or starts model work. */
export class WorkspaceNavigation {
  private guards = new Map<symbol, () => WorkspaceGuard>()
  private listeners = new Set<() => void>()
  private state: NavigationState = { pending: null, leaving: false, error: null, dirty: false, active: false, saving: false, hasWork: false }
  constructor(private commit: (destination: RoomDestination) => void | Promise<void>) {}
  getSnapshot = () => this.state
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener) } }
  private update(patch: Partial<NavigationState>) {
    if (Object.entries(patch).every(([key, value]) => this.state[key as keyof NavigationState] === value)) return
    this.state = { ...this.state, ...patch }; this.listeners.forEach(listener => listener())
  }
  register = (id: symbol, guard: () => WorkspaceGuard) => {
    this.guards.set(id, guard); this.refresh()
    return () => { this.guards.delete(id); this.refresh() }
  }
  refresh = () => {
    const guards = [...this.guards.values()].map(read => read())
    this.update({ dirty: guards.some(g => g.dirty), active: guards.some(g => g.active), saving: guards.some(g => g.saving), hasWork: guards.some(g => g.hasWork) })
  }
  shouldWarn = () => { this.refresh(); return this.state.dirty || this.state.active || this.state.saving }
  request = (destination: RoomDestination) => {
    if (this.state.leaving) return
    this.update({ pending: destination, error: null })
    if (!this.shouldWarn()) void this.confirm()
  }
  stay = () => { if (!this.state.leaving) this.update({ pending: null, error: null }) }
  confirm = async () => {
    const destination = this.state.pending
    this.refresh()
    if (!destination || this.state.leaving || this.state.saving) return false
    this.update({ leaving: true, error: null })
    try {
      // Stop improvements before stopping/discarding their source reconstruction.
      for (const read of [...this.guards.values()].sort((a, b) => (a().priority ?? 0) - (b().priority ?? 0))) {
        await read().leave?.()
      }
      this.refresh()
      if (this.state.saving) throw new Error('Wait for the save to finish before leaving.')
      await this.commit(destination)
      this.update({ pending: null, leaving: false, error: null })
      return true
    } catch (error) {
      this.update({ leaving: false, error: error instanceof Error ? error.message : 'Couldn’t leave this room. Your workspace is kept.' })
      return false
    }
  }
}

export function isRoomRoute(hash: string): boolean {
  return /^(#|#explore|#my-rooms|#(?:room|run)\/[a-f0-9-]{36})$/.test(hash)
}

export function protectPageUnload(navigation: WorkspaceNavigation, event: BeforeUnloadEvent) {
  if (!navigation.shouldWarn()) return
  event.preventDefault()
  event.returnValue = ''
}
