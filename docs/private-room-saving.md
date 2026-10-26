# Private room saving — milestone step 2

The viewer's **Save** action saves the existing room without generating it again. A guest first signs in, then presses Save explicitly; account creation alone never saves or imports a room.

A private save includes:

- Exact copies of the existing normalized JPEG photos, outside temporary upload storage.
- Server-owned observations, reconstruction report, clarification answers and original-room ratings.
- Original, retained improvement/refinement scenes, their starting contexts, and every accepted editable copy, including color previews and locks.
- The current comparison selection and refinement conversation displayed in the workspace.

Scenes are immutable version records. A new save updates the private project's snapshot and revision, retaining earlier versions. Unchanged versions and photos are reused. The current viewer selection does not discard manual edits in another comparison view.

## Configuration

Run `uv run alembic upgrade head` before saving rooms. This adds `projects`, `project_images`, `scene_versions` and `project_saves`; scene/snapshot documents use PostgreSQL JSONB. User/session tables from step 1 remain unchanged.

`PROJECT_STORAGE_DIR` defaults to `.local/project-images`. Keep this directory private and outside `UPLOAD_STORAGE_DIR`. It is never cleared by guest cleanup or backend startup. Photo files are private, normalized copies; raw original HEIC files and EXIF metadata are not retained.

The current storage adapter uses local disk. Deployment needs a persistent private volume or an object-storage adapter; an ephemeral cloud filesystem is not durable storage. PostgreSQL contains private metadata and storage references, not image blobs. Back up database and photo storage together.

## API

- `POST /api/projects`: explicit save/update with a client-generated `request_id`, owned `source_run_id`, title, editable copies, comparison state and conversation. Updates require `project_id` and `expected_revision`.
- `GET /api/projects/{id}`: authenticated owner's private snapshot, version history and image metadata. This supports verification and step 3's reopening work.
- `GET /api/projects/{id}/images/{image_id}`: authenticated owner-only JPEG bytes. No static public image URL or filesystem key is returned.

The first save requires the temporary run and its photos to remain available. Once saved, the project and its photos remain available independently of guest expiry, deletion and backend restart. Subsequent saves of already-retained scenes use durable source data, without requiring the old guest run.

Retries reuse the exact frozen request. A database receipt returns the original confirmation even if the response was lost or the guest upload has since expired. Reusing the identifier with different content is rejected. Conditional revision updates prevent stale saves from overwriting newer work.

Account authentication and guest-source ownership are both checked. Another account cannot retrieve or update the saved project or its photos. An account cookie alone does not grant access to someone else's guest run. Nothing is published by a save.

Requests are bounded at 8 MiB and reject unknown fields, duplicate JSON keys, invalid scene references and unsupported message roles. Manual scenes reuse the existing geometry/context checks; clearance warnings remain visible. Failures preserve the browser workspace and roll back partial database writes. A failed/ambiguous commit never causes deletion of a possibly committed photo set. If database access prevents proving an orphan is unreferenced, its private files are retained for later cleanup rather than risking a valid saved photo.

## Verification and next step

`uv run pytest tests/test_projects.py tests/test_auth.py -q` covers explicit saving, account isolation, private photos, duplicate saves, immutable revisions, manual edits/locks, retained improvements, invalid data, storage/database rollback and restart recovery. The frontend tests cover saved-copy capture, explicit authentication, frozen retries and account changes.

Browser verification used a fixture reconstruction with real uploads, authentication, PostgreSQL and durable photo storage. It verified guest → sign-in → explicit save → backend restart → save changes. A separate local PostgreSQL check verified photo retrieval after restart and concurrent revision protection. No model requests were made.

**Steps 3 and 4 are complete:** see [room reopening, registered-run recovery and the final audit](room-recovery.md). Public sharing and daily generation quotas remain in the following milestone. Saving is explicit; there is no autosave yet.
