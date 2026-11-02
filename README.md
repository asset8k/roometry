# Roometry

> Turn room photos into an editable 3D reconstruction, then explore validated improvements to space, storage, workspace, and aesthetics.

**Live application:** [roometry-ai.vercel.app](https://roometry-ai.vercel.app) · **API:** [roometry.fastapicloud.dev](https://roometry.fastapicloud.dev)

**Deployment status:** The frontend and API are deployed. Account creation and Explore connectivity have been checked; all 25 synthetic examples and their public previews have passed a read-only production check. Generation and SSE verification remain in progress.

![Photo-based room reconstruction with an interactive 3D viewer and room ratings](docs/screenshots/reconstruction.jpg)

Roometry separates two tasks: reconstruct the room that exists, then improve it after the user chooses a goal. A LangGraph workflow combines visual observations, clarification questions, a 217-model furniture catalog exposed through MCP, geometric validation, and bounded corrections. The result is a navigable scene with explicit uncertainty—not a generated interior photograph.

**Python 3.12+ · FastAPI · LangGraph · OpenAI Responses API · MCP · PostgreSQL · SQLAlchemy · Alembic · React 19 · TypeScript · Three.js / React Three Fiber · Vite**

On the final 25-room regression evaluation, **20/25 passed visual LLM review**, **20/25 passed owner human review**, and **15/25 passed both code and LLM gates**. These measure different things; the [evaluation section](#evaluation) explains the rubric, failures, and limits.

## Contents

- [Product capabilities](#product-capabilities)
- [Screenshots](#screenshots)
- [Architecture](#architecture)
- [AI workflow and model routing](#ai-workflow-and-model-routing)
- [Catalog and geometry](#catalog-and-geometry)
- [Evaluation](#evaluation)
- [Reliability, ownership, and allowances](#reliability-ownership-and-allowances)
- [API](#api)
- [Run locally](#run-locally)
- [Configuration and deployment](#configuration-and-deployment)
- [Tests and repository structure](#tests-and-repository-structure)
- [Known limitations](#known-limitations)
- [Sources and licensing](#sources-and-licensing)

## Product capabilities

- **Reconstruct from photos:** upload 1–10 views of the same room, select its type, and optionally provide dimensions and descriptive context. JPEG, PNG, WebP, and HEIC/HEIF inputs are normalized before analysis.
- **Resolve uncertainty:** up to three focused questions identify missing measurements, wall orientation, or furniture identity. Unknown dimensions can use disclosed estimates or assumed starting sizes.
- **Inspect an editable scene:** orbit, zoom, switch to top-down, choose wall visibility, select objects, adjust positions/rotation, preview colors, and protect objects with locks. Editing creates a separate layout while retaining the original.
- **Choose an improvement:** four assessment cards cover space and movement, storage and organization, workspace, and aesthetics and cohesion. A separate optimization graph proposes, checks, reviews, and retains an acceptable candidate—or keeps the previous scene.
- **Refine in chat:** request additions, removals, material changes, or repositioning from the visible scene, including current edits and locks. Compare Original, Improved, and a manual layout without overwriting their source versions.
- **Follow live progress:** SSE delivers workflow stages, questions, and terminal results. Reconnection reads existing work rather than starting another paid generation.
- **Save and share deliberately:** guests can create rooms; accounts can explicitly save and reopen rooms with scene versions. Explore contains opt-in public layouts and previews, without source photos or private chat.

## Screenshots

Landscape captures from the local application, exported at 2880 × 1620. The reconstruction and object controls use an actual Eval 2 scene; the improvement example is an existing saved living room. These are real account records, not mocked scenes or claims of exact photo reproduction. Screenshot examples are separate from the evaluation metrics below.

### Photo input and clarification

![Photo upload, room type, and optional room measurements](docs/screenshots/room-input.jpg)

*Room details are optional; a clarification can resolve an important unknown before construction.*

![Three-choice clarification before scene construction](docs/screenshots/clarification.jpg)

*Clarification UI replay: the production component displays a measurement question. This capture does not incur another model call.*

### Object controls and saved improvements

![Object selection and editable layout controls alongside the reconstructed room](docs/screenshots/object-editing.jpg)

*The original stays intact; edits operate on a separate layout and pass backend validation.*

![Saved improved layout with its rating change](docs/screenshots/improvement.jpg)

*A saved living-room improvement reopens with Original / Improved comparison and recorded gains for space and storage.*

### Saved rooms and public examples

![Account-owned room library with reopen and management controls](docs/screenshots/saved-rooms.jpg)

*Saved originals and accepted versions reopen without another generation.*

![Explore showing actual opt-in shared room previews](docs/screenshots/explore.jpg)

*Explore reads public layout snapshots; publishing does not expose source photos or chat.*

### Accounts

![Sign-in dialog with guest access](docs/screenshots/authentication.jpg)

*Username/password authentication is owned by the FastAPI application. PostgreSQL hosting is separate from authentication logic.*

## Architecture

```mermaid
flowchart LR
    UI[React + TypeScript<br/>Vercel] -->|HTTP + SSE| API[FastAPI<br/>FastAPI Cloud]
    API --> R[LangGraph reconstruction]
    API --> O[LangGraph optimization]
    R --> AI[OpenAI Responses API]
    O --> AI
    R <--> MCP[MCP catalog subprocess<br/>stdio · read-only search/details]
    O <--> MCP
    R --> G[Catalog geometry + support<br/>clearance + relationship checks]
    O --> G
    API <--> DB[(PostgreSQL<br/>Supabase deployment target)]
    API --> STORE[Private photo storage<br/>persistent storage adapter]
    API --> UI
    UI --> VIEW[React Three Fiber / Three.js<br/>shared catalog recipes]
```

**Deployment topology:** Vercel serves the frontend, FastAPI Cloud hosts the API, and Supabase hosts PostgreSQL and private saved photos. The application supports local or Supabase private photo storage; existing local files are not moved automatically. All 25 synthetic examples and their public previews have passed a read-only production check; generation and SSE verification remain in progress.

The API owns model credentials, account authorization, scene validation, graph execution, and persistence. The browser renders typed scene data; it does not generate arbitrary model-supplied JavaScript or meshes. Python exports the scene contracts and catalog recipes consumed by TypeScript so the renderer and validators share the same model definitions.

## AI workflow and model routing

The following diagram describes the expanded product flow. The actual reconstruction graph uses `guard`, `advance`, `tools`, `clarify`, `review_scene`, `assess_room`, and `finalize` nodes; several stages below execute inside those nodes.

```mermaid
flowchart TD
    A[Photos + room details] --> B[Photo analysis<br/>GPT-6.1 Terra]
    B --> C{Clarification needed?}
    C -->|Yes| D[Ask user · checkpoint · interrupt<br/>validate answer · resume]
    C -->|No| E[Resolve dimensions, wall axes<br/>catalog models + supports]
    D --> E
    E --> F[Scene construction<br/>GPT-6.1 Terra]
    F --> G[Geometry + relationship checks<br/>deterministic backend]
    G -->|Repair needed| H[Construction repair<br/>GPT-6 Luna · bounded]
    H --> I[Validate · retain usable objects<br/>report unresolved observations]
    G -->|Usable scene| J[Photo + diagnostic render review<br/>GPT-6 Luna]
    I --> J
    J -->|Repairable differences| K[Visual correction<br/>GPT-6 Luna · at most once]
    K --> L[Validate correction<br/>or retain preceding scene]
    J -->|No correction| M[Room assessment<br/>backend proxies + Luna aesthetics]
    L --> M
    M --> N[Original room + ratings]
    N -->|User selects a goal or sends chat| O[Improvement proposal<br/>GPT-6.1 Terra]
    O --> P[Placement search + geometry<br/>locks, clearance, utility, goal checks]
    P -->|Invalid or insufficient gain; budget remains| O
    P -->|Valid candidate| Q[Candidate review<br/>GPT-6 Luna]
    Q --> R[Keep accepted version<br/>or retain previous layout]
```

### Model decisions

| Operation | Recorded Eval 2 profile | Purpose |
| --- | --- | --- |
| Photo analysis | `gpt-6.1-terra`, low reasoning | Observe objects, appearances, walls, and cross-view relationships |
| Scene construction | `gpt-6.1-terra`, medium reasoning | Select catalog representations and propose poses |
| Construction / visual correction | `gpt-6-luna`, medium reasoning | Produce narrow structured repairs |
| Photo/render review | `gpt-6-luna`, low reasoning | Identify repairable differences within bounded context |
| Aesthetics and candidate review | `gpt-6-luna`, low reasoning | Supplement deterministic measures with visual/design judgment |
| Improvement / chat proposal | `gpt-6.1-terra`, low reasoning | Propose complete typed scene modifications |

Terra supplies the difficult visual interpretation and construction work; Luna handles narrower review and repair tasks. These are configurable model identifiers, not immutable snapshot guarantees or a controlled model-comparison result. The table describes the recorded evaluation and `.env.example` profile; code defaults and environment overrides can differ.

A Terra improvement proposal is already a structured candidate change. Luna reviews it; there is no separate Luna “implementation” step. The backend applies permitted operations, validates the result, and preserves an acceptable version.

### Context and output boundaries

[Analysis and construction instructions](src/roometry/vision/prompts.py), request builders in `vision/`, and the [optimization provider](src/roometry/optimization/provider.py) version the policy alongside code. Context includes normalized photos, observations, catalog metadata, confirmed constraints, and scoped feedback. Construction prioritizes major objects and openings before dependent decor. Compact context and a target photo subset reduce redundant input; unique photographic evidence can still require additional views.

Provider output must satisfy structured schemas and semantic constraints. Repairs are bounded by per-stage and whole-run budgets; they do not silently replace supplied measurements. Invalid independent review findings can be isolated rather than discarding an otherwise usable scene. Objects that cannot be placed remain named unresolved observations, not invisible successful reconstructions.

Clarification uses a real LangGraph interruption. Paid analysis happens before the interruption, not inside replayed question code. Resuming an answer therefore does not repeat photo analysis.

## Catalog and geometry

The catalog contains **217 procedural model definitions**, including furniture, kitchen/bathroom fixtures, appliances, lighting, storage, plants, and decorations. It is a representation catalog, not a vendor inventory or a price database.

The [MCP server](src/roometry/mcp/furniture_server.py) exposes read-only `search_furniture`, `get_furniture_details`, and `list_furniture_categories` tools over stdio. The backend starts the subprocess when needed; a separate public MCP domain is not required for this transport.

A catalog entry carries dimensions, allowed mountings, simplified solid components, and named support areas. A tabletop is a usable support surface; an entire table is not treated as a solid box. This allows chairs near desks, objects on shelves, and inset fixtures without broadly disabling collision checks.

Validation covers rotated room boundaries, component overlaps, floor/wall/ceiling attachment, support footprints, opening conflicts, door access and known door swings, and basic walking routes. Unknown hinge/swing information stays explicitly unassessed. Existing clearance problems remain visible; manual edits that introduce disallowed regressions are rejected with rollback.

Four room ratings are decision aids, not reconstruction accuracy metrics. Space, storage, and workspace use deterministic planning proxies and observed evidence; aesthetics uses a model judgment. No dedicated workspace can be shown as unassessed rather than inventing a zero-quality desk. Optimization checks missing baseline features, meaningful goal gains, protected objects, and allowed trade-offs before accepting a candidate.

## Evaluation

[Final metrics](docs/evaluation/eval-2.json) · [Human judgments](data/evals/results/eval-2-human.json) · [Dataset gallery](data/evals/index.html) · [Dataset guide](data/evals/GUIDE.md)

**Eval 2 — 30 October 2026:** 25 fresh original reconstructions and eight predefined improvement trials, run once per case with normal bounded internal repairs. All results were saved and read back from an account. Evaluation references were not passed into generation prompts. Only final-round results are published here.

The dataset contains seven living rooms, six bedrooms, five kitchens, four bathrooms, and three offices: **20 generated images and five licensed photographs**. It began with five development and twenty held-out cases; after failure analysis and reuse, it is a regression dataset, not an untouched holdout. References were authored in-session and are not independently human-validated.

| Evaluation layer | What it measures | Final result |
| --- | --- | ---: |
| Code checks | Schema, geometry, major inventory, functional features, wall/support relationships and execution evidence | **15/25 (60%)** |
| LLM visual judge | Five dimensions, each 0–2: major objects, layout, orientation/support, proportions, appearance | **20/25 (80%)** |
| Human visual judge | Project owner's per-room boolean pass/fail review | **20/25 (80%)** |
| Code + LLM gates | Both checks pass on the same reconstruction | **15/25 (60%)** |
| All three layers | Code, LLM and human pass on the same reconstruction | **14/25 (56%)** |

Mean visual score: **7.96/10**. Mean major-anchor coverage: **96.7%**. All 25 produced saved, schema-valid, collision-valid, renderable scenes; that alone does not mean all 25 resemble their source rooms.

The code evaluator requires 90% major-object coverage and 85% assessed spatial consistency, with feature and hard-constraint checks. Visual passing requires at least 8/10 with no zero dimension. The judge was Codex in the coding session, inspecting source photos and multiple diagnostic views—not a separate paid judge API. Available improvements used side-blinded A/B review. The human review was one unblinded owner review; four “slight fail” judgments count as false, and the original notes are retained. Human improvement ratings were not supplied.

### Improvements and remaining failures

**4/8 improvement trials** produced accepted, useful changes to their reconstructed baseline. Three found no acceptable candidate; one was blocked before model dispatch because the reconstruction lacked an observed radiator. Only **1/8** combined a useful accepted improvement with an original that passed independent reconstruction gates. A promising redesign does not repair an incorrect baseline by itself.

Failures include missing bedside or storage features, incorrect wall/support relationships, duplicated kitchen sinks, and chairs or bedside units positioned on the wrong side. Ten original cases still fail the combined code/visual criterion. Exact photo-derived dimensions and centimeter-level position accuracy are not established by these checks.

### Cost and latency

| Measure | Recorded result |
| --- | ---: |
| 25 originals | **$1.3489735** total |
| Mean original | **$0.0540**; all 25 below $0.10 |
| 8 improvement trials | **$0.1667200** total |
| Entire evaluation | **$1.5156935**; 152 provider calls |
| Original latency | **129.5 s mean · 130.7 s median · 171.2 s p90** |

Costs are estimates computed from recorded provider token usage and pinned standard rates, not invoices. Image and reasoning tokens are included. There were no unpriced calls and no paid judge requests. This small, partly synthetic, developer-reviewed sample does not establish general population accuracy.

The [offline runner](scripts/run_evals.py) evaluates saved executions without calling OpenAI. Raw recordings remain local because they contain account identifiers and full execution payloads. To replay your own recording bundle:

```sh
uv run python scripts/run_evals.py \
  --manifest data/evals/manifest.json \
  --recordings artifacts/your-run/recordings/index.json \
  --calibration data/evals/calibration-v1.json \
  --reviews artifacts/your-run/chat-reviews.json \
  --output artifacts/evaluations/your-replay --render
```

This requires an existing compatible recording bundle and a new output directory. A fresh clone includes the public photos, annotations, and final metrics—not account-owned raw recordings.

## Reliability, ownership, and allowances

- **Idempotent execution:** duplicate starts/answers and terminal execution reads reuse existing state. SSE reconnection does not initiate paid calls. Unknown in-flight requests after a restart are not blindly replayed.
- **Bounded work:** model calls, tool calls, graph steps, response sizes, output tokens, candidate attempts, and deadlines are capped. Cancellation is cooperative; it may not undo an already dispatched provider request.
- **Explicit persistence:** account-owned runs use SQL checkpoints. Save persists source photos, original and accepted versions, edits, locks, ratings, and chat. Reopening restores stored state without regeneration; there is no automatic save of every guest edit.
- **Account boundaries:** Argon2 password hashing, expiring JWTs, cookie settings, and owner-scoped project operations. The browser receives no database or provider secrets.
- **Public boundaries:** Share publishes one selected saved layout, a preview, title and caption after consent. Source photos, account details, and chat stay private. Unpublishing revokes future public reads.
- **Configurable quotas:** three new AI sessions per identity per UTC day; three actions per session, including initial reconstruction. Internal repairs and clarification answers are not additional actions. Quotas default off in development and on in production, and can be overridden through `GENERATION_QUOTA_ENABLED`. Failures after paid dispatch consume the action.

## API

Interactive OpenAPI documentation is available locally at `http://127.0.0.1:8000/docs`.

| Route family | Purpose |
| --- | --- |
| `GET /api/health` | Service health |
| `/api/rooms/*` | Input limits, normalized upload batches, analysis and construction services |
| `/api/agent-runs/*` | Start/read/execute, clarification answer, cancellation and SSE |
| `/api/agent-runs/{id}/optimizations/*` | Goal improvement and chat refinement execution, results and SSE |
| `/api/scenes/*` | Scene validation/editing services |
| `/api/auth/*` | Registration, sign-in, current account, sign-out |
| `/api/projects/*` | Private saved rooms, images, versions and recovery |
| `GET /api/usage` | Remaining AI allowance |

The guided frontend uses the agent workflow; the lower-level analysis and construction APIs remain service boundaries rather than separate product buttons. Public sharing routes expose a deliberately smaller layout representation than the private project API.

## Run locally

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), PostgreSQL, and Node.js **22.12–22.x or 24+**. Generation needs your own configured OpenAI credentials. Starting the servers and browsing saved data do not themselves trigger model calls.

```sh
git clone https://github.com/asset8k/roometry.git
cd roometry
uv sync --locked
cp -n .env.example .env
```

Set these values in the ignored `.env`:

```dotenv
ANALYSIS_ENABLED=true
OPENAI_API_KEY=your-api-key
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/roometry
JWT_SECRET_KEY=replace-with-a-private-random-secret-of-at-least-32-bytes
GENERATION_QUOTA_ENABLED=false
```

Create the PostgreSQL database separately. For a local installation with PostgreSQL's CLI configured, `createdb roometry` creates it; set a URL matching your actual role and authentication. Apply migrations explicitly, then start the API:

```sh
uv run alembic upgrade head
uv run uvicorn roometry.main:app --reload --host 127.0.0.1 --port 8000
```

In a second terminal:

```sh
cd frontend
npm ci
npm run dev
```

Open `http://127.0.0.1:5173`. The Vite proxy forwards `/api` to the local API. Account configuration is optional for guest-only use; it is required for durable rooms, account checkpoints, sharing, and enforced quotas. Keep existing `.env` files when restarting.

## Configuration and deployment

[`.env.example`](.env.example) documents the complete backend profile; [`frontend/.env.example`](frontend/.env.example) contains only frontend/proxy options. [Development notes](docs/development.md) cover storage, recovery, quotas, and operations.

| Setting | Role |
| --- | --- |
| `ANALYSIS_ENABLED`, `OPENAI_API_KEY` | Explicitly enable paid generation |
| `ANALYSIS_MODEL_NAME`, `SCENE_MODEL_NAME`, `VISUAL_REVIEW_MODEL_NAME`, `OPTIMIZATION_MODEL_NAME` | Override individual stage routes |
| `AGENT_MAX_MODEL_CALLS`, `AGENT_MAX_TOOL_CALLS`, `AGENT_MAX_STEPS` | Whole-run execution limits |
| `SCENE_TIMEOUT_SECONDS`, `ANALYSIS_TIMEOUT_SECONDS` | Provider request deadlines |
| `DATABASE_URL`, `JWT_SECRET_KEY` | Accounts and persistent state |
| `UPLOAD_STORAGE_DIR`, `PROJECT_STORAGE_DIR` | Temporary uploads and, when selected, local saved private photos |
| `PROJECT_STORAGE_BACKEND`, `SUPABASE_URL`, `SUPABASE_STORAGE_BUCKET`, `SUPABASE_STORAGE_SECRET_KEY` | Select and configure backend-only private Supabase photo storage |
| `APP_ENVIRONMENT`, `GENERATION_QUOTA_ENABLED` | Environment profile and quota override |
| `CORS_ORIGINS`, `AUTH_COOKIE_SECURE`, `AUTH_COOKIE_SAMESITE` | Exact frontend origins and cookie policy |
| `VITE_API_BASE_URL` | Browser-visible API base; contains no secret |

The production deployment uses a private Supabase Storage bucket, backend-only secrets, an exact frontend origin, and the backend's `/api` endpoint as the frontend API base. Account cookies use HTTPS. The application uses its own authentication, not Supabase Auth. Cross-origin session behavior has been checked in Safari; broader browser and SSE verification remain in progress.

## Tests and repository structure

```sh
uv run pytest -q
npm --prefix frontend test
npm --prefix frontend run build
```

Automated tests mock model responses. They cover upload validation, graph interruption/resume, replay safety, catalog contracts, component collisions, supports, doors/routes, optimization acceptance, ownership, persistence, publication privacy, quotas, and frontend state transitions. Database integration tests need their configured local PostgreSQL setup; see the test fixtures before supplying a database. Do not use production credentials for tests.

Regenerate shared frontend contracts and model recipes after changing their Python sources:

```sh
npm --prefix frontend run contracts:generate
```

```text
src/roometry/
  agents/           Reconstruction graph, state, questions, budgets and tools
  vision/           Observation, construction, context, visual review and repairs
  optimization/     Goal/chat proposal graph and acceptance policy
  scenes/           Catalog geometry, contracts, edits, support and clearance
  assessment/       Evidence-aware ratings and optimization prerequisites
  mcp/              Catalog server and bounded client
  auth/ projects/   Accounts, private rooms, versions and photos
  quota/            Persistent AI allowances
  evaluation/       Offline matching, metrics, costs and review import
frontend/           React workspace, Three.js renderer, UI and generated contracts
migrations/         Explicit Alembic schema changes
scripts/            Contract export, offline diagnostics and opt-in live verification
data/evals/         Public 25-case dataset, provenance and human judgments
docs/               Screenshots, final evaluation evidence and development guides
```

## Known limitations

- Photo reconstruction is approximate. Occlusion, ambiguous room axes, cross-view identity, dimensions, and fine placements can remain unresolved.
- Catalog models approximate the photographed objects. Sloped ceilings, built-ins, transparent fixtures and detailed materials have representation gaps.
- Collision-free geometry is not photo fidelity. Useful room functions can still be omitted or assigned to the wrong wall.
- Scores are planning proxies or model judgments, not building-code, accessibility, ergonomic, valuation, or safety certification.
- Improvement can return no accepted change. A low rating does not guarantee that the bounded candidate search finds a valid better layout.
- Temporary uploads and guest run state are local to an API instance and can be lost on restart or unavailable to another instance. Registered runs copy inputs to the configured private photo store and persist checkpoints in SQL; existing local files require a separate migration. Horizontal scaling is not established by setting a PostgreSQL URL alone.
- The evaluation is a reused regression set with one owner reviewer and an in-session LLM judge. It is not an independent large-scale human study.

## Sources and licensing

Dataset image provenance, per-image licenses and required attribution are recorded in the [dataset guide](data/evals/GUIDE.md) and [generation manifest](data/evals/generation.json). Generated images and licensed photographs have different provenance; the five photograph licenses remain applicable to their copies. Personal room photos, database exports and account-owned execution records are excluded from Git.

An application-code license has not yet been selected. Dataset image permissions do not imply a license for the application code or endorsement by source photographers.

---

Built by [Asset](https://github.com/asset8k) as an applied AI engineering project: multimodal stateful workflows, tool-backed scene construction, deterministic constraints, and three-layer evaluation around a usable product.
