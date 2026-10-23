# Roometry scene conventions

This reference records the conventions shared by scene assembly, geometry tools,
and the React Three Fiber renderer. Python models define the fields and limits;
FastAPI's `/docs` and `/openapi.json` describe the current API.

## Sources of truth

| Concern | Implementation |
| --- | --- |
| Scene format and reference integrity | [`scenes/schemas.py`](../src/roometry/scenes/schemas.py) |
| Approved types, assets, and placements | [`scenes/assets.py`](../src/roometry/scenes/assets.py) |
| Model dimensions, components, support areas | [`scenes/catalog.py`](../src/roometry/scenes/catalog.py) and the furniture/equipment/fixture/decor catalogs |
| Spatial validation | [`scenes/validation.py`](../src/roometry/scenes/validation.py), `collision.py`, and `geometry.py` |
| Edit protection and rollback | [`scenes/service.py`](../src/roometry/scenes/service.py) and `mutations.py` |
| Observation-to-scene assembly | [`vision/proposals.py`](../src/roometry/vision/proposals.py) and `partial.py` |
| Agent state and interruption | [`agents/state.py`](../src/roometry/agents/state.py), `graph.py`, and `clarification.py` |

The catalog contains **100 procedural assets across 68 object types**. Variants
share a semantic type; instance IDs distinguish individual objects using the same
asset. Asset IDs reference approved local models, never arbitrary URLs or code.

## Coordinates and transforms

- Distances are meters; rotations are radians in a right-handed, Y-up system.
- The origin is the northwest interior floor corner. X increases west → east,
  Z increases north → south, and Y increases floor → ceiling. Wall names are
  scene labels, not geographic bearings inferred from photos.
- Rooms are rectangular, including squares, with a flat floor and four walls.
  Interior bounds are `[0, width_m] × [0, height_m] × [0, length_m]`.
- `position` is the object's **bottom center**. `dimensions_m` describes its
  unrotated local width (X), height (Y), and depth (Z). Local +Z is the front.
- Positive Y rotation maps a local footprint point `(u, v)` to:

```text
world_x = position.x + u * cos(angle) + v * sin(angle)
world_z = position.z - u * sin(angle) + v * cos(angle)
```

Stored angles normalize into `[0, 2π)`. Wall opening offsets increase along world
X on north/south walls and world Z on west/east walls; sill heights start at the
floor. Camera movement never changes these coordinates.

## Placement, provenance, and acceptance

`RoomScene` contains `schema_version`, `room`, `openings`, `objects`, `assumptions`,
and `warnings`. Pydantic rejects unknown fields, invalid assets, nonfinite values,
and broken references. Unused required nullable references use explicit `null`.

| Placement | Required relationship |
| --- | --- |
| `floor` | Bottom at Y = 0 |
| `surface` | `support_object_id` plus an eligible catalog support area; assembly binds `support_surface_id` |
| `wall` | Host `wall`, inward orientation, and back-edge contact |
| `ceiling` | Object top touches the ceiling |

Window coverings also reference their opening through `opening_id`. Catalog
placement configurations provide suitable dimensions and support surfaces.
Existing fixture compatibility permits an omitted `support_surface_id`;
validation still requires the child to fit an eligible area.

Preserve stable instance IDs, observations, and user-supplied measurements.
`user_confirmed` labels supplied measurements; entering them requires no extra
confirmation checkbox. Any estimated room axis keeps the aggregate source
`estimated`, with individual confirmed axes retained in run state. Object sizes
also distinguish `catalog` from `estimated` and `user_confirmed` sources.

Geometry policy `components-v1` checks rotated room containment, conservative
solid components, catalog support areas, attachments, opening bounds, and
wall-object/opening conflicts. Furniture may fit into another object's empty
space; a table's entire outer box is not treated as solid. Rugs have explicit
overlap exceptions. `valid` covers these component errors. Door access, swing,
and walking-route findings remain advisory, so poor existing clearance does not
hide a reconstruction. A passing report does not establish photo accuracy,
continuous-space walkability, full headroom, stability, or room-specific quality.

`door_clearance` uses policy `door-clearance-v1`: a full-door-width access zone
extends 0.8 m into the room by default. Catalog solid components between the
configured 0.02 m floor gap and the door head are tested; rugs are excluded.
Known inward hinges use a 90-degree quarter-circle sweep, respecting all four
walls and both offset-based hinge ends. Unknown direction/hinge is unassessed;
outward swings are outside this room's scope. Sliding/non-swinging doors still
get an access check. Results identify the door and blocking objects in structured
warnings and per-door statuses. `/api/scenes/validate` accepts optional
`door_policy` overrides for access depth and floor gap. These are planning
heuristics, not building-code measurements.

Manual and agent pose edits compare clearance against the current accepted scene
using server defaults. New door blockers, lost entrance connections, worse route
statuses, and increased significant disconnected space reject the entire edit,
including carried children. Unknown swings remain unknown. A previously assessed
check becoming unavailable also rejects the edit; an already unassessed baseline
cannot establish route protection. Existing defects and no-op/corrective edits
remain allowed. Disconnected-area increases up to four grid cells (at least
0.05 m²) are ignored as sampling noise. Scores cannot override this policy.
`clearance_regression` edit issues explain rollback separately from component
validity. Graph state enforces the same rule independently of tool dispatch.

`walking_space` uses policy `walking-v1`, with a 0.6 m minimum and 0.8 m preferred
passage width, a 1.8 m upright circular probe, and a requested 0.1 m grid. Policy
values are configurable via `walking_policy` on `/api/scenes/validate`. Collision
parts between the 0.02 m floor gap and probe height are projected into the floor
plan; rugs and higher parts are excluded. Every grid edge and entrance connector
checks continuous clearance against rotated components, so thin obstacles cannot
be jumped and diagonal corners are not cut. Door openings must fit the probe.

Reports identify entrance routes to the largest minimum-width region, connectivity
between known doors, substantial disconnected regions, and regions connected only
at the narrower width. Regions under 0.5 m² of probe-center area do not alone
trigger disconnected/narrow-region warnings. Reported areas describe sampled
probe centers, not floor area or an openness score. Grid steps are reported
explicitly and may coarsen at the 40,000-cell cap. Raster and entrance-connection
work are bounded; missing entrances or exhausted work return `unassessed`, never
invented successful routes. Found routes are conditional on modeled openings;
door leaf motion and external corridors remain outside this route model. A
sampled failure can miss a path between grid points and is an advisory finding.

Soft layout scoring is a separate stateless calculation: `/api/scenes/score`
accepts `scene`, existing `goals`, and optional `policy`, returning
`layout-score-v1`. Scores range from 0 to 100, with formulas, measurements, and
the policy echoed in the response:

- **Openness:** 100 × the sampled unoccupied floor fraction. Standing-height
  component projections are unioned, so stacked/overlapping projections are not
  counted twice. Rugs and higher components are excluded. This is separate from
  entrance reachability; grid steps and the bounded work limit are explicit.
- **Storage:** 100 × V / (V + reference), with a default 1 m³ reference. V sums
  nominal outer volumes of declared standalone storage types; surface containers
  are excluded. This is a size proxy, not verified internal or accessible capacity.
- **Workspace:** the best declared desk's capped usable-width/depth ratios,
  using catalog support surfaces. Default minima are 1 × 0.5 m, with a worktop
  height of 0.6–0.9 m and 0.6 × 0.8 m chair space. Blocked/out-of-room approach
  space or an unsuitable height gives zero. One fitting chair facing the desk
  (or a stool) is permitted, including up to 0.25 m of tucking. Per-desk findings
  identify dimensions and blockers. Desktop clutter, ergonomics, and routes from
  an entrance to the desk are not assessed by this local proxy.
- **Aesthetics:** unassessed; geometry and free-text style do not establish
  measurable taste criteria.

Requested measurable criteria use equal weights. Any selected unassessed
criterion keeps the overall score null, including general analysis, which selects
all four. Invalid component geometry receives no scores. Clearance warnings stay
separate and cannot be cancelled by a high soft score. These scores neither
authorize deleting observed/locked furniture nor establish reconstruction fidelity.
The agent can inspect `validate_scene` and `score_layout` once per accepted scene
revision. Construction feedback includes clearance immediately. The viewer checks
the current accepted scene (including base/working switches and reset), never a
drag draft or color preview. It shows door/route warnings, selected goal proxies,
unknowns and resolution; unavailable checks do not hide the room. Assessment is a
deterministic local request, not an additional model call. Autonomous goal-driven
redesign and optimization remain separate future work.

Partial proposals retain validated objects and explicitly list unresolved
observations outside the scene. An incomplete result must not invent successful
geometry. Fixed and locked objects remain protected by authorized run state and
edit services; the stateless preview endpoint alone cannot establish ownership.

## Current user flow and lifecycle

Users select **1–10 photos**, optionally enter room details, then press
**Generate room**. The frontend uploads internally and starts one owned agent
run. The graph analyzes photos together, builds a base scene, and can inspect,
validate, or correct it using bounded tools. It can interrupt for up to three
questions with three choices each, then resume without repeating photo analysis.
This stage reconstructs the observed room; goal-driven optimization comes later.

Completed and clearly labeled partial scenes open in the editable viewer.
**Base scene** stays read-only; **Your layout** preserves working edits when
switching. Movable floor objects can move/rotate and carry supported children.
Rejected geometry rolls back; color previews remain independent. Reset layout
restores the run's initial working geometry. Refresh clears the browser state.

Uploads are validated, stripped of metadata, and normalized to private JPEGs.
The local adapter and graph checkpoints support **one worker**, remain temporary,
and are lost on restart/reload. Resources become inaccessible at expiry; periodic
cleanup removes files. Owner checks apply to inputs, analysis, and agent runs.
Read-only status polling does not execute the graph. Duplicate start/answer
requests use idempotent handling; cancellation during provider calls is cooperative.
PostgreSQL-backed persistence, durable checkpoints, and SSE remain future work.

## Catalog MCP boundary

The agent's `search_furniture`, `get_furniture_details` and
`list_furniture_categories` tools use the co-located stdio MCP process. They
replace the former direct `search_catalog` lookup. Assembly and rendering retain
the same shared catalog; reads do not add/replace objects or authorize edits.
Only catalog filters/asset IDs cross this boundary, with no photos, room inputs,
provider credentials or model-chosen subprocess commands. The client discovers
and checks the three read-only tools, validates their input/output schemas and
local result contracts, and rejects wrong identities or unapproved assets.

A process is started lazily per interaction and closed in the same async task,
including cancellation. At most two interactions per app run concurrently. The
configurable `MCP_FURNITURE_TIMEOUT_SECONDS` (default 8) covers waiting, startup,
discovery and invocation; response payloads are limited by
`MCP_FURNITURE_MAX_RESPONSE_BYTES` (default 65536). Transport remains `stdio`.
App health/startup does not require a running catalog process. No service needs
starting separately. Host subprocess support must still be verified at deployment.

Every attempted catalog read consumes the existing tool budget, never a provider
call. Checkpointed `catalog_reads` preserves duplicate/limit handling across
clarification, edits and truncated feedback history: up to four searches, eight
item lookups and one category listing per run. Duplicate arguments reuse feedback
without repeating MCP. A not-found item allows a corrected lookup. A transport,
timeout or invalid-result failure disables catalog reads for that run, produces
safe feedback and a viewer notice, and preserves any validated reconstruction.
There is no silent local-query fallback or automatic model retry. Only data—not
MCP handles or child processes—is checkpointed.

## Generated files and verification

Run `npm run contracts:generate` from `frontend/` after changing shared Python
contracts or catalog metadata. It exports only the contracts used by the current
frontend, catalog recipes under `frontend/src/data/`, and validated samples under
`frontend/src/lib/test-support/fixtures/`. Generated files should not be edited
by hand. Samples test rendering and geometry; they are not user/demo workspaces
or reconstruction benchmarks.

Run `uv run pytest` at the repository root, then `npm test` and `npm run build`
in `frontend/`. Model requests are mocked in automated tests. Live provider tests
require a separate, explicitly authorized run.

Reconstruction quality metadata is optional for older scenes. `wall_mapping` retains
observed-wall identities; `finishes` records approximate floor/wall colors and a
plain, wood or tile floor. Objects can override wood, metal, linen, inset, rubber
and enamel colors independently. Unknown roles retain catalog defaults.

Photo observations may include evidence-backed facing, wall topology, adjacency,
support and wall coverage. Construction receives the same private normalized
photos, places anchors before accessories and gets one bounded repair. Entered
room measurements remain authoritative. Changed photo size estimates require
`estimate_corrections` with exact before/after values, source views and a visual
reason; observations remain unchanged. User identity/facing answers are stored as
separate reconstruction constraints and reuse cached analysis.

Observed walls can carry shorter/longer-span evidence with confidence and source
views. Before construction, `wall_axes` resolves physical walls to `width_m`
(north/south) or `length_m` (east/west), without sorting or swapping entered
dimensions. An ambiguous rectangular room gets one three-choice wall question;
square rooms do not need it. The answer overrides photo hypotheses and is retained
separately from observations. Construction's strict schema restricts wall choices
to the resolved axis, assembly checks them before furniture, and scenes retain
`wall_axis_bindings` for validation. “Not sure” or an exhausted question allowance
keeps orientation explicitly estimated and any usable scene provisional.

Proposal `fidelity` compares coverage and recorded relationships/proportions/colors
separately from geometry. Unknown evidence is unassessed, not a successful check.
A failed required relationship triggers repair rather than deleting the furniture
to hide the mismatch. Partial scenes disclose missing anchors. Independent photo
annotations in `data/evals/` catch omissions from analysis itself; reference scenes
are hand-authored test arrangements, not evidence of live model accuracy. Run
`scripts/run_evals.py --case data/evals/bedroom_multiview.json --run /tmp/run.json`
against a saved agent response. Personal source photos are not copied into fixtures.

Geometry-valid reconstructions with unresolved fidelity checks remain available as
`partial` previews, even when every object is retained. Such previews carry
`fidelity` issues, may have an empty `unresolved_entities` list, and must not be
labeled `ready`. General observed `wall_id` references are advisory for floor
furniture; only explicit relationships establish precise placement constraints.
Construction failures do not imply that the user's input is missing information.
