import { useEffect, useRef, useState } from 'react'
import type { RoomScene } from '../../lib/scene.generated'
import { apiAsset, feedClient } from '../../lib/feed'
import type { SharingOptions } from '../../lib/feed'
import Dialog from '../ui/Dialog'
import GlassSelect from '../ui/GlassSelect'
import { Button, Icon } from '../ui/primitives'
import { useWorkspaceGuard } from './WorkspaceNavigation'

export default function ShareDialog({ scene, sourceRunId, onClose, onSignIn, signedIn = false }: {
  scene: RoomScene; sourceRunId?: string; onClose: () => void; onSignIn: () => void; signedIn?: boolean
}) {
  const [options, setOptions] = useState<SharingOptions | null>(null)
  const [loading, setLoading] = useState(!!signedIn && !!sourceRunId), [pending, setPending] = useState(false)
  const [version, setVersion] = useState(''), [title, setTitle] = useState(''), [summary, setSummary] = useState('')
  const [consent, setConsent] = useState(false), [retry, setRetry] = useState(0)
  const [error, setError] = useState<string | null>(null), [notice, setNotice] = useState<string | null>(null)
  const request = useRef<AbortController | null>(null)
  useWorkspaceGuard({ dirty: false, saving: pending, hasWork: false })
  useEffect(() => () => request.current?.abort(), [])
  useEffect(() => {
    if (!signedIn || !sourceRunId) return
    const abort = new AbortController()
    setLoading(true); setError(null); setConsent(false)
    void feedClient.options(sourceRunId, scene, abort.signal).then(data => {
      if (abort.signal.aborted) return
      setOptions(data); setVersion(data?.selected_version_id ?? '')
      setTitle((data?.publication?.active ? data.publication.title : data?.title ?? '').slice(0, 80))
      setSummary(data?.publication?.active ? data.publication.summary : '')
    }).catch(reason => { if (!abort.signal.aborted) setError((reason as Error).message) })
      .finally(() => { if (!abort.signal.aborted) setLoading(false) })
    return () => abort.abort()
  }, [signedIn, sourceRunId, scene, retry])
  const selected = options?.versions.find(item => item.id === version)
  const submit = async (remove = false) => {
    if (!options || pending || (!remove && (!consent || !version || !title.trim()))) return
    const abort = new AbortController(); request.current = abort
    setPending(true); setError(null); setNotice(null)
    try {
      if (remove) {
        await feedClient.unpublish(options, abort.signal)
        if (!abort.signal.aborted) { setNotice('Removed from Explore. Your saved room stays private.'); setRetry(value => value + 1) }
      } else {
        const publication = await feedClient.publish(options, version, title.trim(), summary.trim(), consent, abort.signal)
        if (!abort.signal.aborted) { setOptions({ ...options, publication }); setConsent(false); setNotice('Shared to Explore.') }
      }
    } catch (reason) { if (!abort.signal.aborted) setError((reason as Error).message) }
    finally { if (!abort.signal.aborted) setPending(false); if (request.current === abort) request.current = null }
  }
  return <Dialog title="Share your space." onClose={onClose} dismissible={!pending}>{dismiss => <>
    <p className="dialog-description">Share one saved layout. Photos and chat stay private.</p>
    {loading && <p role="status">Loading saved layouts…</p>}
    {!loading && !options && !error && <>
      <div className="sharing-preview"><Icon name="room"/><div><strong>Your {scene.room.room_type.replaceAll('_', ' ')}</strong><span>Private</span></div></div>
      <p className="feature-notice">{signedIn ? 'Save your room before sharing it.' : 'Sign in and save your room before sharing it.'}</p>
      {!signedIn && <Button variant="primary" onClick={() => dismiss(onSignIn)}>Sign in</Button>}
    </>}
    {options && !loading && <form className="account-form sharing-form" onSubmit={event => { event.preventDefault(); void submit() }}>
      <fieldset disabled={pending}>
        <GlassSelect label="Layout to share" value={version} options={[{ value: '', label: 'Choose a saved layout' }, ...options.versions.map(item => ({ value: item.id, label: item.label }))]} onChange={value => { setVersion(value); setConsent(false); setNotice(null) }} />
        {!options.selected_version_id && <p className="input-help">Your current changes aren’t saved. Choose a saved layout or save first.</p>}
        {selected && <img className="sharing-scene-preview" src={apiAsset(selected.preview_url)} crossOrigin="use-credentials" alt={`Preview of ${selected.label}`}/>}
        <label>Public title<input data-initial-focus required maxLength={80} value={title} onChange={event => { setTitle(event.target.value); setConsent(false); setNotice(null) }}/></label>
        <label><span>Caption <span className="input-help">Optional</span></span><textarea maxLength={240} rows={2} value={summary} onChange={event => { setSummary(event.target.value); setConsent(false); setNotice(null) }}/></label>
        <label className="consent-choice"><input type="checkbox" checked={consent} onChange={event => setConsent(event.target.checked)}/><span>Publish this layout and preview to Explore.</span></label>
        <Button variant="primary" type="submit" disabled={!consent || !selected || !title.trim()}>{pending ? 'Updating…' : options.publication?.active ? 'Update sharing' : 'Share to Explore'}<Icon name="share"/></Button>
      </fieldset>
    </form>}
    {options?.publication?.active && <div className="dialog-actions"><a className="ui-button" href={`#shared/${options.publication.id}`} target="_blank" rel="noreferrer">View shared room<Icon name="arrow"/></a><Button disabled={pending} onClick={() => void submit(true)}>Unpublish</Button></div>}
    {error && <div className="input-notice error" role="alert"><p>{error}</p><Button disabled={pending} onClick={() => setRetry(value => value + 1)}>Reload sharing</Button></div>}
    {notice && <p className="input-notice success" role="status">{notice}</p>}
    <Button variant="quiet" disabled={pending} onClick={() => dismiss()}>Back to room</Button>
  </>}</Dialog>
}
