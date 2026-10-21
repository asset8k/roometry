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
overlap exceptions. A passing report does not establish photo accuracy, door
swing clearance, walking/headroom clearance, stability, or room-specific quality.

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
