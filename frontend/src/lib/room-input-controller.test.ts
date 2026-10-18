import { describe, expect, it, vi } from 'vitest'
import { RoomInputController } from './room-input-controller'
import { RoomInputError } from './room-input'
import { limits, photo, prepared } from './test-support/room-input'

type Client = NonNullable<ConstructorParameters<typeof RoomInputController>[0]>
function setup(overrides: Partial<Client> = {}) {
  const client: Client = { limits: vi.fn(async () => limits), upload: vi.fn(async files => prepared(files)), discard: vi.fn(async () => {}), ...overrides }
  const urls = { create: vi.fn(() => `blob:${Math.random()}`), revoke: vi.fn() }
  const controller = new RoomInputController(client, urls)
  return { client, urls, controller }
}
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

describe('room input controller', () => {
  it('does not select photos until public policy is loaded; policy failure can be retried', async () => {
    const api = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(limits)
    const { controller } = setup({ limits: api })
    controller.addPhotos([photo()])
    expect(controller.getSnapshot().photos).toEqual([])
    await controller.loadLimits()
    expect(controller.getSnapshot().policyError).toBeTruthy()
    await controller.loadLimits()
    controller.addPhotos([photo()])
    expect(controller.getSnapshot().photos).toHaveLength(1)
  })
  it('releases object URLs after removal, unavailable previews, reset and unmount', async () => {
    const { controller, urls } = setup()
    await controller.loadLimits()
    controller.addPhotos([photo(), photo('phone.heic', 'image/heic')])
    const [first, second] = controller.getSnapshot().photos
    controller.previewFailed(second.id)
    expect(controller.getSnapshot().photos[1].url).toBeNull()
    controller.removePhoto(first.id)
    expect(urls.revoke).toHaveBeenCalledTimes(2)
    controller.addPhotos([photo()])
    await controller.discard()
    expect(urls.revoke).toHaveBeenCalledTimes(3)
    controller.addPhotos([photo()])
    controller.release()
    expect(urls.revoke).toHaveBeenCalledTimes(4)
    expect(controller.getSnapshot().photos).toEqual([])
  })
  it('rejects a bad addition atomically and explains the new filename', async () => {
    const { controller, urls } = setup()
    await controller.loadLimits()
    controller.addPhotos([photo()])
    controller.addPhotos([photo('valid.png'), photo('bad.svg', 'image/svg+xml')])
    expect(controller.getSnapshot().photos).toHaveLength(1)
    expect(urls.create).toHaveBeenCalledTimes(1)
    expect(controller.getSnapshot().notice?.issues?.[0].message).toContain('bad.svg')
  })
  it('blocks known invalid dimensions and photo resolution without making a request', async () => {
    const { controller, client } = setup()
    await controller.loadLimits()
    controller.addPhotos([photo()])
    controller.change({ width: '0' })
    expect(await controller.submit()).toBe(false)
    controller.change({ width: '' })
    controller.previewSize(controller.getSnapshot().photos[0].id, 50, 50)
    expect(await controller.submit()).toBe(false)
    expect(client.upload).not.toHaveBeenCalled()
  })
  it('requires confirmation again when previously checked dimensions change', () => {
    const { controller } = setup()
    controller.change({ width: '4', confirmed: true })
    controller.change({ style: 'Cozy' })
    expect(controller.getSnapshot().draft.confirmed).toBe(true)
    controller.change({ width: '4.1' })
    expect(controller.getSnapshot().draft.confirmed).toBe(false)
  })
  it('accepts input, marks later changes dirty, and preserves the previously accepted batch on rejection', async () => {
    const { controller, client } = setup()
    await controller.loadLimits()
    controller.addPhotos([photo()])
    expect(await controller.submit()).toBe(true)
    const accepted = controller.getSnapshot().accepted
    controller.change({ goalText: 'More storage' })
    vi.mocked(client.upload).mockRejectedValueOnce(new RoomInputError('Bad photo', [{ code: 'invalid_image', message: 'Bad photo', file_index: 0 }], true))
    expect(await controller.submit()).toBe(false)
    expect(controller.getSnapshot()).toMatchObject({ accepted, dirty: true, uncertain: false })
    expect(controller.getSnapshot().draft.goalText).toBe('More storage')
    expect(controller.getSnapshot().photos).toHaveLength(1)
  })
  it('keeps files and text on network failure and confirms a successful retry', async () => {
    const { controller, client } = setup()
    await controller.loadLimits()
    controller.addPhotos([photo()])
    controller.change({ description: 'My room' })
    vi.mocked(client.upload).mockRejectedValueOnce(new Error('offline'))
    expect(await controller.submit()).toBe(false)
    expect(controller.getSnapshot()).toMatchObject({ uncertain: true, draft: { description: 'My room' } })
    expect(await controller.submit()).toBe(true)
    expect(controller.getSnapshot()).toMatchObject({ uncertain: false, dirty: false })
  })
  it.each(['success', 'error'])('cancels and ignores stale upload %s after a newer submission', async late => {
    const pending = deferred<ReturnType<typeof prepared>>()
    const { controller, client } = setup()
    await controller.loadLimits()
    controller.addPhotos([photo()])
    vi.mocked(client.upload).mockReturnValueOnce(pending.promise)
    const first = controller.submit()
    expect(await controller.submit()).toBe(false)
    controller.change({ description: 'Blocked while uploading' })
    expect(controller.getSnapshot().draft.description).toBe('')
    const signal = vi.mocked(client.upload).mock.calls[0][2]
    controller.cancel()
    expect(signal.aborted).toBe(true)
    expect(await controller.submit()).toBe(true)
    const current = controller.getSnapshot()
    if (late === 'success') pending.resolve(prepared())
    else pending.reject(new Error('late error'))
    expect(await first).toBe(false)
    expect(controller.getSnapshot()).toBe(current)
  })
  it('retains inputs when deletion fails and clears/revokes them only after confirmation', async () => {
    const { controller, client, urls } = setup()
    await controller.loadLimits()
    controller.addPhotos([photo()])
    await controller.submit()
    vi.mocked(client.discard).mockRejectedValueOnce(new Error('offline'))
    expect(await controller.discard()).toBe(false)
    expect(controller.getSnapshot().photos).toHaveLength(1)
    expect(controller.getSnapshot().uncertain).toBe(true)
    expect(urls.revoke).not.toHaveBeenCalled()
    expect(await controller.discard()).toBe(true)
    expect(controller.getSnapshot()).toMatchObject({ accepted: null, photos: [], dirty: false })
    expect(urls.revoke).toHaveBeenCalledTimes(1)
  })
  it('allows reuse after cleanup, including React StrictMode effect replay', async () => {
    const { controller } = setup()
    await controller.loadLimits()
    controller.release()
    await controller.loadLimits()
    controller.addPhotos([photo()])
    expect(await controller.submit()).toBe(true)
  })
  it('expires accepted input without discarding the selected local files', async () => {
    let clock = Date.now()
    const client: Client = { limits: async () => limits, upload: async files => prepared(files, clock + 1000), discard: async () => {} }
    const controller = new RoomInputController(client, { create: () => 'blob:photo', revoke: vi.fn() }, () => clock)
    await controller.loadLimits()
    controller.addPhotos([photo()])
    await controller.submit()
    clock += 1000
    controller.checkExpiry()
    expect(controller.getSnapshot()).toMatchObject({ accepted: null, dirty: true })
    expect(controller.getSnapshot().photos).toHaveLength(1)
  })
  it('ignores a late discard response after cancellation and a newer upload', async () => {
    const pending = deferred<void>()
    const { controller, client, urls } = setup()
    await controller.loadLimits()
    controller.addPhotos([photo()])
    await controller.submit()
    vi.mocked(client.discard).mockReturnValueOnce(pending.promise)
    const clearing = controller.discard()
    controller.cancel()
    expect(controller.getSnapshot().uncertain).toBe(true)
    expect(await controller.submit()).toBe(true)
    const current = controller.getSnapshot()
    pending.resolve()
    expect(await clearing).toBe(false)
    expect(controller.getSnapshot()).toBe(current)
    expect(urls.revoke).not.toHaveBeenCalled()
  })
  it('rolls back partially allocated preview URLs if allocation fails', async () => {
    const { controller, urls } = setup()
    await controller.loadLimits()
    urls.create.mockReturnValueOnce('blob:first').mockImplementationOnce(() => { throw new Error('allocation failure') })
    controller.addPhotos([photo(), photo()])
    expect(controller.getSnapshot().photos).toEqual([])
    expect(urls.revoke).toHaveBeenCalledWith('blob:first')
  })
})
