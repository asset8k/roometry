/** Bounded retries for idempotent reads only. Mutations always have one attempt. */
export async function safeRead(url: string, init: RequestInit = {}, timeoutMs = 10000): Promise<Response> {
  if (!['GET', 'HEAD'].includes((init.method ?? 'GET').toUpperCase()) || init.body != null) throw new Error('safeRead only accepts reads')
  const signal = init.signal
  for (let attempt = 0; ; attempt++) {
    signal?.throwIfAborted()
    try {
      const response = await fetch(url, { ...init, signal: signal
        ? AbortSignal.any([signal, AbortSignal.timeout(timeoutMs)]) : AbortSignal.timeout(timeoutMs) })
      if (attempt === 2 || ![502, 503, 504].includes(response.status)) return response
      await response.body?.cancel()
    } catch (error) {
      if (signal?.aborted || attempt === 2 || !(error instanceof TypeError || (error instanceof DOMException && error.name === 'TimeoutError'))) throw error
    }
    await new Promise<void>((resolve, reject) => {
      const abort = () => { clearTimeout(timer); reject(signal?.reason) }
      const timer = setTimeout(() => { signal?.removeEventListener('abort', abort); resolve() }, 500 * 2 ** attempt)
      signal?.addEventListener('abort', abort, { once: true })
      if (signal?.aborted) abort()
    })
  }
}
