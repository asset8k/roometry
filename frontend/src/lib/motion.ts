import { flushSync } from 'react-dom'

export const MOTION = { enter: 260, exit: 180, easing: 'cubic-bezier(.22, 1, .36, 1)' } as const

export function reducedMotion(): boolean {
  return typeof window === 'undefined' || !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
}

/** Visual work only: never owns UI state, timers, requests or component lifetimes. */
export function surfaceMotion(element: HTMLElement | null, direction: 'enter' | 'exit' = 'enter', dialog = false): Animation | null {
  if (!element?.animate || reducedMotion()) return null
  const quiet = { opacity: 0, transform: dialog ? 'translateY(8px) scale(.985)' : 'translateY(5px)' }
  const visible = { opacity: 1, transform: 'none' }
  const animation = element.animate(direction === 'enter' ? [quiet, visible] : [visible, quiet], {
    duration: direction === 'enter' ? MOTION.enter : MOTION.exit, easing: MOTION.easing,
    fill: direction === 'exit' ? 'forwards' : 'none',
  })
  // Changing the OS preference during an animation should take effect immediately.
  const preference = window.matchMedia?.('(prefers-reduced-motion: reduce)')
  const finish = () => { if (preference?.matches) animation.finish() }
  preference?.addEventListener('change', finish)
  void animation.finished.catch(() => {}).finally(() => preference?.removeEventListener('change', finish))
  return animation
}

let revision = 0
let active: ViewTransition | null = null

/** Swap a page exactly once. Snapshots never mount a second generation controller. */
export async function pageMotion(element: HTMLElement | null, update: () => void): Promise<void> {
  const current = ++revision
  active?.skipTransition()
  let applied = false
  const apply = () => {
    if (applied || current !== revision) return
    applied = true
    flushSync(update)
  }
  if (typeof document === 'undefined' || reducedMotion() || !document.startViewTransition) {
    apply()
    surfaceMotion(element)
    return
  }
  try {
    const transition = document.startViewTransition(apply)
    active = transition
    void transition.finished.catch(() => {}).finally(() => { if (active === transition) active = null })
    await transition.updateCallbackDone
  } catch {
    // Unsupported/hidden-document snapshots must not swallow or replay navigation.
    apply()
  }
}
