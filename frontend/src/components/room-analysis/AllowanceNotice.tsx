import type { Allowance } from '../../lib/quota'

export default function AllowanceNotice({ allowance, room = false }: { allowance: Allowance | null; room?: boolean }) {
  if (!allowance?.enabled) return null
  const remaining = room ? allowance.actions_remaining : allowance.sessions_remaining
  if (remaining === null) return null
  return <p className="input-help ai-allowance" role="status">{room
    ? remaining === 0 ? 'AI actions used for this room. Manual editing is still available.' : `${remaining} AI ${remaining === 1 ? 'change' : 'changes'} remaining for this room`
    : `${remaining} of ${allowance.sessions_limit} AI sessions remaining today · Resets at 00:00 UTC`}</p>
}
