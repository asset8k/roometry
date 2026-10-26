import { useCallback, useLayoutEffect, useRef, useSyncExternalStore } from 'react'
import type { ComponentPropsWithoutRef, RefObject } from 'react'
import { surfaceMotion } from '../../lib/motion'

export function useSurfaceMotion(ref: RefObject<HTMLElement | null>, change: string | number | null) {
  useLayoutEffect(() => {
    const animation = surfaceMotion(ref.current)
    return () => animation?.cancel()
  }, [ref, change])
}

/** Changing a motion key animates the existing wrapper; it never keys its children. */
export default function MotionRegion({ motionKey, ...props }: ComponentPropsWithoutRef<'div'> & { motionKey: string | number }) {
  const ref = useRef<HTMLDivElement>(null)
  useSurfaceMotion(ref, motionKey)
  return <div ref={ref} {...props} />
}

export function useMediaQuery(query: string) {
  const subscribe = useCallback((listener: () => void) => {
    const media = window.matchMedia(query)
    media.addEventListener('change', listener)
    return () => media.removeEventListener('change', listener)
  }, [query])
  const snapshot = useCallback(() => window.matchMedia(query).matches, [query])
  return useSyncExternalStore(subscribe, snapshot, () => false)
}
