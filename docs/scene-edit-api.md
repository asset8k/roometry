# Demo scene validation and edit API

Day 3 Step 2 implements backend calculations. Frontend editing controls are the next checkpoint. No database, model provider, or MCP server is required.

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
