import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import AccountDialog from './AccountDialog'
import ShareDialog from './ShareDialog'
import { Explore, MyRooms } from './LibraryPages'
import { loadExampleScene } from '../../lib/test-support/scenes'

describe('future account and sharing surfaces', () => {
  it('keeps guest creation available and does not pretend account access works', () => {
    const html = renderToStaticMarkup(<AccountDialog onClose={vi.fn()} onGuest={vi.fn()}/>)
    expect(html).toContain('<dialog')
    expect(html).toContain('aria-labelledby=')
    expect(html).toContain('Accounts are coming soon')
    expect(html).toContain('Continue as guest')
    expect(html).toMatch(/autocomplete="username"/i)
    expect(html).toContain('type="password"')
    expect(html).toContain('Show password')
    expect(html).toMatch(/<button(?=[^>]*type="submit")(?=[^>]*disabled="")[^>]*>/)
    for (const text of ['type="email"', 'Forgot password', 'Google']) expect(html).not.toContain(text)
  })
  it('keeps a sharing draft private and never includes photos or conversation data', () => {
    const html = renderToStaticMarkup(<ShareDialog scene={loadExampleScene()} onClose={vi.fn()} onSignIn={vi.fn()}/>)
    expect(html).toContain('Private')
    expect(html).toContain('Photos and chat stay private')
    expect(html).toContain('Nothing will be published')
    expect(html).toContain('type="checkbox"')
    expect(html).not.toContain('checked=""')
    expect(html).toMatch(/disabled=""[^>]*>Share to Explore/)
    expect(html).not.toContain('<img')
  })
  it('uses truthful empty states rather than invented saved or shared projects', () => {
    const saved = renderToStaticMarkup(<MyRooms onSignIn={vi.fn()} onCreate={vi.fn()}/>)
    const feed = renderToStaticMarkup(<Explore onCreate={vi.fn()}/>)
    expect(saved).toContain('Sign in to save and reopen')
    expect(saved).toContain('Saved rooms are coming')
    expect(feed).toContain('Community rooms are coming soon')
    expect(feed).toContain('Create your own')
    expect(saved + feed).not.toContain('<img')
  })
})
