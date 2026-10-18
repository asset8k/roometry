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

Copy the environment example only on first setup; preserve an existing `.env`. No database or model-provider credentials are needed for the current scaffold. `GET http://127.0.0.1:8000/api/health` returns `{"status":"ok","service":"roometry"}`. API docs are at `http://127.0.0.1:8000/docs`. This endpoint checks API liveness only.

In another terminal, start the frontend:

```bash
cd frontend
npm ci
npm run dev
```

Open `http://127.0.0.1:5173`. Viewing the bundled scene and previewing colors work without the backend; layout edits require the API for validation. The connection indicator checks backend connectivity through Vite's `/api` development proxy and reflects edit-request failures/recovery. Optional overrides are documented in `frontend/.env.example`; copy it to `frontend/.env` to customize them. Backend `.env` settings and frontend `.env` settings are separate. Frontend variables prefixed with `VITE_` are public.

Checks:

```bash
# From the repository root
uv run pytest -q

# From frontend/
npm test
npm run build
```

`npm run build` checks TypeScript and creates `frontend/dist/`. Production API routing and deployment will be configured later.
