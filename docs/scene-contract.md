# Scene conventions

Python schemas are authoritative; FastAPI exposes the API at `/docs` and
`/openapi.json`. See [development](development.md) for setup, persistence, and tools.

## Sources of truth

| Concern | Implementation |
| --- | --- |
| Scene schema and reference integrity | [scenes/schemas.py](../src/roometry/scenes/schemas.py) |
| Catalog types, dimensions, mountings, solid components, support areas | [scenes/catalog.py](../src/roometry/scenes/catalog.py) and its model modules |
| Catalog MCP tools and client | [mcp/](../src/roometry/mcp/) |
| Geometry, attachments, boundaries, clearance | [scenes/validation.py](../src/roometry/scenes/validation.py) |
| Accepted edits and rollback | [scenes/service.py](../src/roometry/scenes/service.py) |
| Photo evidence and catalog-aware assembly | [vision/service.py](../src/roometry/vision/service.py) and [proposals.py](../src/roometry/vision/proposals.py) |
| Graph state, clarification, and progress | [agents/](../src/roometry/agents/) and [progress.py](../src/roometry/progress.py) |
| Goal-driven improvements and chat | [optimization/](../src/roometry/optimization/) |
| Four user-facing ratings | [assessment/](../src/roometry/assessment/) |
| Offline reconstruction checks | [evaluation/](../src/roometry/evaluation/) |

The procedural catalog is shared by assembly, the MCP server, geometry validation,
and browser/CPU rendering. Asset IDs select approved local models, not arbitrary
URLs or executable code. Nominal catalog dimensions are not photo measurements.

## Coordinates and relationships

- Units are meters and radians in a right-handed, Y-up system.
- The origin is the northwest interior floor corner. X increases west to east;
  Z increases north to south. Wall names are scene labels, not geographic bearings.
- Rectangular interior bounds are `[0, width_m] × [0, height_m] × [0, length_m]`.
- Object `position` is its bottom center. Unrotated local dimensions are X width,
  Y height, Z depth. Local +Z is the functional front.
- Angles normalize into `[0, 2π)`. Camera movement never changes object transforms.
- Opening offsets increase along world X on north/south walls and world Z on
  east/west walls; sill height is measured from the floor.

```text
world_x = position.x + local_x * cos(angle) + local_z * sin(angle)
world_z = position.z - local_x * sin(angle) + local_z * cos(angle)
```

| Placement | Required relationship |
| --- | --- |
| `floor` | Bottom at Y = 0 |
| `surface` | Supporting object and an eligible catalog support area |
| `wall` | Host wall, inward orientation, and back-edge contact |
| `ceiling` | Object top touches the ceiling |

Window coverings reference their opening. Children must fit declared support
areas; catalog mounting configurations supply compatible dimensions. Stable IDs
connect observations, scene instances, support relationships, and later edits.

## Reconstruction and uncertainty

Entered measurements remain authoritative, including individual confirmed axes.
Width/length wall assignments resolve before furniture placement; ambiguous
assignments can trigger a focused three-choice question. The graph asks at most
three clarification questions and resumes using cached photo analysis.

Room scale resolves from supplied values, direct photo estimates, then sufficiently
supported wall-span evidence. If scale remains unknown after the user chooses
estimates, missing axes use explicit assumptions: 4 m width, 4 m length, 2.6 m height.
`estimated_axes` and `assumed_axes` distinguish estimates from these fallbacks.
Neither may silently replace user measurements.

Evidence records wall association, facing, adjacency, support, coverage, and
cross-view identity. Construction receives normalized photos plus observations,
selects suitable catalog variants, and places anchors before accessories. Changes
to uncertain estimates, identities, or appearance retain provenance/corrections;
confirmed measurements and observations remain protected.

Valid subsets can be returned as partial scenes. Unresolved entities retain their
reason, stage, and dependencies; missing supports can defer their children.
Geometry validity alone does not prove photo likeness. Fidelity and visual review
check separate evidence and can retain a usable scene when optional review fails.
Use the offline diagnostics script to inspect omission chains without model calls.

## Geometry, editing, and ratings

Component-aware checks cover rotated containment, solid collisions, support areas,
mountings, opening bounds, and opening conflicts. Empty space between furniture
components is usable; rugs have explicit overlap exceptions.

Door access defaults to an inward 0.8 m access zone. Known inward hinges also get
a 90-degree swing check. Unknown swings remain unassessed. Walking checks use
0.6 m minimum and 0.8 m preferred passages with bounded sampling. Missing entrances
or exhausted checks return unassessed, not an invented clear route. These are
approximate planning checks, not building-code or safety certification.

Existing clearance defects remain viewable. Edits and candidate improvements are
validated against the accepted scene; new blockers or significant route regressions
reject the change atomically. Fixed/locked objects and user-confirmed constraints
remain protected. Moving a support carries its children; rejected edits roll back.
Original and retained Improved versions stay read-only; manual changes use a copy.

User-facing 0–10 ratings cover space/movement, storage, workspace, and aesthetics.
Practical ratings are measured from the modeled layout. Original photo aesthetics
and paired rendered design reviews are distinct evidence and are not subtracted
from one another. Missing evidence is not a zero rating. Ratings follow the selected
saved version; manual edits are not automatically re-rated. Soft scores never
override geometry, ownership, or protected-object checks.

`/api/scenes/score` separately exposes the older 0–100 planning proxies. Those
values are not interchangeable with user-facing cards or photo-fidelity results.

## Generated assets and fixtures

Use `npm --prefix frontend run contracts:generate` after changing Python contracts
or model metadata. It exports schemas/types under `frontend/src/lib/`, procedural
recipes under `frontend/src/data/`, and validated fixtures under
`frontend/src/lib/test-support/fixtures/`. Keep these generated files tracked for
reproducible builds; edit their Python/data sources rather than generated copies.

`data/fixtures/` exercises rendering, geometry, and regressions. `data/evals/`
contains independent reconstruction annotations and reference scenes; the source
room photos remain local and untracked. Neither dataset is a user-facing demo.
