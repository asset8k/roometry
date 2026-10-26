import { useId, useLayoutEffect, useRef } from 'react'
import type { ReactNode } from 'react'
import { flushSync } from 'react-dom'
import { Button, Icon } from './primitives'
import { surfaceMotion } from '../../lib/motion'

/** Native modal semantics provide focus containment, Escape, and inert background. */
export default function Dialog({ title, onClose, children, dismissible = true }: { title: string; onClose: () => void; children: ReactNode | ((dismiss: (action?: () => void) => void) => ReactNode); dismissible?: boolean }) {
  const heading = useId()
  const dialog = useRef<HTMLDialogElement>(null)
  const content = useRef<HTMLDivElement>(null)
  const mounted = useRef(false), closing = useRef(false)
  const entrance = useRef<Animation | null>(null), departure = useRef<Animation | null>(null)
  const dismiss = (action = onClose) => {
    if (closing.current || !dismissible || !mounted.current) return
    closing.current = true
    entrance.current?.cancel()
    const element = dialog.current!
    element.dataset.closing = 'true'
    content.current!.inert = true
    const animation = departure.current = surfaceMotion(content.current, 'exit', true)
    const finish = () => {
      if (!mounted.current) return
      // Keep both exit frames in place until React removes the modal. Resetting
      // first briefly reveals the panel and restarts its backdrop entrance.
      try { flushSync(action) }
      finally {
        // A guarded dismissal may choose to keep the dialog open.
        if (mounted.current) {
          animation?.cancel()
          closing.current = false
          delete element.dataset.closing
          content.current!.inert = false
        }
      }
    }
    if (animation) void animation.finished.catch(() => {}).then(finish)
    else finish()
  }
  useLayoutEffect(() => {
    const element = dialog.current!
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    mounted.current = true
    element.showModal()
    entrance.current = surfaceMotion(content.current, 'enter', true)
    element.querySelector<HTMLElement>('[data-initial-focus]')?.focus()
    return () => {
      mounted.current = false
      entrance.current?.cancel(); departure.current?.cancel()
      element.close()
      document.body.style.overflow = overflow
      previous?.focus({ preventScroll: true })
    }
  }, [])
  return <dialog ref={dialog} className="site-dialog" aria-labelledby={heading}
    onKeyDown={event => {
      if (event.key !== 'Tab') return
      const controls = [...event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]')]
        .filter(element => element.getClientRects().length > 0)
      const first = controls[0], last = controls.at(-1)
      if (!first || !last) return
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }}
    onCancel={event => { event.preventDefault(); dismiss() }} onClick={event => { if (event.target === event.currentTarget) dismiss() }}>
    <div ref={content} className="dialog-content glass-panel">
      <Button variant="quiet" className="dialog-close" aria-label="Close dialog" disabled={!dismissible} onClick={() => dismiss()}><Icon name="close" /></Button>
      <h2 id={heading}>{title}</h2>
      {typeof children === 'function' ? children(dismiss) : children}
    </div>
  </dialog>
}
