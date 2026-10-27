import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { expect, it } from 'vitest'
import AllowanceNotice from '../components/room-analysis/AllowanceNotice'
import { parseAllowance } from './quota'

const allowance = { enabled: true, sessions_limit: 3, sessions_remaining: 2, actions_limit: 3, actions_remaining: 1, resets_at: '2026-10-28T00:00:00Z' }
it('shows daily sessions separately from the current room allowance', () => {
  expect(renderToStaticMarkup(createElement(AllowanceNotice, { allowance }))).toContain('2 of 3 AI sessions remaining today')
  expect(renderToStaticMarkup(createElement(AllowanceNotice, { allowance, room: true }))).toContain('1 AI change remaining')
  expect(renderToStaticMarkup(createElement(AllowanceNotice, { allowance: { ...allowance, actions_remaining: 0 }, room: true }))).toContain('Manual editing is still available')
})
it('keeps disabled quotas invisible and rejects malformed counts', () => {
  expect(renderToStaticMarkup(createElement(AllowanceNotice, { allowance: { ...allowance, enabled: false } }))).toBe('')
  expect(parseAllowance(allowance)).toEqual(allowance)
  expect(() => parseAllowance({ ...allowance, actions_remaining: -1 })).toThrow()
  expect(() => parseAllowance({ ...allowance, sessions_remaining: 4 })).toThrow()
})
