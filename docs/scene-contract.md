# Roometry Scene Contract

Status: version 1 Pydantic schemas, approved asset metadata, a JSON fixture, generated TypeScript types, a furnished viewer with eight procedural models, local color previews, and structural/model tests are implemented. Most of the broader model catalog, physical geometry validation, and scene-edit persistence/enforcement are not implemented yet.

## Purpose

A scene is a JSON description of one approximate room, its openings, furniture, fixtures, appliances, electronics, and decor. Photo analysis and the agent produce data in this format; deterministic backend tools validate it; the browser renders and edits it.

The scene contains data only. It must not contain executable code, arbitrary asset URLs, private image references, chat transcripts, or graph checkpoints. Ordinary photos do not establish precise measurements. Estimated geometry must remain identifiable.

## Coordinate system

| Property | Convention |
| --- | --- |
| Linear units | Meters |
| Angular units | Radians |
| Axes | Right-handed, Y up |
| Origin | Northwest interior floor corner, `(0, 0, 0)` |
| X | Width, increasing from west to east |
| Y | Elevation, increasing from floor to ceiling |
| Z | Length, increasing from north to south |
| Floor | `y = 0` |
| Interior bounds | `0 ≤ x ≤ width_m`, `0 ≤ y ≤ height_m`, `0 ≤ z ≤ length_m` |

North, south, east, and west are scene labels, not geographic bearings inferred from photos. Camera movement does not change these axes.

The MVP room is rectangular with a flat floor and four vertical walls. Dimensions describe the usable interior; wall thickness is a rendering detail outside that interior.

## Object transforms

`position` is the center of the object's horizontal footprint at the bottom of the object, in world coordinates. Floor objects use `position.y = 0`; tabletop, wall-mounted, and ceiling-mounted objects may have positive Y positions. Placement rules below determine which positions are valid.

`dimensions_m.width`, `height`, and `depth` describe the object's unrotated local X, Y, and Z extents. Rotation changes its placement, not these dimensions.

At `rotation_y_rad = 0`, local axes align with room axes. Positive rotation follows the right-hand rule around positive Y. A local point `(u, v)` in the X/Z footprint transforms to:

```text
world_x = position.x + u * cos(angle) + v * sin(angle)
world_z = position.z - u * sin(angle) + v * cos(angle)

-width / 2 ≤ u ≤ width / 2
-depth / 2 ≤ v ≤ depth / 2
```

For example, a positive quarter turn maps local positive X toward room negative Z. Accept finite angles and normalize stored angles into `[0, 2π)` without rounding them to whole degrees.

A renderer can place a centered box inside an object group at local `(0, height / 2, 0)`, then position and rotate the group using the scene transform. All assets must use the same bottom-center pivot and declared dimensions. The local positive Z face is the front for directional assets such as TVs and pictures.

Containment checks use all four rotated footprint corners and the vertical interval from `position.y` to `position.y + height`. Checking only its center is insufficient.

## Scene structure

All fields listed below are required, including nullable fields: use explicit JSON `null` for an unused reference. Empty collections are valid; absent collections are not. Unknown fields are rejected to catch mistakes early.

Instance/reference IDs use lowercase letters, digits, and underscores, start with a letter, and have at most 64 characters. Asset IDs follow the same format with at most 96 characters and must appear in `src/roometry/scenes/assets.py`. Numeric JSON integers are accepted for numeric measurements, but booleans and numeric strings are rejected. Stored colors are normalized to uppercase hexadecimal.

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | Integer, exactly `1` | Format version |
| `room` | Room object | Interior dimensions and measurement source |
| `openings` | Array of opening objects | Doors and windows |
| `objects` | Array of scene objects | Furniture, fixtures, appliances, electronics, and decor |
| `assumptions` | Array of strings | Explicit modeling assumptions |
| `warnings` | Array of strings | User-facing limitations or unresolved issues |

### Room

| Field | Type | Meaning |
| --- | --- | --- |
| `room_type` | Approved room category | `bedroom`, `living_room`, `kitchen`, `bathroom`, `office`, `dining_room`, `entryway`, `laundry_room`, or `other` |
| `width_m` | Positive finite number | Interior X extent |
| `length_m` | Positive finite number | Interior Z extent |
| `height_m` | Positive finite number | Floor-to-ceiling Y extent |
| `measurements_source` | `user_confirmed` or `estimated` | Source of the dimensions |

Use `user_confirmed` only when the user supplied or confirmed all three dimensions. If any dimension is estimated, use `estimated` and identify the estimated dimensions in `assumptions`. Preserve any individually confirmed values during subsequent edits even when this aggregate source is `estimated`.

User confirmation is a source label, not a certification of measurement accuracy. Confirmed values and constraints must also remain in authorized run/project state so an agent cannot silently overwrite them by changing JSON.

#### Room types

Every room type uses the same scene geometry and object contract. `room_type` guides analysis, suggestions, and layout scoring; it does not limit which categories may appear. For example, a living room may contain a desk, and a bathroom may contain a washing machine. Use `other` when the purpose is unknown or mixed, and clarify only when it affects the requested improvement.

| Room | Typical objects and improvement focus |
| --- | --- |
| Bedroom | Bed, wardrobe, nightstand, dresser, desk, mirror, window covering, laundry basket; storage and usable floor space |
| Living room | Sofa, armchair, coffee/side table, TV, console, projector, shelf, rug, lamp; seating and circulation |
| Kitchen | Cabinets, counters or island, sink, refrigerator, cooker or integrated hob/oven, microwave, dishwasher, bin; access and storage around fixed installations |
| Bathroom | Toilet, sink or vanity, bathtub, shower, mirror, towel rack, bin, basket, washing machine, water heater; access and storage around fixed plumbing |
| Office | Desk, chair, desktop or laptop, monitor, printer, shelf, storage box, lamp; workspace and circulation |
| Dining room | Table, chairs, sideboard, chandelier, rug; seating and circulation |
| Entryway | Shoe rack, bench, mirror, storage box, basket, coat-rack variant; access and compact storage |
| Laundry room | Washing machine, dryer, counter, cabinet, basket, drying rack; access and usable working space |

A nursery or child's room uses `bedroom` with suitable asset variants and the user's goals. A combined kitchen/living room uses one rectangular scene with `other` and a description of its uses. Separate connected rooms, outdoor spaces, and irregular room outlines remain outside this room contract.

Kitchens and bathrooms are approximate visual layouts with conservative checks. The MVP preserves installed fixtures and focuses on movable storage, accessories, and access. Room-specific quality rules must be implemented and evaluated before claiming an optimized kitchen or bathroom. When such rules are unavailable, report a limited analysis rather than claiming complete room-specific validation.

### Scene object

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | Nonempty string | Stable instance identifier, such as `bed_1` |
| `type` | Approved object category | A category from the initial set below |
| `asset_id` | Approved asset identifier | Renderer/catalog entry, never a URL or file path |
| `position` | Object with finite `x`, `y`, `z` | Bottom-center position in meters |
| `rotation_y_rad` | Finite number | Y rotation in radians |
| `dimensions_m` | Object with positive finite `width`, `depth`, `height` | Unrotated extents in meters |
| `color` | String matching `^#[0-9A-Fa-f]{6}$` | Editable primary color, such as `#008000` |
| `placement` | `floor`, `surface`, `wall`, or `ceiling` | How the object is supported |
| `support_object_id` | Object ID; nullable | Required for `surface`; otherwise `null` |
| `wall` | `north`, `south`, `west`, or `east`; nullable | Required for `wall`; otherwise `null` |
| `opening_id` | Opening ID; nullable | Required for `window_covering`; otherwise `null` |
| `fixed` | Boolean | Installed fixture whose placement, dimensions, and existence the optimizer must preserve |
| `locked` | Boolean | Prevents optimizer changes to placement, appearance, dimensions, or existence |
| `provenance` | `photo_observed`, `user_added`, or `agent_added` | How the instance entered the scene |
| `measurements_source` | `user_confirmed`, `catalog`, or `estimated` | Source of its dimensions |

`id` identifies an instance; `asset_id` identifies its visual template. Two chairs may share an asset while retaining different instance IDs. Preserve IDs when moving or rotating existing furniture.

#### Initial object categories and simple models

These are planned supported categories, not implemented assets. Start with one recognizable low-poly model per category, with a second picture shape and a few universal souvenir shapes if useful. Do not create a separate mesh for each color or generate new models during user requests.

| Group | Categories | Allowed placement |
| --- | --- | --- |
| Seating and sleeping | `bed`, `sofa`, `armchair`, `chair`, `stool`, `bench`, `ottoman` | Floor |
| Tables | `desk`, `table`, `coffee_table`, `side_table`, `nightstand` | Floor |
| Storage | `wardrobe`, `dresser`, `shelf`, `tv_stand`, `shoe_rack`, `sideboard` | Floor |
| Wall storage | `wall_shelf` | Wall |
| Portable storage | `storage_box`, `basket` | Floor or surface |
| Waste | `bin` | Floor |
| Electronics | `computer`, `laptop`, `monitor`, `printer`, `game_console`, `speaker` | Surface; speakers may also use floor placement |
| Display | `tv` | Surface or wall |
| Projection | `projector` | Surface or ceiling |
| Floor covering | `rug` | Floor |
| Wall decor | `picture`, `clock` | Wall |
| Window treatments | `window_covering` | Wall, attached to a window reference |
| Small decor | `souvenir`, `vase`, `book_stack` | Surface |
| Small household items | `household_accessory` | Floor, surface, or wall, according to the approved asset |
| Mirror | `mirror` | Floor or wall |
| Plants | `plant` | Floor or surface |
| Lighting | `floor_lamp`, `table_lamp` | Floor for floor lamps; surface for table lamps |
| Overhead fixtures | `chandelier`, `ceiling_light`, `ceiling_fan` | Ceiling |
| Heating and cooling | `radiator`, `air_conditioner`, `fan` | Wall for radiators and air conditioners; floor or surface for portable fans |
| Kitchen storage | `kitchen_cabinet`, `kitchen_counter` | Floor |
| Kitchen wall fittings | `wall_cabinet`, `extractor_hood` | Wall |
| Large appliances | `refrigerator`, `cooker`, `dishwasher`, `washing_machine`, `dryer` | Floor |
| Small appliances | `microwave`, `kettle`, `toaster` | Surface |
| Plumbing fixtures | `sink`, `toilet`, `bathtub`, `shower` | Floor; sinks and toilets may also use wall placement |
| Bathroom storage | `vanity` | Floor or wall |
| Bathroom accessories | `towel_rack` | Wall |
| Water heating | `water_heater` | Wall |
| Laundry accessories | `drying_rack` | Floor |

`rug` includes the loose carpets users place in a room. `computer` starts as one simplified desktop setup; separate keyboard, mouse, and cable objects are unnecessary. Pictures can use rectangular and square frames with a plain colored insert. TVs and computers need only basic recognizable shapes.

`game_console` covers PlayStation, Xbox, and similar devices using one generic console shape; brand-specific meshes and logos are unnecessary. `souvenir` uses a few reusable assets such as a small figurine, ornament, or trophy. Store the visual variant in `asset_id`, not in a separate type for every personal possession. Shelf decor initially sits on the shelf unit's approved top surface; modeling individual internal shelf levels is deferred.

#### Shared types and reusable variants

Keep semantic types useful for layout rules, while sharing geometry between them. For example, a wardrobe and a refrigerator can share a box-based model family with different proportions and front details. A new category does not require a unique complex mesh.

| Existing category | Variants it can cover |
| --- | --- |
| `bed` | Single/double bed, crib; dimensions and simple rails distinguish a crib |
| `chair`, `stool` | Dining/office chair, kitchen/bar stool |
| `table` | Dining table or compact general-purpose table |
| `shelf`, `wall_shelf` | Bookcase, freestanding coat rack, or simple mounted shelf; coat-rack assets do not supply a support surface |
| `storage_box`, `basket` | Storage chest, toy box, laundry hamper, or small organizer |
| `kitchen_counter` | Straight counter, island, or counter with an integrated sink/hob |
| `kitchen_cabinet` | Base cabinet or a unit with a built-in oven |
| `window_covering` | Curtain panel or blind, using simple flat geometry |
| `household_accessory` | Dish/bowl, utensil holder, soap dispenser, toothbrush cup, toilet-paper holder, toilet brush, or small storage caddy |

`household_accessory` covers recognized small items with approved assets and placements. It is not an unrestricted category for arbitrary geometry or unidentified large obstacles. Keep uncertain large objects in observations until they can be identified or represented by an approved category.

Do not reconstruct every plate, book, toy, or bottle. Group similar small items when they do not affect layout. Prioritize objects that affect space, access, the user's constraints, or room recognition. Preserve meaningful observed personal items without inventing additional decor.

A chandelier needs only a stem and a few simple light shapes. A projector is a small box with a lens and, for ceiling placement, a mount. Ceiling fixture dimensions include the full mount, cord, or suspension so the bounding volume reaches the ceiling.

Composite assets prevent double counting and artificial overlaps: a `vanity` includes its basin, a `cooker` includes its oven and hob, and a floor-standing `sink` includes its base or pedestal. Integrated kitchen counter/cabinet variants include their sink, hob, or built-in oven. Do not also create those included parts as intersecting independent objects. Kitchens may use multiple independent rectangular cabinet/counter units to approximate an L-shaped arrangement inside a rectangular room. A stacked washer/dryer can initially use one composite appliance asset with the full stack dimensions; arbitrary object stacking remains deferred.

The category and asset registry may grow without changing this object structure. Each supported asset declares its category, permitted placements, colorable materials, fixture defaults, and whether it supplies a usable support surface. Add assets incrementally; this list does not require building every model before the first room renderer works. A simple category-specific placeholder can represent an item with implemented placement/validation rules while detailed assets are deferred.

#### Balanced implementation scope

This planned vocabulary covers 68 semantic categories across the room types above. It is a coverage target, not a requirement for 68 handcrafted meshes or 68 purchasable catalog entries. Share procedural model families, and keep the first curated MCP catalog to approximately 20–50 useful items. Observed fixtures and appliances may have renderer assets without being offered as purchases.

Build the renderer in passes:

1. Structural and major space-occupying objects: room/openings, main furniture, kitchen units, bathroom fixtures, and large appliances. Use recognizable primitives initially.
2. Common context: electronics, lamps, rugs, window coverings, heating/cooling, and laundry/storage objects.
3. Small decor: a few reusable souvenir and household-accessory models.

Use a small fixture scene for each named room type to check coverage before claiming support. Each should render its main objects, preserve fixed items, and exercise relevant geometry checks. Fewer well-validated shapes are preferable to a large set of visually detailed assets with inconsistent dimensions or placement rules.

#### Colors

Every object has its own `color`, independent of its asset ID. For example, the same TV asset can appear gray in one scene and black in another. Apply the color to the main material or designated colorable parts; screens, glass, and small details may retain neutral materials so the object remains recognizable.

The initial color model uses one primary color per object. Textures, arbitrary image uploads for picture frames, and separate per-part color controls are deferred. Color changes are deterministic manual edits, require no LLM call, and are preserved in later AI refinements unless the user asks to change them.

The current viewer provides local, unsaved color previews for unlocked objects, including fixed fixtures. Preview overrides do not mutate the source fixture and disappear on refresh. Persisting these edits and preserving them across AI refinements require the later scene-edit/refinement APIs. The viewer's asset registry maps stable IDs to normalized model families; approved assets without a visual model use a dimensioned bounding-box fallback.

#### Placement and support

- **Floor:** `position.y = 0`, `support_object_id = null`, and `wall = null`.
- **Surface:** Reference an existing floor or wall object with an approved flat support surface. The child's `wall` and the support object's own `support_object_id` must be `null`. Initially, eligible supports are desks, tables, coffee/side tables, nightstands, dressers, TV stands, sideboards, shelf units, wall shelves, and plain kitchen counters. Use a simple rectangular top at the support's upper Y boundary. The child's bottom must touch that top, and its rotated footprint must fit entirely on it. Do not treat basins, cookers, or integrated sink/hob counters as generic support surfaces. Nested stacking is deferred; surface objects cannot support other surface objects.
- **Wall:** Set `wall`, keep `support_object_id = null`, and place the object inside the room with its back touching the specified wall. Only Y rotation is supported; pictures and TVs stay vertical. Require full room containment and no intersection with doors or windows on that wall, except the explicit window-covering rule below.
- **Ceiling:** Set `support_object_id = null` and `wall = null`. The full asset's top touches the ceiling, so `position.y = room.height_m - dimensions_m.height`. Require nonnegative Y and full containment. Y rotation is allowed; arbitrary tilt and projector beam simulation are deferred.

For wall objects, face into the room: north uses rotation `0`, east `3π/2`, south `π`, and west `π/2`. Offset the footprint center inward by half its depth from the host wall. For example, a north-wall picture with depth `0.04` has `position.z = 0.02`. Its `position.y` is the bottom edge's elevation.

A `window_covering` references an existing window through `opening_id` and uses the same host wall. Its approved asset may overlap that window's opening area, but never a door or unrelated opening. Validate room bounds and solid-object collisions as usual. Treat it as a thin decorative layer; opaque panels may affect qualitative daylight/privacy suggestions, but they do not fill the window opening with a solid wall. Fixed rails/attachments are included in the asset; cloth movement is not simulated. All other categories set `opening_id = null`.

Surface object positions remain in world coordinates. Moving or rotating a supporting object must transform its children together and revalidate the result. Reject a change that would move a locked or fixed child. Reject removing a support until its children are explicitly relocated or removed. Deleting a desk must not silently delete its computer. Explicit user corrections to window geometry must also update and validate attached coverings atomically; reject deleting a window while a covering still references it.

Placement permissions and collision behavior come from the trusted registry, not an agent-supplied flag. Rugs are thin decorative floor layers: they can overlap furniture footprints, are traversable for the MVP, and do not count as solid obstacles. Their bounds still require validation. Only one rug layer is supported; overlapping rugs are rejected. Rug thickness is a visual approximation, not a trip-hazard assessment.

Overhead objects must not become floor obstacles solely because their X/Z footprints overlap a walking route. Clearance checks consider their vertical extent and a configurable headroom policy, and disclose limitations. A chandelier can be above a table if their solid volumes do not intersect and the configured clearance rules pass.

#### Fixed fixtures and user locks

`fixed` describes an installed object; `locked` describes the user's edit restriction. They are independent. A fixed fixture can have an editable color while its placement remains protected. A locked object protects appearance as well.

Mark observed plumbing fixtures, installed cabinets/counters, built-in or connected appliances, ceiling fittings, heating/cooling installations, water heaters, window-covering attachments, and mounted fittings as fixed by default. Freestanding refrigerators, washing machines, and similar connected appliances should also remain fixed during optimization unless the user explicitly identifies them as movable. If installation status is uncertain, preserve placement and ask only when moving it is necessary to the user's goal.

Enforce fixed status against authorized prior state and trusted asset rules. The agent must not turn `fixed` off, remove a fixture, or propose moving plumbing or electrical connections to improve a score. Users may explicitly correct a fixture's estimated location/dimensions in the model; that is a scene correction, not a recommendation to physically relocate it. Fixture corrections require validation and a new revision.

Room-specific plumbing, electrical, ventilation, waterproofing, projector optics, and installation engineering are outside the MVP. Render and preserve these objects without inventing verification for systems that are not modeled.

Provenance and measurement source are separate: a photo-observed bed may still have estimated dimensions. `catalog` means curated catalog dimensions, not measurements inferred from a photo. Added catalog furniture must match the selected entry's dimensions; observed furniture may use an approved scalable template to represent its own estimated or confirmed dimensions.

Asset IDs must match the category in the approved asset registry. The renderer should display a simple bounding-box fallback if an approved asset fails to load. Unknown IDs from user/model input must be rejected by the backend.

Locks are enforced against the previous authorized scene, not just the incoming `locked` flag. The agent cannot unlock objects to bypass a constraint. An explicit user action may unlock one. Manual edits must preserve the latest user-approved state when a refinement starts.

### Opening

Openings attach to walls; they have no free-standing furniture transform.

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | Nonempty string | Stable opening identifier |
| `type` | `door` or `window` | Opening category |
| `wall` | `north`, `south`, `west`, or `east` | Host wall |
| `offset_m` | Nonnegative finite number | Distance to the opening's first edge along the wall axis below |
| `width_m` | Positive finite number | Horizontal opening span |
| `height_m` | Positive finite number | Vertical opening span |
| `sill_height_m` | Nonnegative finite number | Bottom of opening above floor; `0` for doors |
| `swing` | `inward`, `outward`, `none`, or `unknown` | Door behavior; `none` for windows |
| `hinge` | `min_offset`, `max_offset`, or `unknown`; nullable | Door hinge edge; `null` for windows and doors with `swing = none` |
| `provenance` | `photo_observed` or `user_added` | Observation or explicit user input |
| `measurements_source` | `user_confirmed` or `estimated` | Source of opening dimensions and placement |

Offsets always follow increasing world coordinates, including on opposite walls:

| Wall | Fixed coordinate | Offset direction | Available length |
| --- | --- | --- | --- |
| North | `z = 0` | Increasing X from `x = 0` | `room.width_m` |
| South | `z = room.length_m` | Increasing X from `x = 0` | `room.width_m` |
| West | `x = 0` | Increasing Z from `z = 0` | `room.length_m` |
| East | `x = room.width_m` | Increasing Z from `z = 0` | `room.length_m` |

`offset_m` locates an edge, not the center. The horizontal center is at `offset_m + width_m / 2`. The vertical center is at `sill_height_m + height_m / 2`.

An opening must fit horizontally within its wall and vertically below the ceiling. Openings on the same wall must not overlap in both horizontal and vertical extent.

Do not infer a hinge or swing when it is unclear: use `unknown` and add a warning or request clarification. Initial clearance checks may use a conservative interior zone instead of an exact swing arc. Unknown swing geometry must not be reported as verified. Clearance distances and approximations belong to documented validator policy, not claims of building-code compliance.

Doors with `swing = inward`, `outward`, or `unknown` require a hinge edge or `unknown`. Doors with `swing = none`, such as a simplified sliding door, require `hinge = null`.

Optimization must preserve openings. Correcting one requires explicit user input and a new validated scene version.

## Example scene

The asset identifiers below are proposed procedural asset names, not already implemented registry entries. The example includes a green rug, yellow clock, gray wall-mounted TV, and black desktop computer.

```json
{
  "schema_version": 1,
  "room": {
    "room_type": "bedroom",
    "width_m": 4.0,
    "length_m": 3.5,
    "height_m": 2.6,
    "measurements_source": "user_confirmed"
  },
  "openings": [
    {
      "id": "door_1",
      "type": "door",
      "wall": "north",
      "offset_m": 0.3,
      "width_m": 0.8,
      "height_m": 2.1,
      "sill_height_m": 0.0,
      "swing": "inward",
      "hinge": "min_offset",
      "provenance": "photo_observed",
      "measurements_source": "estimated"
    },
    {
      "id": "window_1",
      "type": "window",
      "wall": "east",
      "offset_m": 1.0,
      "width_m": 1.2,
      "height_m": 1.1,
      "sill_height_m": 0.9,
      "swing": "none",
      "hinge": null,
      "provenance": "photo_observed",
      "measurements_source": "estimated"
    }
  ],
  "objects": [
    {
      "id": "bed_1",
      "type": "bed",
      "asset_id": "bed_standard_01",
      "position": { "x": 2.7, "y": 0.0, "z": 2.0 },
      "rotation_y_rad": 0.0,
      "dimensions_m": { "width": 1.6, "depth": 2.0, "height": 0.55 },
      "color": "#808080",
      "placement": "floor",
      "support_object_id": null,
      "wall": null,
      "opening_id": null,
      "fixed": false,
      "locked": true,
      "provenance": "photo_observed",
      "measurements_source": "estimated"
    },
    {
      "id": "rug_1",
      "type": "rug",
      "asset_id": "rug_standard_01",
      "position": { "x": 2.0, "y": 0.0, "z": 1.8 },
      "rotation_y_rad": 0.0,
      "dimensions_m": { "width": 2.6, "depth": 2.8, "height": 0.01 },
      "color": "#008000",
      "placement": "floor",
      "support_object_id": null,
      "wall": null,
      "opening_id": null,
      "fixed": false,
      "locked": false,
      "provenance": "photo_observed",
      "measurements_source": "estimated"
    },
    {
      "id": "desk_1",
      "type": "desk",
      "asset_id": "desk_standard_01",
      "position": { "x": 0.65, "y": 0.0, "z": 2.0 },
      "rotation_y_rad": 0.0,
      "dimensions_m": { "width": 1.0, "depth": 0.6, "height": 0.75 },
      "color": "#B08968",
      "placement": "floor",
      "support_object_id": null,
      "wall": null,
      "opening_id": null,
      "fixed": false,
      "locked": false,
      "provenance": "photo_observed",
      "measurements_source": "estimated"
    },
    {
      "id": "computer_1",
      "type": "computer",
      "asset_id": "computer_standard_01",
      "position": { "x": 0.65, "y": 0.75, "z": 2.0 },
      "rotation_y_rad": 0.0,
      "dimensions_m": { "width": 0.55, "depth": 0.3, "height": 0.45 },
      "color": "#000000",
      "placement": "surface",
      "support_object_id": "desk_1",
      "wall": null,
      "opening_id": null,
      "fixed": false,
      "locked": false,
      "provenance": "photo_observed",
      "measurements_source": "estimated"
    },
    {
      "id": "tv_1",
      "type": "tv",
      "asset_id": "tv_standard_01",
      "position": { "x": 1.0, "y": 1.1, "z": 3.46 },
      "rotation_y_rad": 3.141592653589793,
      "dimensions_m": { "width": 1.0, "depth": 0.08, "height": 0.6 },
      "color": "#808080",
      "placement": "wall",
      "support_object_id": null,
      "wall": "south",
      "opening_id": null,
      "fixed": true,
      "locked": false,
      "provenance": "photo_observed",
      "measurements_source": "estimated"
    },
    {
      "id": "clock_1",
      "type": "clock",
      "asset_id": "clock_standard_01",
      "position": { "x": 2.3, "y": 1.6, "z": 0.025 },
      "rotation_y_rad": 0.0,
      "dimensions_m": { "width": 0.3, "depth": 0.05, "height": 0.3 },
      "color": "#FFFF00",
      "placement": "wall",
      "support_object_id": null,
      "wall": "north",
      "opening_id": null,
      "fixed": true,
      "locked": false,
      "provenance": "photo_observed",
      "measurements_source": "estimated"
    }
  ],
  "assumptions": [
    "Door and window dimensions and positions are estimated from photos.",
    "The door is modeled as inward-opening with a hinge at the smaller offset.",
    "Object dimensions, positions, and colors are illustrative estimates; verify dimensions before planning purchases."
  ],
  "warnings": [
    "The scene is approximate. Confirm opening geometry and furniture dimensions in the room."
  ]
}
```

For a ceiling-mounted object in the example room, this chandelier's top is at `1.9 + 0.7 = 2.6` meters:

```json
{
  "id": "chandelier_1",
  "type": "chandelier",
  "asset_id": "chandelier_standard_01",
  "position": { "x": 2.0, "y": 1.9, "z": 1.75 },
  "rotation_y_rad": 0.0,
  "dimensions_m": { "width": 0.6, "depth": 0.6, "height": 0.7 },
  "color": "#D4AF37",
  "placement": "ceiling",
  "support_object_id": null,
  "wall": null,
  "opening_id": null,
  "fixed": true,
  "locked": false,
  "provenance": "photo_observed",
  "measurements_source": "estimated"
}
```

This is an object example to include in `objects`, not a complete scene or a claim that headroom has been verified.

## Validation and acceptance

Schema validation and spatial validation are separate mandatory checks. Passing the schema does not establish that a layout fits.

### Implemented structural validation

`RoomScene.model_validate(...)` and `RoomScene.model_validate_json(...)` enforce strict field types, finite numeric values, supported categories/assets, category/asset/placement compatibility, required references, door/window field consistency, unique IDs, and valid support/window links. They also reject nested support chains and support assets without an approved flat top. Rotation and color normalization occur during validation.

Initial structural resource limits are defined in `scenes/schemas.py`:

| Resource | Limit |
| --- | --- |
| Room width / length | Greater than 0, at most 30 m each |
| Room height | Greater than 0, at most 10 m |
| Object dimensions | Greater than 0, at most 30 m per axis |
| Object X / Z coordinates | Between -100 and 100 m; actual room containment is separate |
| Object Y coordinate | Between 0 and 100 m; floor objects require exactly 0 |
| Opening width / offset | At most 30 m; width positive, offset nonnegative |
| Opening height / sill height | At most 10 m; height positive, sill nonnegative |
| Objects / openings | At most 200 objects and 32 openings |
| Assumptions / warnings | At most 50 strings in each collection, each 1–1,000 characters with non-whitespace content |

These are initial resource limits, not geometric acceptance rules or measured room accuracy. Asset metadata declares fixture defaults but does not silently replace explicit `fixed`/`locked` input values. Initial scene creation must apply authorized fixture policy, and later mutations must enforce it against previous authorized state.

The schema does not prove containment, collisions, physical surface contact, wall/ceiling attachment, headroom, budget adherence, or preservation of previous constraints. The baseline geometry checks below cover some of these rules; full acceptance still requires further geometry and scene-mutation services. Revalidate incoming data before each acceptance; these models do not make Python lists or attributes immutable.

`data/fixtures/bedroom_scene.json` includes nine objects: bed, rug, desk, computer, TV, clock, chandelier, window-linked curtain, and wardrobe, covering all four placement modes. The wardrobe completes the Day 2 bed/desk/wardrobe renderer examples. It is a synthetic development fixture, not a reconstruction benchmark or a physically certified layout. Run the structural tests with `uv run pytest`.

### Implemented baseline geometry — Day 3 Step 1

`validate_geometry(scene)` in `src/roometry/scenes/validation.py` revalidates the scene structure and returns a serializable `GeometryReport`. Structural failures raise Pydantic `ValidationError`; spatial failures appear in `report.errors`. The function does not mutate the input. Reports use policy version `baseline-v1`, deterministic entity ordering, and a `valid` value derived from whether the errors collection is empty.

```python
from pathlib import Path
from roometry.scenes.schemas import RoomScene
from roometry.scenes.validation import validate_geometry

scene = RoomScene.model_validate_json(
    Path("data/fixtures/bedroom_scene.json").read_text()
)
report = validate_geometry(scene)
print(report.model_dump_json(indent=2))
```

Reusable calculations in `scenes/geometry.py` use oriented rectangles in X/Z and vertical intervals, matching the renderer's rotation and bottom-center pivot. Collision detection uses the separating-axis test on both objects' local axes rather than world-aligned bounding boxes. Distance/contact tolerance is `0.000001` m. Wall-facing angular tolerance is separately `0.000001` rad, and both ends of the back edge must also touch the wall within the distance tolerance.

Each error has `code`, `entity_ids`, and `message`:

| Code | Baseline rule |
| --- | --- |
| `object_outside_room` | All rotated footprint corners and the object's vertical extent must fit inside the room |
| `object_overlap` | Solid bounding boxes must not penetrate beyond tolerance; exact touching and vertical separation are allowed |
| `rug_overlap` | Rugs may overlap furniture, but two rug footprints cannot overlap, even if their thin vertical intervals fall within tolerance |
| `surface_support` | A surface object's bottom must touch its approved support's top, and all child footprint corners must fit on the rotated support |
| `wall_attachment` | A wall object must face inward and have its entire back edge touching the specified host wall |
| `ceiling_attachment` | A ceiling object's top must touch the ceiling |
| `opening_outside_wall` | Opening offset, width, sill height, and height must fit the host wall |
| `wall_opening_overlap` | A correctly attached wall object must not overlap a door or window, except its own linked window for an approved covering asset |

Rug and window-covering exceptions come from trusted asset registry metadata. A curtain remains solid for object/object collision checks. Surface children remain ordinary solids too: touching the support top is allowed; penetrating it is rejected.

`report.valid` means only that the implemented baseline passed. Every report includes a separate `baseline_scope` warning: solid bounding boxes are conservative, and door swings, walking/headroom clearances, overlapping openings, room-specific rules, budgets, and preservation of authorized prior state remain unchecked. Floor furniture blocking a doorway also requires the later clearance rules. The geometry function itself does not enforce fixed/locked edit restrictions or save scenes; the Step 2 edit service below adds protection checks and an API.

The bundled bedroom fixture passes these baseline checks without layout changes. Run `uv run pytest tests/test_geometry.py -q` for the geometry tests, or `uv run pytest -q` for the full backend suite.

### Implemented demo edit operations and API — Day 3 Step 2

`scenes/edit_schemas.py` defines strict move/rotate request models separately from the scene format. A move sets an absolute X/Z position; a rotation sets an absolute Y angle in radians. Neither operation can set elevation, dimensions, color, references, protection flags, or other scene fields. Scene schema version 1 remains unchanged.

`apply_edit(scene, edit)` in `scenes/mutations.py` returns a structurally validated candidate without mutating its input. It rejects fixed/locked targets and direct surface/wall/ceiling edits. Floor-support movement and rotation transform all surface children together around the support's bottom-center pivot, preserving elevation and adding the parent's angle change to each child's angle. A change that would transform a fixed/locked child is rejected atomically, even below geometry tolerance. Identical normalized parent transforms are no-ops; direct requests targeting protected objects are still rejected.

`preview_edit(request)` in `scenes/service.py` first checks the supplied base's geometry, applies an edit to a copy, and checks the candidate's geometry. A rejected edit returns the unmodified normalized base with structured edit errors or candidate geometry errors. It never returns an invalid candidate as the accepted scene. If the base is already invalid, it is reported unchanged and no edit is attempted; this does not manufacture a valid recovery scene. Absolute requests can be retried without applying the movement or rotation twice.

`scenes/router.py` exposes `POST /api/scenes/validate` and `POST /api/scenes/preview-edit`. These are stateless demo calculations. They do not establish ownership, store edits, or verify a saved revision. Protection checks compare with the supplied base; a client can alter that base between calls. Saved-project APIs must instead load an authorized previous scene from trusted storage and enforce revision concurrency. See [scene-edit-api.md](scene-edit-api.md) for request/response details and an executable example.

### Full acceptance policy to implement

Before accepting a scene, the backend must:

1. Validate field types, supported schema version, allowed categories and asset IDs, and finite numbers. Reject booleans or numeric strings where numbers are required.
2. Require positive dimensions and enforce configured limits on dimensions, object counts, text lengths, and request size. Version these limits with the validation policy.
3. Require unique IDs across all scene objects and openings, valid support/window references, and allowed placements for each asset.
4. Check rotated object containment, vertical bounds, solid-object overlaps, surface support, wall/ceiling attachment, opening bounds, and configured door/walking/headroom clearance rules. Apply the explicit rug exception from the trusted registry.
5. Preserve confirmed measurements, openings, fixed fixtures, locked objects, and other user constraints against the authorized previous state.
6. Reject stale revisions before saving an edit or finalizing a refinement.

Use a shared numerical tolerance, initially `0.000001` meters, for floating-point boundary comparisons. Exact touching is not an overlap; positive penetration beyond tolerance is. Clearance rules may still require separation. Do not use this tolerance as a claim about measurement precision.

Initial solid-object collision checks combine oriented rectangles in the X/Z plane with vertical interval overlap. A computer touching its desk's top is allowed; a computer intersecting the desktop is rejected. Bounding boxes remain conservative: a chair tucked under a desk may be rejected even when the real desk has empty space underneath. More detailed mesh geometry is outside the first implementation. Wall objects use the same solid bounds and must also avoid openings on their host wall.

Validators return structured failures identifying the rule and affected IDs, plus separate warnings for uncertainty or heuristic limitations. The model cannot override failures. Invalid proposals remain candidates; exhaustion of the retry budget preserves the last valid scene or reports failure if none exists.

## Versions and editing

`schema_version` identifies the JSON format. It is separate from the revision ID of a particular saved layout.

Project/run metadata stores the scene revision ID, parent revision, kind (`base`, `optimized`, `refined`, or `manual`), ownership, and validation report. Keep these outside the geometric scene payload. Saved scenes are immutable snapshots; editing creates a new revision and preserves the original/base scene.

Manual edits use the same geometry and authorization rules as agent edits and do not invoke an LLM. Refinements start from the latest authorized scene, including accepted manual edits. They must not overwrite a newer revision created while the agent was running.

Pydantic models in `src/roometry/scenes/schemas.py` are the machine-readable source of truth, with approved asset/category metadata in `src/roometry/scenes/assets.py`. Export JSON Schema through `RoomScene.model_json_schema()` or share synchronized types with the frontend. Update this document, example fixtures, validators, and renderer together when changing the contract; incompatible changes require a new schema version and an explicit migration strategy.

The frontend currently uses `frontend/src/lib/scene.schema.json` and `scene.generated.ts`, with a validated bundled fixture under `frontend/src/data/`. Regenerate them using `npm run scene:generate` from `frontend/`. The exporter validates the source fixture through Python before copying it. The frontend's JSON Schema check does not reproduce Python model validators for relationships, normalization, or scene history; it is an additional input-shape check, not a substitute for backend acceptance.
