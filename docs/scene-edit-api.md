# Demo scene validation and edit API

Day 3 Step 2 implements backend calculations; Step 3 connects them to the frontend editor; Step 4 adds Before/After comparison and layout reset. No database, model provider, or MCP server is required.

| Endpoint | Request | Result |
| --- | --- | --- |
| `POST /api/scenes/validate` | `{ "scene": <complete scene> }` | Baseline `GeometryReport` |
| `POST /api/scenes/preview-edit` | `{ "scene": <complete scene>, "edit": <operation> }` | Preview result with accepted/original scene and reports |

The scene follows [scene-contract.md](scene-contract.md). OpenAPI request/response models are available at `http://127.0.0.1:8000/docs` while the backend is running.

## Operations

Move to an absolute world X/Z position in meters:

```json
{
  "operation": "move",
  "object_id": "desk_1",
  "position": { "x": 0.85, "z": 2.0 }
}
```

Rotate to an absolute Y angle in radians:

```json
{
  "operation": "rotate",
  "object_id": "desk_1",
  "rotation_y_rad": 1.5707963267948966
}
```

Rotations normalize into `[0, 2π)`. Coordinates must be finite numbers between -100 and 100 m; geometry checks additionally enforce room bounds. Numeric strings, booleans, nulls, unknown operation names, extra fields, and non-finite values are rejected. Moves cannot change Y. Operations cannot change flags, appearance, dimensions, or references.

Direct transforms are limited to unlocked, non-fixed floor objects. A floor support carries its surface children with it, including their positions and orientations. Edits that would transform a locked/fixed child are rejected in full. A normalized no-op on an unprotected parent does not move a protected child. No-op requests targeting fixed/locked objects remain rejected.

## Responses and failures

Structurally valid requests return HTTP 200 even when the proposed edit fails policy or geometry checks. Malformed request contracts return HTTP 422 with `detail` entries containing `type`, `loc`, and `msg`; raw input and exception context are omitted so non-finite input cannot break error serialization.

The preview response contains:

| Field | Meaning |
| --- | --- |
| `accepted` | Whether the edit passed protection, structural, and baseline geometry checks |
| `scene` | Accepted candidate, or unchanged normalized supplied base on rejection |
| `changed_object_ids` | Sorted IDs changed by an accepted edit, including children; empty on rejection or no-op |
| `edit_errors` | Structured policy/schema failures with `code`, `entity_ids`, and `message` |
| `base_validation` | Baseline geometry report for the supplied scene |
| `candidate_validation` | Baseline geometry report for the candidate, or null if none could be evaluated |

Policy error codes are `object_not_found`, `object_locked`, `object_fixed`, `unsupported_placement`, `protected_children`, `candidate_schema`, and `invalid_base_geometry`.

On candidate geometry failure, `edit_errors` is empty and the reasons are in `candidate_validation.errors`. On an edit-policy or candidate-schema failure, `edit_errors` explains the rejection and `candidate_validation` is null. An invalid base receives `invalid_base_geometry`, a failing `base_validation`, and no candidate. The client must inspect `accepted` before replacing its working scene.

Structure is validated and normalized before editing, so unchanged means the normalized scene (for example, uppercase color hex values), not the original JSON spelling. Input scene objects are never mutated by these calculations.

These endpoints are stateless. They neither load nor save a project and cannot establish ownership or a trusted revision. Fixed/locked checks use the supplied base, which a client can replace between requests. Saved-project editing later must load an authorized previous scene from storage and enforce concurrency. A passing baseline still does not verify door swings, walking/headroom clearance, or all room-specific rules.

## Try a preview

Start the backend with `uv run uvicorn roometry.main:app --reload`. From another terminal in the repository root, generate a complete request from the bundled fixture:

```bash
uv run python - <<'PY'
import json
from pathlib import Path

body = {
    "scene": json.loads(Path("data/fixtures/bedroom_scene.json").read_text()),
    "edit": {
        "operation": "move",
        "object_id": "desk_1",
        "position": {"x": 0.85, "z": 2.0},
    },
}
Path("/tmp/roometry-preview-edit.json").write_text(json.dumps(body))
PY

curl --fail --silent --show-error \
  http://127.0.0.1:8000/api/scenes/preview-edit \
  -H 'Content-Type: application/json' \
  --data-binary @/tmp/roometry-preview-edit.json \
  | uv run python -m json.tool
```

The result should have `accepted: true` and `changed_object_ids: ["computer_1", "desk_1"]`. This previews a desk move with its computer; it does not save anything. To continue editing, submit the returned accepted scene as the next request's base. To try rejection, change the desk's target X to `2.7` (overlaps the bed) or `-0.5` (outside the room).

Run the backend checks with `uv run pytest -q`, or just these edit/API tests with `uv run pytest tests/test_scene_edits.py tests/test_scene_api.py -q`.

## Using the editor

Run both development servers and open the frontend. Select an object directly in the room or through the Objects list. A bounding-box highlight identifies the selection; clicking empty canvas space or pressing Escape while idle clears it. The inspector shows position and angle even for fixed, locked, or indirectly supported objects.

For movable floor objects, choose Move or Rotate in the inspector. Move handles use world X/Z with 5 cm steps; the Y rotation ring uses 15° steps. Numeric inputs allow finer changes: X/Z are meters and the displayed angle is degrees, converted to radians for the API. On phone-sized layouts, use Edit values above the canvas to jump to the inspector. Direct wall/ceiling/surface transforms are deferred; moving or rotating a support carries its surface children instead.

Dragging changes a temporary visual candidate, including its surface children. Releasing the handle or pressing Apply submits an absolute edit against the last accepted local scene. Orbit controls are disabled during a handle drag. While checking, the candidate is shown, selection/edit controls are held, and a Cancel edit action remains available. The backend's accepted scene replaces the working scene only after response shape and report consistency checks pass.

Rejected edits restore the accepted scene and list the affected objects. Network failures, HTTP failures, invalid responses, and cancellation also restore it. Escape cancels an active drag/request; cancellation aborts the request and ignores any late response, including one arriving after another successful edit. The service indicator reflects edit-request failures and successful contact. These local states do not establish a saved revision or ownership.

Color previews remain separate from the geometry working copy, so accepted/rejected/cancelled moves cannot discard them. Fixed colors are editable in After; locked colors remain protected. Refresh resets both geometry edits and color previews to the bundled After fixture.

Frontend contracts under `src/lib/edit-request.*` and `edit-response.*` are generated from Python with `npm run scene:generate`. Visual preview math has tests for the shared rotation convention and child propagation; Python remains the spatial authority. Controller/API tests cover acceptance, rollback, offline errors, cancellation, overlapping submissions, stale responses, and color compatibility. Run them with `npm test` from `frontend/`.

## Before/After comparison

The viewer starts in **After**, an authored alternative from `data/fixtures/bedroom_after_scene.json`. **Before** displays the original `bedroom_scene.json` read-only: objects can be selected and inspected, but movement, rotation, color changes, and layout reset are disabled. These are two synthetic examples, not AI recommendations. They share the same room dimensions, openings, stable object IDs, assets, dimensions, colors, and fixed/locked objects. Only movable poses differ; the computer remains on its desk.

Switching preserves accepted After edits and its independent color previews. Before always shows the original poses and colors. Selection and old notices clear when switching, while camera and wall-display settings stay in place. Switching during a drag or validation request cancels the candidate, aborts the request, and ignores late responses.

**Reset layout** restores the authored After geometry, clears selection, and keeps After color previews. It also cancels active edits safely. **Reset colors** restores After's fixture colors without changing geometry. Neither action changes Before. All changes are local and unsaved; refreshing returns to the authored After layout and colors. Comparison and reset work without a backend; new geometry edits require it.

Both fixtures pass schema and baseline geometry checks during export. Tests verify stable identity and protected-object preservation, support-child movement and collision rollback on After, independent source copies, read-only guards, switching/reset during active edits, and stale-response handling. This baseline still does not validate door swings, walking clearance, headroom, or room-specific planning rules.
