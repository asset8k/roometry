/** Keep backend metric identifiers out of user-facing explanations. */
export function readableText(value: string): string {
  return value.replace(/\bstorage_reference_m3\b/g, 'typical storage size')
    .replace(/\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b/gi, text => text.replaceAll('_', ' '))
}

export function conciseText(value: string, limit = 220): string {
  const text = readableText(value).trim()
  if (text.length <= limit) return text
  const end = text.lastIndexOf(' ', limit - 1)
  return text.slice(0, end > limit / 2 ? end : limit - 1).replace(/[,:;.]$/, '') + '…'
}
