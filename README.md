# Roometry

**AI 3D Room Optimization Agent**

Roometry is a web application being built to turn photos of a single room into an approximate, editable 3D scene and suggest practical furniture layout improvements.

The planned experience combines a stateful LangGraph agent, a custom furniture-catalog MCP server, deterministic geometry checks, and an interactive browser-based 3D viewer.

Planned capabilities:

- Photo analysis with explicit assumptions and human clarification.
- Bounded tool use and iterative layout revision with collision and clearance checks.
- Editable 3D rooms with original/optimized layout comparison.
- Private saved projects, optional public sharing, and generation limits.
- Reproducible offline agent evaluations.

Planned stack: Python, FastAPI, Pydantic, LangGraph, MCP, PostgreSQL, React, TypeScript, and React Three Fiber / Three.js.

This is an Applied AI Engineering portfolio project focused on stateful agents, human-in-the-loop execution, structured scene editing, and verification. Room geometry is approximate; Roometry is not a precision measurement or building-code certification tool.

## Local development

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), and Node.js 22.12–22.x or 24+. Node.js 23 is outside the test runner's supported versions.

From the repository root, start the backend:

```bash
uv sync --locked
cp -n .env.example .env
uv run uvicorn roometry.main:app --reload
```

Copy the environment example only on first setup; preserve an existing `.env`. Health checks work without a database or model-provider credentials; real photo generation requires the configured provider credentials. `GET http://127.0.0.1:8000/api/health` returns `{"status":"ok","service":"roometry"}`. API docs are at `http://127.0.0.1:8000/docs`. This endpoint checks API liveness only.

In another terminal, start the frontend:

```bash
cd frontend
npm ci
npm run dev
```

Open `http://127.0.0.1:5173`. The guest flow uploads room photos, asks necessary questions, reconstructs a room, and offers a separate improvement request. The backend validates uploads, generation and layout edits; connection errors appear beside the affected operation. Live progress uses SSE through Vite's `/api` development proxy. Optional overrides are documented in `frontend/.env.example`; copy it to `frontend/.env` to customize them. Backend `.env` settings and frontend `.env` settings are separate. Frontend variables prefixed with `VITE_` are public.

Checks:

```bash
# From the repository root
uv run pytest -q

# From frontend/
npm test
npm run build
```

`npm run build` checks TypeScript and creates `frontend/dist/`. Production API routing and deployment will be configured later.

## Live run progress

The original start/answer/execute POST drives the LangGraph execution. Progress subscriptions are **read-only** and never invoke a graph, model, or tool. Existing idempotency and cancellation behavior remain in place.

- Reconstruction: `GET /api/agent-runs/{run_id}/events`
- Optimization: `GET /api/agent-runs/{source_run_id}/optimizations/{run_id}/events`

Both require the same private guest session as the run. A different owner, wrong optimization parent, expired input, or revoked run cannot access the stream. The client discovers the run ID with the existing read-by-request-ID endpoint while a long start POST is in flight.

The `progress` and `snapshot` SSE events contain versioned `ProgressEvent` JSON: run ID, sequence, kind, trusted stage/status, step/candidate counters, and a terminal flag. They exclude prompts, tool arguments, provider text, images, scene JSON, and private reasoning. `frontend/src/lib/run-progress.schema.json` is exported from the backend with the other contracts.

Event IDs have the form `{run_id}:{sequence}`. Native EventSource sends `Last-Event-ID` on reconnect; the server replays up to 128 retained events and always sends a current snapshot signal. Missing/invalid/too-old cursors reconcile to the latest owned state through the existing validated GET endpoint. Full scenes and clarification questions are fetched there, not embedded in SSE. In-flight reads are coalesced so a later update is not lost.

Streams send a heartbeat every 10 seconds, recheck authorization at least every second while waiting, and roll over after 120 seconds. Each run permits four concurrent subscribers. Healthy streams suspend routine frontend polling. After three connection errors, malformed events, or 25 seconds without activity, the client uses the existing bounded read/poll recovery path. Reconnection does not repeat paid mutations. Closing an SSE connection only releases that subscription; explicit cancellation still uses its existing POST.

Progress history shares the current **process-local, expiring run lifetime**. A backend restart loses temporary runs/history; shared durable state and multi-worker deployment support remain part of the persistence/hosting milestones.

## Scene workspace and refinements

A completed or partial reconstruction opens a viewer-first workspace. Overview retains the original room ratings; Objects provides manual transforms, color previews and additional locks; Chat requests a bounded refinement of the visible scene. Mobile uses a Room tools sheet. Original, retained candidates, and editable copies remain separate.

The existing improvement preparation endpoint accepts an optional `context` (`scene`, `parent_run_id`) and an optional `instruction` (1–1,000 characters). Chat requires a context. A parent must be an owned, retained candidate from the same reconstruction. The server verifies room data, inventory, model dimensions, mounting, supports, protected objects, manual transforms and visible material controls against that ancestry, then checks geometry and access regressions before making model calls. Only this scene-bearing preparation route receives a 512 KiB JSON-body allowance; other agent mutations keep their existing smaller limit.

Refinement reuses the propose → geometry/score checks → semantic review graph and existing private SSE. It does not repeat photo analysis or reconstruction. Each chat request permits at most two candidates and four model calls (or lower configured limits), and a temporary reconstruction permits five chat requests. Repeated request IDs recover the same work. Refinements may move, rotate or recolor existing furniture; they do not add catalog purchases, alter dimensions/openings, or remove protected items. Specific user intent is judged during review rather than requiring a generic focus-score increase; geometry, clearance and lock checks still apply.

Requests capture one starting snapshot, including submitted manual edits, colors and locks. Unchanged objects stay intact. Newer edits made while AI works are retained; the returned candidate stays available for explicit comparison instead of replacing them. Failed/rejected/cancelled work preserves prior scenes. Ratings shown in Overview continue to describe the original reconstruction; candidate details compare the submitted starting snapshot and retained result. Guest chat and copies share the temporary session lifetime and are not saved across refresh.

The streaming/workspace milestone has been verified locally; see [verification results](docs/streaming-workspace-verification.md) for live-flow usage, test coverage and limitations. The UI uses a shared black, smoked-glass and neon-lime theme; progress uses descriptive two-word stages, viewport-aware custom selectors, keyboard navigation and reduced-motion styles. Partial results stay labeled, and phase changes bring progress/questions/errors into view without jumping on each tick.
