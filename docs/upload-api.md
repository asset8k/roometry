# Temporary room-input API

The backend accepts 1–10 still photos and optional room preferences. It validates and normalizes input without calling a model or creating a project/generation run. The frontend's **Your room** workspace submits these inputs; **Explore example** keeps the fixture-based 3D editor separate.

| Endpoint | Result |
| --- | --- |
| `GET /api/rooms/input-limits` | Current public policy, including formats and byte/resolution limits |
| `POST /api/rooms/inputs` | Multipart `images` files plus one `options` JSON string; `201` with `PreparedRoomInput` |
| `DELETE /api/rooms/inputs/{input_id}` | Discard an input owned by the current upload session; `204` |

Interactive requests are available in FastAPI's `/docs`. `RoomInputOptions` is defined in `uploads/schemas.py`; an example is in `data/fixtures/room_input_options.json`. Unknown fields, duplicate JSON keys, nonfinite numbers, and invalid dimensions/budget are rejected. `{}` requests general room analysis; unknown dimensions remain null. Constraints are user notes, not automatic object locks.

## Try an upload

Start the API with `uv run uvicorn roometry.main:app --reload`. From the repository root, use a real photo path:

```bash
umask 077
upload_cookie_file=$(mktemp)
curl -c "$upload_cookie_file" \
  -F 'images=@/absolute/path/room.heic;type=image/heic' \
  -F 'options=<data/fixtures/room_input_options.json' \
  http://127.0.0.1:8000/api/rooms/inputs

# Replace INPUT_ID with the returned input_id. Preserve the session cookie.
curl -b "$upload_cookie_file" -X DELETE \
  http://127.0.0.1:8000/api/rooms/inputs/INPUT_ID
rm "$upload_cookie_file"
```

Repeat the `images` field for multiple photos. The `<` on `options` sends file contents as a string field; `@` would incorrectly upload it as a file. Let the client generate the multipart boundary. Same-origin browser requests send the session cookie normally; a separately hosted frontend needs `credentials: 'include'` and an explicitly configured CORS origin. The existing Vite `/api` proxy supports same-origin requests.

## Validation and output

Supported sources are JPEG, PNG, WebP, HEIC/HEIF, and AVIF. Extension, declared MIME type, and decoded content must agree; HEIC/HEIF aliases and missing/generic MIME declarations are handled through content detection. Animated images and multi-image sequences are rejected. HEIC/HEIF uses `pillow-heif`; the installed Pillow build also supplies AVIF decoding. Source metadata is never trusted as proof of safe content.

Default limits are 8 MiB per source, 80 MiB combined, and an 82 MiB total request. Both source edges must be 256–10,000 px, with at most 40 million pixels. Options are limited to 16 KiB and multipart headers are bounded. Actual bytes are checked even when `Content-Length` is missing or misleading.

Accepted images are fully decoded, correctly oriented, resized without upscaling to at most 2,048 px on the longest edge, flattened onto white where transparent, and written as fresh RGB JPEGs without inherited EXIF/GPS, XMP, ICC, or comments. Each prepared image is limited to 4 MiB. Originals exist only in the request's temporary spool and are closed after completion/failure. Decoding runs outside the event loop with one batch at a time per worker.

The response contains only `input_id`, `status: "ready"`, normalized `options`, ordered image metadata, and timezone-aware `expires_at`. It exposes no filenames, storage paths, session tokens, or public photo URLs. One session has one accepted batch; replacement commits only after all files pass. A failed/cancelled batch preserves the previous accepted input. A lost connection after commit can still leave an input until expiry.

## Privacy and lifecycle

An opaque HttpOnly, SameSite cookie scopes inputs. Production cookies are Secure. A resource ID alone does not grant access: resolve, internal image reads, and deletion check ownership and expiry. Browser mutations check Origin. No static image mount or public image-download endpoint exists.

The local adapter stores only normalized JPEGs under `UPLOAD_STORAGE_DIR` (default `uploads/room-inputs`), with generated names and restrictive permissions. Ownership/metadata remain in memory. Inputs expire after `UPLOAD_TTL_SECONDS` (default 3,600), become inaccessible immediately at expiry, and are physically removed by periodic cleanup (default every 60 seconds). Failed deletion is retried. Cleanup cannot run while the app is stopped.

**This adapter supports one worker only. Restart/reload discards all guest inputs and removes their managed files at startup.** Use host-supported private object storage, shared scope/expiry records, and ingress rate/storage/concurrency controls before public multi-worker deployment. Guest uploads are not durable saved projects. Day 5 can consume bytes internally through the storage interface after verifying the same session scope; image bytes do not belong in logs or graph checkpoints.

## Errors

Failures return `UploadErrorResponse`: `detail` is a list of issues with a stable `code`, safe `message`, and optional zero-based `file_index` or `field`. Decoder exceptions and raw private options are not echoed.

| HTTP | Examples |
| --- | --- |
| 400 | Malformed/incomplete multipart, duplicate or unexpected fields |
| 403 | Untrusted browser origin |
| 404 | Missing, not-owned, replaced, or expired input |
| 413 | File, combined, body, options, or prepared-image byte limit |
| 415 | Unsupported or contradictory file types |
| 422 | Invalid options/count, corrupt image, animation, or resolution |
| 503 | Temporary storage failure |

The health endpoint remains liveness-only. Storage failures do not make unrelated viewer/edit features unavailable.

## Browser form

Select photos, choose goals or leave them blank for general analysis, and optionally add room details, partial dimensions, budget/currency, style, and constraints. The form loads the active policy from the API, performs early count/format/size checks, and checks dimensions for previews the browser can decode. The backend remains authoritative, including for animation and HEIC/HEIF decoding. Browsers without HEIC preview support show a file card and still allow uploading.

Uploading shows indeterminate progress and a Cancel action. Inputs stay selected after rejection or disconnection; duplicate submissions are blocked and cancelled responses cannot overwrite a newer result. Successful submission shows accepted metadata and an expiry time. Editing files/options afterward marks the form as needing another upload. `Discard upload & clear` deletes the known accepted input before clearing local files; deletion failure keeps the handle and selections for retry. Unknown outcomes after interruption are labeled unconfirmed and may require expiry cleanup on the server.

File objects, previews, form values, and the accepted handle live in memory. Preview object URLs are revoked on removal, failed preview, clearing, or unmount. Refresh clears the form; server copies expire according to backend policy. No local-storage or IndexedDB photo persistence is used. Switching to the example workspace preserves local form state and example edits. Uploading does not generate or replace the example scene.

## Internal analysis handoff

An analysis request should supply only the accepted `input_id`; obtain the owner scope from `session(request)` on the server. Resolve normalized options and ordered image IDs using `InputStorage.resolve(input_id, owner)`, then obtain JPEG bytes with `read_image(input_id, image_id, owner)`. Run filesystem reads outside the event loop. Never trust client-provided options, image metadata, paths, or an input ID as proof of ownership.

Every image read checks ownership, membership in the accepted batch, and expiry again. A replacement, discard, or expiry between resolve and read can return `input_not_found`; stop preparation and discard any partially collected bytes. A metadata snapshot does not extend the input's lifetime. Persist references and analysis results only when that lifecycle is explicitly implemented; keep photo bytes out of logs and graph checkpoints. No analysis endpoint or model call is implemented by this upload flow.

## Review checks

Run `uv run pytest` from the repository root and `npm test` / `npm run build` from `frontend`. Contract export checks compare the Python models and source fixtures with the committed frontend copies.

For browser review, select mixed PNG/HEIC/AVIF photos, upload partial dimensions and a zero budget, and inspect the accepted details. Add a corrupt photo and retry: selections and the previous accepted batch should remain. Remove it, retry successfully, then discard. Check that switching to the example preserves form values and example edits, and that Before remains read-only. Repeat at a narrow phone width; HEIC preview failure should show a file card without blocking upload. Cancel, disconnection, expiry, and storage-failure behavior also have automated regression coverage.
