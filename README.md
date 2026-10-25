# Roometry

An AI app that reconstructs a room from photos as an approximate, editable 3D scene and suggests layout improvements. Currently in development.

## Run locally

Requires Python 3.12+, uv, and Node.js 22.12–22.x or 24+.

Backend:

```bash
uv sync --locked
cp -n .env.example .env
uv run uvicorn roometry.main:app --reload
```

Configure provider credentials in `.env` for photo generation. Keep your existing `.env` when restarting.

Frontend, in another terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open [Roometry](http://127.0.0.1:5173). Backend API docs: [localhost:8000/docs](http://127.0.0.1:8000/docs).
