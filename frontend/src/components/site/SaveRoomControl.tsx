import { useEffect, useState, useSyncExternalStore } from 'react'
import type { AgentState } from '../../lib/agent-run'
import type { SceneComparison } from '../../lib/scene-comparison'
import { ProjectSaveController } from '../../lib/projects'
import type { SavedMessage, SavedProject } from '../../lib/projects'
import Dialog from '../ui/Dialog'
import { Button, Icon } from '../ui/primitives'
import { useSiteActions } from './SiteActions'

export default function SaveRoomControl({ run, comparison, messages, disabled, initial }: {
  run: AgentState; comparison: SceneComparison; messages: SavedMessage[]; disabled: boolean; initial?: SavedProject
}) {
  const actions = useSiteActions(), owner = actions?.userId ?? null
  const [controller] = useState(() => new ProjectSaveController())
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot)
  const [open, setOpen] = useState(false)
  const [title, setTitle] = useState(initial?.title ?? `Your ${run.working_scene!.room.room_type.replaceAll('_', ' ')}`)
  useEffect(() => { if (owner && initial) controller.restoreSaved(owner, initial); controller.setOwner(owner); setOpen(false) }, [controller, owner, initial])
  useEffect(() => controller.release, [controller])
  const own = state.owner === owner
  return <>
    <Button disabled={disabled || (own && state.pending)} onClick={() => {
      if (!owner) { actions?.account('signin'); return }
      setOpen(true)
    }}><Icon name="save"/>{own && state.saved ? 'Save changes' : 'Save'}</Button>
    {open && owner && own && <Dialog title={state.saved ? 'Save room changes' : 'Save your room'} onClose={() => { if (!state.pending) setOpen(false) }}>
      <p className="dialog-description">Photos, chat and layouts stay private. Saving doesn’t generate a new room.</p>
      <form className="account-form" onSubmit={event => { event.preventDefault(); void controller.save(run.run_id, title, comparison.snapshotForSave, messages) }}>
        <label>Room name<input data-initial-focus value={title} onChange={event => setTitle(event.target.value)} minLength={1} maxLength={120} required disabled={state.pending || state.retry} /></label>
        {state.error && <p className="input-notice error" role="alert">{state.error}</p>}
        {state.retry && <p className="input-help">Retry checks the same snapshot. It won’t create a duplicate room.</p>}
        {state.saved && !state.error && <p className="input-notice success" role="status">Saved privately · {state.saved.image_count} {state.saved.image_count === 1 ? 'photo' : 'photos'} · revision {state.saved.revision}.</p>}
        <Button variant="primary" type="submit" disabled={state.pending || disabled || !title.trim()} aria-busy={state.pending}>{state.pending ? 'Saving room…' : state.retry ? 'Retry same save' : state.saved ? 'Save changes' : 'Save privately'}<Icon name="save"/></Button>
        <Button variant="quiet" disabled={state.pending} onClick={() => setOpen(false)}>Back to room</Button>
      </form>
    </Dialog>}
  </>
}
