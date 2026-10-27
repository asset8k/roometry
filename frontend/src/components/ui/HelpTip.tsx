import { useId, useState } from 'react'
import type { ReactNode } from 'react'

/** The same short hint is available on hover, keyboard focus and touch. */
export default function HelpTip({ label, children, trigger = 'i' }: { label: string; children: string; trigger?: ReactNode }) {
  const id = useId()
  const [open, setOpen] = useState(false)
  return <span className="help-tip" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
    <button type="button" aria-label={label} aria-describedby={open ? id : undefined} aria-expanded={open}
      onFocus={() => setOpen(true)} onBlur={() => setOpen(false)} onClick={() => setOpen(true)}
      onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); setOpen(false) } }}>{trigger}</button>
    <span id={id} className="help-tip-content" role="tooltip" hidden={!open}>{children}</span>
  </span>
}
