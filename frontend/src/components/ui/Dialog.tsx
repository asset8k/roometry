import { useEffect, useId, useRef } from 'react'
import type { ReactNode } from 'react'
import { Button, Icon } from './primitives'

/** Native modal semantics provide focus containment, Escape, and inert background. */
export default function Dialog({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const heading = useId()
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const element = dialog.current!
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    element.showModal()
    element.querySelector<HTMLElement>('[data-initial-focus]')?.focus()
    return () => {
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
    onCancel={event => { event.preventDefault(); onClose() }} onClick={event => { if (event.target === event.currentTarget) onClose() }}>
    <div className="dialog-content glass-panel">
      <Button variant="quiet" className="dialog-close" aria-label="Close dialog" onClick={onClose}><Icon name="close" /></Button>
      <h2 id={heading}>{title}</h2>
      {children}
    </div>
  </dialog>
}
