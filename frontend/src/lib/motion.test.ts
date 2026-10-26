import { afterEach, expect, it, vi } from 'vitest'
import { pageMotion, reducedMotion, surfaceMotion } from './motion'

function preference(reduce = false) {
  const listeners = new Set<() => void>()
  const media = { matches: reduce, addEventListener: (_: string, fn: () => void) => listeners.add(fn), removeEventListener: (_: string, fn: () => void) => listeners.delete(fn) }
  vi.stubGlobal('window', { matchMedia: () => media })
  return { media, listeners }
}
function element() {
  let finish!: () => void, cancel!: () => void
  const finished = new Promise<void>((resolve, reject) => { finish = resolve; cancel = () => reject(new Error('cancelled')) })
  const animation = { finished, finish: vi.fn(finish), cancel: vi.fn(cancel) }
  const animate = vi.fn(() => animation)
  return { node: { animate } as unknown as HTMLElement, animate, animation }
}
afterEach(() => vi.unstubAllGlobals())

it('does no visual work or delayed navigation when reduced motion is requested', async () => {
  preference(true)
  const { node, animate } = element(), update = vi.fn(), snapshots = vi.fn()
  vi.stubGlobal('document', { startViewTransition: snapshots })
  expect(reducedMotion()).toBe(true)
  expect(surfaceMotion(node)).toBeNull()
  await pageMotion(node, update)
  expect(update).toHaveBeenCalledTimes(1)
  expect(animate).not.toHaveBeenCalled(); expect(snapshots).not.toHaveBeenCalled()
})

it('falls back safely without browser snapshots and commits only once', async () => {
  preference()
  vi.stubGlobal('document', {})
  const update = vi.fn()
  await pageMotion(null, update)
  expect(update).toHaveBeenCalledTimes(1)
})

it('holds the invisible exit frame until the closing surface is removed', async () => {
  preference()
  const { node, animate, animation } = element()
  surfaceMotion(node, 'exit', true)
  expect(animate.mock.calls[0]).toEqual([
    [{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'translateY(8px) scale(.985)' }],
    expect.objectContaining({ fill: 'forwards' }),
  ])
  animation.finish()
  await animation.finished
})

it('does not replay a completed state update when snapshot capture fails', async () => {
  preference()
  const update = vi.fn()
  vi.stubGlobal('document', { startViewTransition: (apply: () => void) => {
    apply()
    return { updateCallbackDone: Promise.reject(new Error('capture failed')), finished: Promise.resolve(), skipTransition: vi.fn() }
  } })
  await pageMotion(null, update)
  expect(update).toHaveBeenCalledTimes(1)
})

it('supersedes stale page swaps during rapid navigation rather than mounting both pages', async () => {
  preference()
  const callbacks: (() => void)[] = [], releases: (() => void)[] = [], skips: ReturnType<typeof vi.fn>[] = []
  vi.stubGlobal('document', { startViewTransition: (apply: () => void) => {
    callbacks.push(apply)
    const skipTransition = vi.fn(); skips.push(skipTransition)
    const updateCallbackDone = new Promise<void>(resolve => releases.push(resolve))
    return { updateCallbackDone, finished: updateCallbackDone, skipTransition }
  } })
  const old = vi.fn(), latest = vi.fn()
  const first = pageMotion(null, old), second = pageMotion(null, latest)
  callbacks[0](); releases[0]()
  callbacks[1](); releases[1]()
  await Promise.all([first, second])
  expect(skips[0]).toHaveBeenCalledTimes(1)
  expect(old).not.toHaveBeenCalled(); expect(latest).toHaveBeenCalledTimes(1)
})

it('finishes an in-progress effect when the motion preference changes and releases its listener', async () => {
  const { media, listeners } = preference()
  const { node, animation } = element()
  expect(surfaceMotion(node)).toBe(animation)
  expect(listeners.size).toBe(1)
  media.matches = true; listeners.forEach(listener => listener())
  await animation.finished; await Promise.resolve(); await Promise.resolve()
  expect(animation.finish).toHaveBeenCalledTimes(1)
  expect(listeners.size).toBe(0)
})
