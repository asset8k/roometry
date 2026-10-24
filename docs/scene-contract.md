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

Users select **1–10 photos**, enter the room type if known, and optionally provide
a description and dimensions, then press **Generate room**. The initial form
submits `general_analysis`; improvement goals, budget and style are reserved for
the later optimization flow. Their optional API fields remain compatible with
existing clients. The frontend uploads internally and starts one owned agent
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

Reconstruction quality metadata is optional for older scenes. `wall_mapping` retains
observed-wall identities; `finishes` records approximate floor/wall colors and a
plain, wood or tile floor. Objects can override wood, metal, linen, inset, rubber
and enamel colors independently. Unknown roles retain catalog defaults.

Photo observations may include evidence-backed facing, wall topology, adjacency,
support and wall coverage. Construction receives the same private normalized
photo evidence, places anchors before accessories and gets one bounded repair. Entered
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
The two bedroom shelf anchors use photo-label evidence to distinguish the window
shelves from desk/display shelving; matching never chooses objects by whether
their candidate positions make a relationship pass. Independent evaluation keeps
its original stricter distance/orientation targets, separate from production's
broader uncertainty bands.

`scripts/live_reconstruction.py --allow-paid` runs one opt-in HTTP generation with
interactive clarification answers and saves private pre/post-review views,
evaluations and provider responses under an ignored artifact directory. Use
`--room-type bedroom` or `living_room` and the actual photo MIME types. Costing
joins provider usage by response ID, includes failed/incomplete output and cache
writes at their total rates, and marks missing usage or unsupported pricing as
unpriced. A write count without a TTL split uses the documented 30m default;
timeouts without returned usage make the reported total a lower bound.

Reconstruction routes its known phases directly through the existing checkpointed
tools by default (`AGENT_DETERMINISTIC_ROUTING=true`): analyze, ask eligible
clarifications, construct/review, finish and assess. It does not pay a model to
choose those predetermined transitions. Interruption/resumption, ownership,
cancellation, expiry and work budgets still apply. Set the flag to `false` to use
the original model-driven tool router. User-triggered optimization retains its
separate bounded proposal/validation/review loop.

`SCENE_COMPACT_CONTEXT=true` deduplicates catalog models, supplies shared default
dimensions and preserves each observed size axis, identity, support and
relationship. Confident variant lists are shortlisted; uncertain alternatives are
retained. Support metadata remains for observed or unknown hosts, and repairs keep
their complete prior scenes and collision neighbors. Request-only `photo_N`
references decode back to the original upload IDs before validation or storage.
Foreign references still fail evidence checks. Compact source-reference schemas
retain the same strict object, wall and measurement constraints.

Analysis and visual review receive every original photo. Construction prefers
`SCENE_PHOTO_TARGET=3` complementary views, retaining more whenever needed to cover
every recorded entity, relationship, wall-span and finish source. Unattributed
evidence falls back to all views; a visual repair also receives its findings'
cited originals. A valid partial scene does not spend another repair solely on
omitted objects with no compatible catalog representation or loose household
accessories. Missing major furniture, openings and geometry failures keep their
repair path. Original unresolved observations remain visible. Already-satisfied
appearance targets do not authorize another paid correction.

`MODEL_PROMPT_CACHE_MODE=explicit` avoids cache writes for these unique dynamic
requests: no cache breakpoints are currently requested. The previous implicit
behavior is configurable; savings never assume a cache hit across users or models.
The provider ledger adds request text-character/image counts, cache mode and
reported cache-write tokens, without logging photos or prompts. These sizes are
diagnostics, not billed token estimates. `scripts/audit_generation_context.py`
compares a saved run offline. The below-$0.10 target requires the final live
reconstruction plus improvement test; it is not a hard dollar cap.

Geometry-valid reconstructions with unresolved fidelity checks remain available as
`partial` previews, even when every object is retained. Such previews carry
`fidelity` issues, may have an empty `unresolved_entities` list, and must not be
labeled `ready`. General observed `wall_id` references are advisory for floor
furniture; only explicit relationships establish precise placement constraints.
Construction failures do not imply that the user's input is missing information.

Recoverable model data is isolated at item boundaries. Invalid optional wall-span,
relationship, duplicate-hint or finish evidence is ignored with analysis warnings;
core observation identities, photo manifests and evidence remain strictly checked.
Malformed poses for known observations become explicit omissions in partial
assembly (or unchanged entries in a repair). Invalid interpretation/appearance
correction groups retain the original hypothesis while unrelated valid groups
continue. Source observations and confirmed measurements are never rewritten.

Visual review validates findings and checklist entries independently. Invalid
items are skipped with visible limitations; missing, duplicate or contradictory
checks become uncertain and cannot authorize repairs. An unsupported identity
difference may concern appearance without implying incorrect placement. Other
validated findings can still use the existing single correction allowance.
Malformed response envelopes, ambiguous core identities, invalid JSON, refusals,
truncation, resource limits and ownership/expiry failures are not locally repaired.
Scene geometry and protected-state checks still govern accepted results; a failed
correction retains the previous valid scene. This recovery does not establish
photo likeness or increase provider-call budgets.

Review checklist entries include bounded photo answers about selected local
relationships and rendered material regions. The model records qualitative
support and material colors from the original photos; the server compares them
with candidate gaps/angles and the actual catalog controls. An overall match vote
cannot conceal a supported, substantially wrong relation or swapped upholstery
colors. Unknown or invalid subcomparisons remain uncertain while valid comparisons
can still authorize the existing bounded correction.
Review value schemas expose hexadecimal color constraints to Structured Outputs;
only floor patterns use `wood`, `tile` or `plain`. Exact unavailable upholstery
patterns remain limitations while useful approximate region colors can be repaired.
Review defaults to low reasoning to leave its bounded output allowance available
for the checklist; construction and repair reasoning settings remain independent.
Opening markers use neutral white doors and blue windows consistently in the
viewer, sidebar and review renders; door colors are defaults, not photo estimates.

Reconstruction relationship checks allow a 0.5 m uncertainty band beyond their
usual near/wall groups and a 15-degree band beyond the 45-degree facing target.
Accepted positional corrections must reach the inner group/facing band; small
cosmetic movements cannot be advertised as fixes. Collision, attachment and
clearance checks use their own unchanged tolerances. Devices follow a corrected
support, preserving measurements, stable identities and locked objects.

`desk_shelved_01` represents a connected metal/wood desk, side shelves and hutch as
one observed piece. Its worktop is the default support for desktop devices, below
the hutch. New analysis should match this connected item across views and preserve
separate freestanding shelves. Existing ambiguous IDs are never silently merged
or removed; their identity issue remains provisional. The shared catalog now has
106 model variants across the existing 68 object types.

After reconstruction and visual review, the read-only `assess_room` node adds an
optional `room_assessment` tied to the final scene hash. Four estimated 0–10 cards
cover space and movement, storage and organization, workspace, and aesthetics and
cohesion. Space uses connected usable floor regions and route checks; storage
combines estimated capacity and front access; workspace uses the actual worktop
and clearance. Missing evidence stays unassessed, and no dedicated workspace is
not applicable rather than a zero. These versioned assessment proxies are
separate from the existing raw layout-scoring diagnostics.

`room-assessment-v2` pairs a working chair with the actual offset catalog worktop
using its size and facing, with a 0.1 m alignment allowance for photo estimates.
Other chairs and obstacles still block the approach; geometry and edit acceptance
are unchanged. Workspace combines worktop usability (4 points), working-side room
(3) and unobstructed approach (3). A blocked approach caps the rating at 5; unusable
worktop height or insufficient room caps it at 3. These are model usability
estimates, not an ergonomics certification. Architectural window sills/ledges
identified by observation labels do not count as storage furniture. Legacy v1
cards remain readable; comparisons do not subtract scores across policy versions.

Aesthetics uses one bounded, low-reasoning Luna request with the existing private
photos and a fixed five-part rubric. Its attempt and result are cached under the
same input ownership and expiry; polling and checkpoint resumption do not repeat
it. Usage shares the run budget. A failed or unavailable visual rating preserves
the measured cards and reconstruction. Unresolved evidence lowers confidence only
in affected categories; a missing wall detail does not invalidate independent
photo aesthetics. Missing major floor furniture or estimated room-side dimensions
limits the geometry categories. Unknown observed workspaces stay unassessed rather
than being described as absent. Only supported, non-low-confidence scores below 8 can be recommended;
the lowest eligible category is selected. Ratings describe the original snapshot,
not subsequent viewer edits.

Improvement actions now collect one selected focus and optional budget, style and
constraints after reconstruction. `POST /api/agent-runs/{source_run_id}/optimizations`
prepares a separate owned `OptimizationRun`; retrying its request UUID with the
same normalized preferences returns that run. Reusing it for different data is a
409. Read and cancel routes append `/{run_id}` and `/{run_id}/cancel`. All use the
existing session, origin checks, JSON limits and no-store responses. Capacity is
bounded; input revocation, parent expiry and cleanup revoke linked requests too.

The server chooses the original scene snapshot, never a client-supplied scene.
The snapshot retains the original assessment and privately keeps observations,
confirmed dimensions, clarification answers, fixed/locked objects and reconstruction
constraints for execution. Partial reconstructions are allowed with their existing
limitations. Viewer edits and legacy upload improvement preferences are not
silently imported. New optional preferences apply only to the improvement request;
budget zero remains zero. Preparing, reading and cancelling make no model calls.
Status `prepared` means saved, not an improved layout. Posting `{}` to
`/{run_id}/execute` runs the separate checkpointed optimization graph once. Duplicate
execute requests read progress or the terminal result; failed/disconnected paid
attempts are not replayed. Progress reads and cooperative cancellation remain free.

The graph proposes small atomic patches, validates components/supports, checks
door/walking-clearance regressions, compares the same 0–10 assessment policy, and
reviews a viable candidate against the retained scene using six diagnostic views.
Supported items follow their parent transforms. Room dimensions, openings,
inventory, assets, sizes, fixed/locked items and confirmed facing constraints are
preserved. Generic photo placement relationships do not lock furniture during
redesign. A measured target must improve by at least 0.2 points and other measured
categories may not fall by more than 0.5. Aesthetics uses a qualitative pairwise
judgment, never an invented numeric score; uncertain or contradicted preference
reviews do not authorize application. Rejected candidates feed the next proposal;
earlier validated gains survive later failures and budget exhaustion.

Defaults allow three candidates, six economical Luna calls and 24 tool calls, with
bounded request/output sizes and per-request deadlines. No photo analysis or Sol
construction call is repeated. One optional read-only MCP search supplies unpriced
catalog context; failure does not block rearrangement of existing furniture.
Purchases, removals, asset swaps and resizing are excluded. A stated purchase
budget also excludes unpriced finish changes; those remain suggestions. Without a
stated budget, supported color overrides are design previews with unknown costs.
Partial inventories retain provisional ratings and visible limitations.

The frontend submits preferences and starts execution from the same user action,
shows real stage/candidate progress, recovers the same run after connection loss,
and displays its outcome and checks. `result_scene` and `result_assessment` are
separate from the immutable original. An explicit `no_improvement` result preserves
the original rather than presenting an unchanged room as improved.

The viewer now offers read-only Original and Improved snapshots plus Your layout,
an independent editable copy. Each source and candidate keeps its own accepted
manual geometry and color previews across view and version switches. Editing a
copy is explicit; reset restores that copy's baseline geometry, preserving color
previews. A newly retained candidate opens automatically unless a drag or edit
request is active; it cannot interrupt that manual operation. Switching views
explicitly cancels pending edits and ignores stale responses. Previous candidates
remain selectable when another focus produces a result. All copies are transient
client state and reset on refresh; they do not start model calls or alter saved
snapshots. Room dimensions remain identical, so camera framing is preserved.

Only terminal runs with an actual retained, accepted change enable Improved.
Failed, budget-limited and cancelled runs can expose their earlier validated best
candidate with an early-stop warning. A no-improvement or unsuccessful run without
a retained candidate leaves the original available. Comparison tables use the
saved 0–10 assessments, mark unknown/not-applicable or provisional categories, and
show deltas only under the same scoring policy. Aesthetics remains qualitative
when no numeric candidate rating exists. An actual scene diff names moved,
rotated and recolored objects, including supported children. Later manual edits
do not silently change these saved-snapshot scores or improvement claims.

The photo aesthetics card exposes its five 0–2 rubric aspects. An aesthetics
optimization can return the same rubric for both retained and candidate designs
inside its existing pairwise review call; no extra model request is made. The UI
labels those figures as a separate rendered-design comparison, not an after-photo
rating or a delta from the original photo score. Invalid optional rubric details
are discarded without losing a valid pairwise judgment or retrying a paid call;
low-confidence comparisons make no numerical improvement claim.

Live verification is opt-in. `scripts/live_reconstruction.py --allow-paid`
accepts local photos, confirmed dimensions, a private output directory and an
independent `--case` annotation. Add `--optimize recommended` (or a category) to
execute exactly one linked improvement after reconstruction in the same session.
Clarifications require explicit answers; reference annotations are used only for
offline evaluation. The harness saves source/candidate diagnostic views, provider
usage and a pricing estimate, and checks snapshot preservation, call limits and
idempotent prepare/execute/read retries. Private artifacts stay ignored by Git.
To validate saved payloads through frontend contracts and comparison components
without more paid calls, run from `frontend/`:
`ROOMETRY_LIVE_ARTIFACTS=/absolute/path/to/artifacts npm test -- src/lib/live-flow.test.tsx`.
This checks component output and comparison state; it is not a browser automation
or a guarantee of improvement on every room.
