# AI sessions and actions

The product allowance is three **new AI room sessions per identity per UTC day**,
with three **AI actions per room session**. Reconstruction uses the first action;
any combination of improvement categories and chat refinements can use the other
two. Internal model/tool calls, bounded repairs, clarification answers, retries of
the same request, SSE reads, manual edits, saves and reopening are not new actions.

Settings:

```env
# Omit: off for development/test, on for production.
GENERATION_QUOTA_ENABLED=false
AI_SESSIONS_PER_DAY=3
AI_ACTIONS_PER_SESSION=3
AI_GUEST_SESSIONS_PER_IP_PER_DAY=12
```

An explicit true/false overrides the environment default. Enforcement requires
`DATABASE_URL`, `JWT_SECRET_KEY` and `uv run alembic upgrade head`. It never disables
authentication throttling, upload limits or per-run execution budgets.

SQL reservations serialize daily counters and room counters before execution.
Each run has a durable action receipt, marked before dispatching its first model
call. Work rejected before paid dispatch refunds its reservation. Once dispatch
begins, failures, refusals, no-improvement results and cancellation count because
inference may have incurred cost. Missing/unavailable quota storage prevents paid
execution. Disabling quotas bypasses product counters without removing run budgets.

The daily **new-session** counter resets at 00:00 UTC. Existing rooms retain their
three-action cap across days, restarts, save/reopen and guest-to-account imports.
Unused actions on an older room remain usable, so this is not a strict nine-model-
calls/day cap. A room created before quotas were enabled reserves reconstruction's
first slot when its first quota-enforced refinement begins.

Accounts use their authenticated identity. Guests use signed upload cookies and a
separate daily IP backstop; only keyed hashes are stored. Cookie clearing, VPNs,
shared networks and new accounts mean guest limits are best effort. The IP comes
from the ASGI client address; configure trusted proxy forwarding correctly at
deployment rather than trusting arbitrary forwarding headers.

`GET /api/usage` returns daily remaining sessions and reset time.
`GET /api/usage?run_id=...` also returns remaining room actions after ownership
checks. Exceeded limits return structured 429 responses; daily/network limits
include `Retry-After`. The frontend displays compact allowance text, refreshes on
mutations/focus/reset, and keeps manual editing/saving available at exhaustion.
The obsolete standalone analysis/construction endpoints return 409 while quotas
are enabled, preventing them from bypassing the guided session allowance.
