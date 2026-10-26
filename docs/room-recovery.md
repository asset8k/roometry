# Private rooms and registered-run recovery

**My rooms** now lists saved private rooms and recent registered reconstructions. Saved rooms support open, rename and confirmed deletion. Opening restores the Original / Improved comparison, accepted editable copies, primary-color previews, locks, ratings, photos and saved refinement chat. Changes still require **Save changes**; there is no autosave or implicit publication.

Run `uv run alembic upgrade head` to install the registered-input, run, checkpoint and pending-write tables. The existing local PostgreSQL database has been migrated. README remains unchanged.

## Execution state versus saved history

A signed-in generation copies its normalized photos into private storage and receives account-scoped execution ownership. That ownership does not depend on the temporary guest cookie. Provisional registered runs can be recovered for seven days; expired execution records, checkpoints and unreferenced photos are cleaned up. The upload form discloses this retention. Guest executions remain temporary, and signing in alone does not save/import their rooms.

Saving a finished room attaches its execution records to its private project. Saved project history and photos are independent of provisional-run expiry. Opening an older saved room can create fresh runtime references to its existing photos and validated scenes, without analyzing the photos or reconstructing the scene again.

The SQL checkpointer implements LangGraph's checkpoint and pending-write interface and restricts typed deserialization. Graph execution uses synchronous checkpoint durability. The analysis cache needed by an interrupted construction is restored with its saved results and attempt counters, so answering a recovered question does not repeat analysis.

Model-call reservations are recorded before dispatch. An execution lease prevents concurrent drivers. Recovery does not replay an uncertain in-flight paid request: after its lease expires it stops with the last retained checkpoint and conservative usage information. Completed requests and duplicate answers return the retained state. A queued request that never began execution can be explicitly continued using its original request identifier.

The checkpoint adapter follows the [LangGraph checkpoint contract](https://reference.langchain.com/python/langgraph.checkpoint). Checkpoints do not contain SDK clients, API keys or browser JWTs.

## Operation and limits

Use **one active FastAPI worker** while guest photo storage remains local/process-owned. PostgreSQL checkpoint recovery is not a distributed background-job system. A hosted deployment still needs persistent private photo storage (a persistent volume or an object-store adapter), database backups and an appropriate worker/runtime configuration.

Closing or refreshing a registered workspace does not send an automatic cancellation request. Explicit Cancel remains available. A backend restart during a genuinely in-flight provider request may stop that operation; it is deliberately not retried automatically. A normally paused human question survives restart and resumes from its stored interrupt.

In-app navigation, the Roometry logo, New room and sign-out now guard unsaved work. Staying preserves the current workspace; confirmed departure stops active reconstruction/improvement work before opening a fresh page. An unconfirmed cancellation keeps the workspace available for retry. Pending saves must finish first, and only the exact confirmed save clears its dirty baseline. Browser refresh/close uses the native unsaved-work warning; registered recovery still works after refresh. Leaving never deletes a saved project or its private photos.

Private project reads, image reads, mutations, run discovery and recovery require the owning account. Project deletion revokes associated runtime references/checkpoints and removes unreferenced photo batches. Rename/delete use revisions to reject stale mutations. Account changes hide the previous account's saved workspace; guest drafts retain their existing behavior.

Saved JSON is compared by values while preserving array order: PostgreSQL JSONB can reorder object keys, which must not look like a changed or forged scene. Protected measurements, object identities, locks and placement constraints still apply.

## Verification

The automated tests use real LangGraph execution and SQL checkpoints with mocked provider HTTP. They cover restart/resume without repeating analysis, duplicate answers, account isolation, persisted optimization results, unknown in-flight-call recovery, expiry cleanup, private room management and restoration of comparison/editor state. Browser verification uses real local PostgreSQL with mocked model responses, including pause → restart/refresh → answer → scene → save → rename/open → refinement → save/refresh.

## Final audit — milestone step 4

The authentication/persistence milestone is complete for the local single-worker MVP. The final audit adds regression coverage for:

- Another account accessing saved scenes, photos, run/request discovery, questions, cancellation, optimization or progress streams; every private reference remains owner-scoped.
- Revoked sessions, including progress connections already open at logout. Streams recheck the session before replay and throughout observation, and close without exposing further progress.
- Deletion of rooms whose reconstruction/optimization is cached; neither cached state nor private photo URLs remain usable.
- Saved photos and history surviving provisional runtime expiry; reopening creates fresh runtime references without another analysis or reconstruction call.
- Database failure during hydration, stale snapshots, duplicate answers, persisted cancellation, and recovery of an uncertain optimization after its old lease expires. Unknown paid work is never automatically replayed.
- Explicit reuse of one browser upload after an account change. Analysis caches, confirmed relationships and attempt counters are scoped to the account.

Room hydration now locks the owned project and commits all runtime references together. Independent local PostgreSQL checks exercise six concurrent opens, competing execution claims and simultaneous revision updates. Only one execution claim/revision mutation wins; all opens restore the same runtime input and original scene. The test also checks restart/resume, private photo access, foreign-account rejection, token revocation and deletion using synthetic images and intercepted provider HTTP. No paid API requests are needed for this audit.

See `tests/test_persistence_audit.py` for the automated ownership/recovery matrix. The deployment limits above still apply. Public sharing and daily quotas remain in the following milestone; README remains unchanged.
