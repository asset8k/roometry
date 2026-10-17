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

**Status:** Day 2 is complete: scene schemas, fixtures, tests, a FastAPI health endpoint, and a React Three Fiber viewer with nine procedural object models, camera controls, wall display modes, and local color previews are implemented. Day 3 Steps 1–2 add backend geometry validation and a stateless move/rotate preview API. The broader model catalog, agent, and interactive editing are still in development. Target MVP: October 29, 2026.

This is an Applied AI Engineering portfolio project focused on stateful agents, human-in-the-loop execution, structured scene editing, and verification. Room geometry is approximate; Roometry is not a precision measurement or building-code certification tool.

## Local development

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), and Node.js 22.12–22.x or 24+. Node.js 23 is outside the test runner's supported versions.

From the repository root, start the backend:

```bash
uv sync --locked
cp -n .env.example .env
uv run uvicorn roometry.main:app --reload
```

Copy the environment example only on first setup; preserve an existing `.env`. No database or model-provider credentials are needed for the current scaffold. `GET http://127.0.0.1:8000/api/health` returns `{"status":"ok","service":"roometry"}`. API docs are at `http://127.0.0.1:8000/docs`. This endpoint checks API liveness only.

In another terminal, start the frontend:

```bash
cd frontend
npm ci
npm run dev
```

Open `http://127.0.0.1:5173`. The room shell loads a bundled example scene and works without the backend. The connection indicator separately checks backend connectivity through Vite's `/api` development proxy. Optional overrides are documented in `frontend/.env.example`; copy it to `frontend/.env` to customize them. Backend `.env` settings and frontend `.env` settings are separate. Frontend variables prefixed with `VITE_` are public.

Checks:

```bash
# From the repository root
uv run pytest -q

# From frontend/
npm test
npm run build
```

`npm run build` checks TypeScript and creates `frontend/dist/`. Production API routing and deployment will be configured later.

## Day 2 review checkpoints

1. **Implemented:** generated scene types, validated fixture loading, floor/walls, door/window markers, and orbit/zoom/pan controls. Walls use inward-facing surfaces for a cutaway view; openings are markers rather than physical wall cutouts.
2. **Implemented:** bed, rug, desk, computer, TV, clock, chandelier, and curtain models; scene transforms/colors; bounding-box fallbacks for approved assets without a model; and local color previews that preserve locked objects. Openings and wall-mounted objects follow the cutaway and appear when the camera moves to their room-facing side. Colors reset on refresh and do not modify the source fixture.
3. **Implemented:** north-up top-down and perspective presets, zoom buttons, reset for the current view, cutaway/transparent/hidden walls, responsive controls, and retry after renderer errors or WebGL context loss. Added the wardrobe example required by the Day 2 plan. Camera framing accounts for room proportions and viewport size, including square rooms. Resetting the camera preserves color previews and wall settings.

Top-down uses an overhead perspective camera, not a measured orthographic floor plan. Transparent/hidden wall modes keep opening markers and wall-mounted objects visible; cutaway hides near-wall attachments with their host walls. Door/window markers are decorative surfaces, not wall cutouts. WebGL is required for rendering. The viewer is loaded separately from the page, but the production build still reports a large Three.js bundle; further loading/performance work remains for later milestones.

Day 2 verification covers schema loading, coordinates, model bounds/transforms/colors, unknown-model fallbacks, locked-object color protection, camera framing/reset, and wall visibility. Desktop and phone-sized browser layouts have been checked.

## Day 3 review checkpoints

1. **Implemented:** backend geometry baseline in `scenes/geometry.py` and `scenes/validation.py`, with structured failures and 110 geometry tests. The nine-object bedroom fixture passes these checks. Run `uv run pytest tests/test_geometry.py -q`; see `docs/scene-contract.md` for usage and rule codes. Passing this baseline does not establish full physical acceptance.
2. **Implemented:** deterministic move/rotate operations, fixed/locked protection, support-child transforms, and a stateless demo preview/validation API. Invalid candidates return the unchanged normalized base scene. See [the edit API guide](docs/scene-edit-api.md) for endpoints, response behavior, and a runnable example. HTTPX is a development-only dependency for API tests.
3. **Pending:** frontend selection and transform controls, with backend validation and rejected-edit recovery.
4. **Pending:** before/after fixture switching and integration verification.

Python remains the source of truth. After changing its schemas, asset metadata, or `data/fixtures/bedroom_scene.json`, run `npm run scene:generate` from `frontend/`, then review and commit the generated JSON Schema, TypeScript types, and frontend fixture. Frontend validation checks JSON shape; cross-field Python rules and physical geometry still require backend validation. Generated files are committed so ordinary frontend builds do not need Python.

Procedural models are defined in `frontend/src/components/room-viewer/ObjectModels.tsx`, with stable asset-to-family mapping in `model-registry.ts`. Models fit a normalized box with a bottom-center pivot; scene dimensions scale them into meters. Desk and table assets already share one model family. The future MCP furniture catalog will reference these same asset IDs with item dimensions and metadata; renderer assets do not imply purchasable catalog entries exist yet.
