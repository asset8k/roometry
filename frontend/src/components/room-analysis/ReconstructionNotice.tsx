import type { AgentState } from '../../lib/agent-run'

/** Keep unresolved reconstruction status visible, including after save/reopen. */
export default function ReconstructionNotice({ run }: { run: AgentState | null }) {
  if (run?.proposal?.status !== 'partial') return null
  const count = run.proposal.unresolved_entities?.length ?? 0
  return <p className="workspace-status" role="status">Partial reconstruction · {count > 0
    ? `${count} ${count === 1 ? 'observation' : 'observations'} unresolved`
    : 'Some room details remain approximate'}</p>
}
