import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import type { CSSProperties, KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import { Icon } from './primitives'

type Option = { value: string; label: string }

/** A single-choice listbox. Focus stays on the trigger, including keyboard navigation. */
export default function GlassSelect({ label, value, options, onChange, disabled = false, placeholder = 'Choose an option', className = '' }: {
  label: string; value: string; options: readonly Option[]; onChange: (value: string) => void
  disabled?: boolean; placeholder?: string; className?: string
}) {
  const id = useId(), trigger = useRef<HTMLButtonElement>(null), menu = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false), [active, setActive] = useState(0)
  const [position, setPosition] = useState<CSSProperties>({ visibility: 'hidden' })
  const search = useRef({ text: '', time: 0 })
  const selected = options.findIndex(option => option.value === value)
  const expanded = open && !disabled
  const show = (last = false) => {
    if (disabled || !options.length) return
    setActive(selected >= 0 ? selected : last ? options.length - 1 : 0)
    setOpen(true)
  }
  const choose = (index: number) => {
    const option = options[index]
    if (!option || disabled) return
    onChange(option.value)
    setOpen(false)
  }
  useEffect(() => { if (disabled) setOpen(false) }, [disabled])
  useLayoutEffect(() => {
    if (!expanded) return
    const place = () => {
      const rect = trigger.current!.getBoundingClientRect()
      const below = window.innerHeight - rect.bottom - 16, above = rect.top - 16
      const up = below < Math.min(280, options.length * 44 + 16) && above > below
      const height = Math.min(340, Math.max(80, up ? above : below))
      const width = Math.min(Math.max(rect.width, 200), window.innerWidth - 24)
      setPosition({ position: 'fixed', left: Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)),
        width, maxHeight: height, ...(up ? { bottom: window.innerHeight - rect.top + 8 } : { top: rect.bottom + 8 }) })
    }
    place()
    window.addEventListener('resize', place)
    window.addEventListener('scroll', place, true)
    const dismiss = (event: Event) => {
      if (event.target instanceof Node && !trigger.current?.contains(event.target) && !menu.current?.contains(event.target)) setOpen(false)
    }
    document.addEventListener('pointerdown', dismiss)
    document.addEventListener('focusin', dismiss)
    return () => {
      window.removeEventListener('resize', place)
      window.removeEventListener('scroll', place, true)
      document.removeEventListener('pointerdown', dismiss)
      document.removeEventListener('focusin', dismiss)
    }
  }, [expanded, options.length])
  useEffect(() => {
    if (expanded) menu.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [active, expanded])
  const keyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === 'Tab') { setOpen(false); return }
    if (event.key === 'Escape' && expanded) { event.preventDefault(); event.stopPropagation(); setOpen(false); return }
    if (['ArrowDown', 'ArrowUp', 'Home', 'End', 'Enter', ' '].includes(event.key)) {
      event.preventDefault()
      if (!expanded) { show(event.key === 'ArrowUp' || event.key === 'End'); return }
      if (event.key === 'Enter' || event.key === ' ') { choose(active); return }
      setActive(index => event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1 : (index + (event.key === 'ArrowUp' ? -1 : 1) + options.length) % options.length)
    } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault()
      const now = Date.now(), previous = now - search.current.time < 600 ? search.current.text : ''
      const text = previous === event.key.toLowerCase() ? previous : previous + event.key.toLowerCase()
      search.current = { text, time: now }
      const start = expanded ? active : selected
      const index = options.findIndex((_, offset) => options[(Math.max(start, -1) + offset + 1) % options.length].label.toLowerCase().startsWith(text))
      if (index >= 0) { setActive((Math.max(start, -1) + index + 1) % options.length); setOpen(true) }
    }
  }
  return <div className={`glass-select ${className}`}>
    <label id={`${id}-label`} htmlFor={id}>{label}</label>
    <button id={id} ref={trigger} className="glass-select-trigger" type="button" role="combobox" disabled={disabled}
      aria-labelledby={`${id}-label`} aria-expanded={expanded} aria-haspopup="listbox" aria-controls={expanded ? `${id}-list` : undefined}
      aria-activedescendant={expanded ? `${id}-option-${active}` : undefined} onKeyDown={keyDown}
      onClick={() => expanded ? setOpen(false) : show()}>
      <span>{options[selected]?.label ?? placeholder}</span>
      <svg className="glass-select-chevron" viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="m5 7.5 5 5 5-5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
    </button>
    {expanded && createPortal(<div ref={menu} id={`${id}-list`} role="listbox" aria-labelledby={`${id}-label`}
      className="glass-select-menu" style={position} onPointerDown={event => event.preventDefault()}>
      {options.map((option, index) => <div id={`${id}-option-${index}`} key={option.value} role="option" aria-selected={option.value === value}
        data-index={index} data-active={index === active} className="glass-select-option" onPointerMove={() => setActive(index)} onClick={() => choose(index)}>
        <span>{option.label}</span>{option.value === value && <Icon name="check"/>}
      </div>)}
    </div>, trigger.current?.closest('dialog') ?? document.body)}
  </div>
}
