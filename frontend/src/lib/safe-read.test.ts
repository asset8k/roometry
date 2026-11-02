import { afterEach, expect, it, vi } from 'vitest'
import { safeRead } from './safe-read'
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })
it('retries gateway/network failures with exponential backoff and preserves credentials', async () => {
  vi.useFakeTimers()
  const fetcher = vi.fn().mockResolvedValueOnce(new Response('', {status:503}))
    .mockRejectedValueOnce(new TypeError('network')).mockResolvedValue(new Response('ok'))
  vi.stubGlobal('fetch', fetcher)
  const pending = safeRead('/read', {credentials:'include'})
  await vi.advanceTimersByTimeAsync(499); expect(fetcher).toHaveBeenCalledTimes(1)
  await vi.advanceTimersByTimeAsync(1); expect(fetcher).toHaveBeenCalledTimes(2)
  await vi.advanceTimersByTimeAsync(1000); expect((await pending).status).toBe(200)
  expect(fetcher.mock.calls[2][1].credentials).toBe('include')
})
it.each([401,403,404,422,500])('does not retry HTTP %s', async status => {
  const fetcher = vi.fn(async () => new Response('', {status}))
  vi.stubGlobal('fetch', fetcher)
  expect((await safeRead('/read')).status).toBe(status)
  expect(fetcher).toHaveBeenCalledTimes(1)
})
it('caps retries at three attempts', async () => {
  vi.useFakeTimers()
  const fetcher=vi.fn(async()=>new Response('',{status:504}));vi.stubGlobal('fetch',fetcher)
  const pending=safeRead('/read');await vi.advanceTimersByTimeAsync(1500)
  expect((await pending).status).toBe(504);expect(fetcher).toHaveBeenCalledTimes(3)
})
it('honors cancellation during backoff', async () => {
  vi.useFakeTimers()
  const controller=new AbortController(), fetcher=vi.fn(async()=>new Response('',{status:502}))
  vi.stubGlobal('fetch',fetcher)
  const pending=safeRead('/read',{signal:controller.signal})
  const check=expect(pending).rejects.toThrow()
  await vi.advanceTimersByTimeAsync(1);controller.abort();await check
  await vi.advanceTimersByTimeAsync(2000);expect(fetcher).toHaveBeenCalledTimes(1)
})
it('retries timeout failures but never sends a mutation', async () => {
  vi.useFakeTimers()
  const fetcher=vi.fn().mockRejectedValueOnce(new DOMException('deadline','TimeoutError')).mockResolvedValue(new Response('ok'))
  vi.stubGlobal('fetch',fetcher)
  const pending=safeRead('/read');await vi.advanceTimersByTimeAsync(500);await pending
  expect(fetcher).toHaveBeenCalledTimes(2)
  await expect(safeRead('/save',{method:'POST'})).rejects.toThrow('only accepts reads')
  expect(fetcher).toHaveBeenCalledTimes(2)
})
