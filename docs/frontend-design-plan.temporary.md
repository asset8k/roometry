# Roometry frontend — temporary design and flow plan

Status: steps 1–2 implemented — shared visual foundation, input/questions, private SSE for reconstruction and optimization, connected workflow stages, reconnect/snapshot recovery, and polling fallback. Step 3 (unified viewer/chat and contextual refinement) and step 4 (complete experience verification/cleanup) remain. Keep this reference through final user review.

Delete this file after the frontend is implemented, visually reviewed, and verified. Keep durable behavior and setup notes in the existing project documentation; do not remove generated schemas or useful tests as part of this cleanup.

## Direction and references

**A black room-design workspace with smoky charcoal liquid glass and neon lime actions. Clear next action, very little text.**

Current direction: follow the user's latest Control Center screenshot for the glass material. Use neutral charcoal translucent fills, 22 px panel blur, 12 px control blur, translucent gradients, soft inner bevels and thin white rim reflections. Controls are rounded capsules; larger panels use soft 28–34 px corners. Keep the black background with subdued ambient light, minimal layouts and Ventus neon-lime primary actions. Avoid green-filled glass, sharp thin card outlines, or hover fill changes. Text stays crisp; do not apply backdrop filters to the WebGL viewer ancestor.

Primary reference: [Ventus on Dribbble](https://dribbble.com/shots/27550490-Ventus-AI-Workspace-Landing-Page), especially the user's supplied crop of the prompt panel, connected workflow nodes, lime buttons, and translucent analytics cards. Match the surface treatment and component proportions closely, using original Roometry content and implementation.

Glass/background and interaction references inspected in the browser:

| Reference | Useful patterns | Application to Roometry |
| --- | --- | --- |
| [Pole Position](https://f1-pole-position.vercel.app/) | Focused welcome screen, one central input, pill controls, compact glass sign-in modal, guest access | A clear starting action, uncluttered header, contextual account dialog in the later authentication milestone |
| [Voyage AI](https://voyage-ai-kz.vercel.app/) | Simple segmented navigation, clear form groups, separate Explore view, sign-in/create-account switch | Grouped room inputs, familiar workspace navigation, future public room feed |

Voyage's Explore page remained in its loading state during inspection. Its navigation, heading, and loading layout were visible; populated feed cards were not verified. The feed card proposal below is a Roometry design choice.

Pole Position sets the black background and glass material. Ventus sets the lime accent palette, buttons, and connected workflow styling. The earlier projects also inform interaction and hierarchy. Authentication, saved rooms, and a public feed belong to their later milestones; keep this milestone's navigation functional and minimal.

## UX rules

- One dominant action per screen or panel. Use stable lime for primary actions, including hover; show errors in contextual alerts rather than changing the action color.
- Show the current task, its state, and the next action without explanatory paragraphs.
- Keep visible helper copy to one short sentence per section. Put evidence, assumptions, scoring explanations, and technical checks behind a contextual `Details` disclosure.
- Keep consequential information visible: partial scene, unplaced major items, failed operation, approximate measurements, or rejected edit. Summarize it plainly; detailed diagnostics can expand.
- Use persistent labels, recognizable icons with accessible names, and clear selected states. Do not communicate status through color alone.
- Avoid internal IDs, model names, token counts, tool names, and raw validation codes in the normal UI.
- Do not show a second upload action or ask users to reconfirm measurements they entered.
- The original reconstruction comes first. Improvement preferences appear only after the user chooses an improvement.
- Do not promise persistence that is not implemented. Refresh currently clears the form; temporary runs and uploads expire. Reconnecting to an active run must not start a new paid generation.

## Visual system

Starting values to tune against the reference in the browser:

| Token | Starting value / treatment |
| --- | --- |
| Page | `#080A0A`, black with subdued ambient light |
| Solid panel fallback | `#292C2C`, neutral charcoal fallback |
| Glass panel | `rgba(42, 47, 53, .32)`, transparent cool-neutral gradients, 22 px backdrop blur and 135% saturation |
| Panel border | `rgba(226, 235, 229, .19)`, white specular rim and soft, thicker inner bevels |
| Primary lime | `#CCFF32`, saturated neon-lime gradient, dark text, luminous edge and a soft halo |
| Secondary lime | `#ADDF1E`, with bright lime icons and selected states on smoked glass |
| Main text | `#F5F6F5`, soft white |
| Secondary text | `#B9BFBC`, neutral muted text; verify actual contrast on every backdrop |
| Warning / error | Distinct amber / soft red with icon and text |
| Shape | Panels 28–34 px radius; capsule buttons and single-line fields; smaller cards 26–28 px |
| Typography | Clean sans-serif, strong short headings, readable 14–16 px body; no decorative type inside controls |
| Spacing | Consistent 4 px scale; generous panel padding, compact related controls |
| Motion | Brief 150–220 ms state changes; gentle active-stage glow; respect reduced motion |

Glass should transmit muted ambient light through a neutral smoky surface, with soft white reflections around rounded lens-like edges and a broader inner bevel. Keep the text sharp. Primary buttons use dark text, a bright yellow-lime fill and a visible neon halo, with clear hover, focus, pressed, loading, and disabled states. Secondary buttons use smoked translucent glass, lime icons, soft white text, and a beveled border.

Use subdued static ambient light behind the panels; the overall page stays black. Keep the 3D canvas on a quiet dark backdrop so furniture colors and room finishes remain readable. Apply glass to the toolbar, chat, and overlays rather than blurring the scene itself. UI theme colors must never recolor scene objects, doors, or finishes.

Keep large animated effects and stacked backdrop filters away from the WebGL canvas. Provide an opaque fallback and verify text contrast, keyboard focus, touch targets of at least 44 px, and responsive behavior. Do not add heavy graphics dependencies for these effects.

## User journey

```text
Photos + room details
        |
   Create room
        |
   Read photos ----- Need a detail? ---> One question, three choices
        |                                      |
        +--------------------------------------+
        |
   Build -> Check -> Review
        |
   Original room + four ratings
        |
   Choose an improvement
        |
   Optional preferences -> Improve room
        |
   Original / Improved comparison
        |
   Refine or edit a copy
```

The diagram shows the user's path. On screen, the progress component follows actual backend stages; clarification is conditional and review/ratings can finish independently.

### 1. Start: photos and essential details

- Header: Roometry wordmark, Explore, My rooms, and Sign in. No Connected label, duplicate creation CTA, setup counter, or footer tagline. Connectivity errors belong beside the failed operation.
- Short heading: **See your room differently.**
- Central glass panel: photo picker, removable thumbnails, room type, optional description, width / length / height with unit labels.
- Keep all room details in one compact group; dimensions can remain blank.
- Primary action: **Create room**. Upload and reconstruction happen behind this one action.
- Photo guidance: **Add different angles of the same room.** File requirements live in a small disclosure; validation errors appear beside the affected photo or field.
- Privacy line: **Private photos · Temporary session** with accessible details about refresh and expiry.
- No initial goals, budget, or style inputs. No repeated summary sidebar that restates the form.

### 2. Generation: connected glass workflow

- Replace the long input page with a focused progress workspace; keep a compact `Room details` disclosure and photo strip available.
- Use the supplied Ventus flow as the direct component reference: stacked glass pills connected by fine arrows on narrow screens, a compact horizontal or vertical arrangement on desktop.
- User-facing stages: **Photos → Build → Check → Review**. Show a real short activity label when assessing ratings or checking the catalog; avoid turning every internal tool into a new node.
- Completed nodes show a check, the current node has the lime highlight, waiting nodes are muted. A question pauses the flow visibly.
- Example activity: **Building your room…** One status line is enough.
- Show real upload progress if available; use an indeterminate indicator for unknown-duration model work. Never invent a percentage or countdown.
- `Cancel` remains accessible. Connection loss shows **Reconnecting…** and recovers the existing run rather than generating another room.

### 3. Clarification: one decision at a time

- Show the question inside the progress workspace, preserving the room/photo context.
- Example: **Which wall is 3 m?**
- Three large glass choices with descriptive human-readable labels: **Window wall / Desk wall / Not sure**. Highlight selection with border, icon, and lime accent.
- Single action: **Continue**. Show measurement inputs only for an answer that needs them.
- Small counter: **Question 1 of up to 3**. Follow the backend cap, including across resumed work.
- Do not make a user re-enter supplied dimensions or replay photo analysis after an answer.

### 4. Result: viewer first, ratings close by

- Open the editable viewer as the main screen. Do not require scrolling through analysis, validation, and assumptions to reach the room.
- Desktop: large 3D canvas plus a roughly 340–380 px glass side panel. Keep orbit, top view, wall visibility, and zoom controls compact and accessible.
- Side panel sections: **Overview / Objects / Chat**. Selected-object controls replace the relevant side-panel content rather than adding another full sidebar. Overview contains dimensions and ratings.
- Ratings: **Space / Storage / Workspace / Aesthetics**. Each card has a score or honest unrated state, one short reason, and `Improve`. Use `Recommended` on the suggested focus. Explanation and confidence are available in details; do not replace missing evidence with `0/10`.
- Ratings describe the original reconstruction; show a compact **Original room ratings** label so manual edits cannot be mistaken for reassessed scores.
- Default scene label: **Original**. A partial result shows **Approximate · 2 items unresolved** when applicable, with the actual unresolved count and a short summary of important missing items.
- Geometry and layout warnings stay accessible near the viewer. Use plain summaries, such as **Door access blocked**, with an option to inspect details and affected objects.
- One `Details` area groups photo evidence, uncertainties, wall assignments, estimate corrections, and validation notes. Keep these records; reduce their visual footprint.

### 5. Improvement: user chooses the direction

- Clicking a rating's `Improve` opens a compact request panel for that focus.
- Optional fields: budget, style, and **Anything to keep?** Use progressive disclosure for extra preferences. Keep budget semantics (zero means no purchases) and unverified pricing information available beside the field.
- One visible action: **Improve room**. Reuse backend prepare/execute operations internally without the current redundant `Improve room → Run improvement` confirmation screen.
- Show compact streamed progress for proposing, checking, and reviewing candidates. Keep the original room viewable while work runs.
- Show **Original / Improved** only when an improved candidate exists. Put the short explanation of useful changes beside the comparison, with detailed checks expandable.
- If no acceptable candidate is found: **No better layout found.** Keep the original scene and offer `Try another focus`; do not present an unchanged room as an improvement.
- Preserve originals and accepted candidates. Manual experimentation uses **Edit a copy**, with explicit discard/reset actions.

### 6. Refinement: chat next to the scene

- Chat accepts short requests in context, for example **Move the desk closer to the window** or **Try a warmer palette**.
- Keep messages and progress concise. Do not stream raw model reasoning, provider payloads, or internal tools.
- Route requests through the existing bounded planning/edit/validation path. Free-form intent handling and streamed refinement need integration work; the current UI does not provide a general scene chat endpoint.
- Retain the visible scene while a candidate is checked. Only present a replacement after validation; if rejected, explain the relevant reason briefly and keep the previous scene.
- Preserve confirmed dimensions and fixed/locked items. Never silently restart reconstruction to handle a refinement.
- State clearly which scene a refinement starts from; ratings stay tied to the original until an explicit reassessment exists.

## States and minimal copy

| State | Visible copy | Action |
| --- | --- | --- |
| No photos | Add room photos | Choose photos |
| Invalid photo | This photo couldn't be read. | Replace photo |
| Uploading | Preparing photos… | Cancel |
| Analyzing | Reading your room… | Cancel |
| Building | Building your room… | Cancel |
| Reviewing | Checking the details… | Cancel |
| Question | The focused question itself | Continue |
| Partial result | Approximate · N items unresolved | View details |
| Ratings unavailable | Ratings unavailable | Keep viewer usable |
| Reconnecting | Reconnecting… | Check status if recovery fails |
| Provider failure | Couldn't finish this attempt. | Try again |
| Cancelled | Generation cancelled. | Try again |
| Expired | Session expired. | Start again |
| Invalid manual edit | This move blocks the door. | Keep previous placement |
| No accepted improvement | No better layout found. | Try another focus |

Errors should give the real relevant cause when known, using plain words. Preserve selected photos and entered details where the existing lifecycle allows it; do not promise recovery after refresh or expiry. New billed runs follow an explicit user action. A ratings or review failure must not hide an otherwise usable reconstruction.

## Responsive layout

- Desktop: canvas and side panel together; compact floating viewer controls; no large marketing heading above a completed workspace.
- Tablet: narrower or collapsible side panel, preserving usable canvas space.
- Mobile: canvas with a bottom sheet for **Overview / Objects / Chat**; controls remain reachable and do not intercept orbit gestures. Questions and forms use one column.
- Workflow pills stack vertically on small screens. No horizontal page overflow or tiny three-column answers.
- Keep draft text, selected answers, selected objects, camera position, and scene edits stable when opening panels or receiving progress events.

## Later authentication and feed

Design continuity only; these are not implementation requirements for the current milestone.

- Sign-in: use Pole Position's compact modal pattern, with Roometry's smoked liquid glass and lime action. Short heading **Welcome back**, labeled fields, and a clear create-account switch. Actual fields and account behavior follow the authentication milestone.
- Navigation when supported: **Create / Explore / My rooms**, with `Sign in` or account controls. Add destinations as their features become available.
- Feed: room-preview cards, short room title, room type, one `View room` action, and restrained metadata. An optional Original/Improved preview can distinguish shared improvements.
- Publishing is a separate explicit choice after persistence/public sharing is implemented. Private uploads and generation must never publish a room automatically.

## Implementation sequence

1. **Visual foundation and input screen:** introduce shared tokens and glass/pill/input components in the existing frontend; restyle the header, photo form, and question cards. Establish the reference match at desktop and mobile widths before extending it everywhere.
2. **Real streaming — implemented:** private scoped SSE for generation and optimization feeds existing controllers and the connected stage flow. Healthy streams replace routine polling; authorized snapshot reads recover full state. Reconnect to the same run, order/deduplicate events, recover missed updates from authoritative snapshots, stop subscriptions on cancellation/unmount, and keep polling as a bounded fallback. Streams only observe work and must never trigger model calls.
3. **Workspace and refinement:** put the viewer first; integrate concise ratings, questions, improvement preferences, Original/Improved comparison, object editing, and bounded chat refinement. Preserve backend safeguards and existing scene/edit behavior while replacing repetitive prose and duplicate actions.
4. **Verify and clean up:** check input → interruption → resume → reconstruction → ratings → improvement → comparison/refinement with mocked responses, including reconnects, cancellations, partial results, rejected candidates, and error isolation. Visually inspect supported desktop/mobile widths and reduced motion. Run appropriate frontend checks/build and tests for changed streaming endpoints. Delete this temporary plan after implementation and user review.

## Existing integration points

| Area | Existing files to reuse |
| --- | --- |
| App shell and theme | `frontend/src/App.tsx`, `frontend/src/index.css`, shared tokens in `frontend/src/design-system.css`, primitives in `frontend/src/components/ui/primitives.tsx` |
| Photos and room details | `frontend/src/components/room-input/RoomInputWorkspace.tsx` |
| Generation and clarification | `frontend/src/components/room-analysis/RoomGenerationWorkspace.tsx` |
| Progress and recovery | `frontend/src/lib/room-generation-controller.ts`, `frontend/src/lib/agent-api.ts` |
| Ratings and improvement | `frontend/src/components/room-analysis/RoomAssessmentCards.tsx`, `RoomImprovementWorkspace.tsx`, `ImprovementComparison.tsx` |
| Optimization requests | `frontend/src/lib/optimization-controller.ts` |
| Viewer and scene edits | `frontend/src/components/room-viewer/SceneWorkspace.tsx` and existing viewer/editor components |
| Agent status / streaming foundation | `src/roometry/agents/router.py`, `runs.py`, `state.py` and the existing optimization run lifecycle |

## Completion criteria

- One shared black / liquid-glass / neon-lime theme covers upload, questions, ratings, viewer, object editor, improvement forms, comparison, and future account/feed pages.

- The initial task is obvious without reading a paragraph: choose photos, supply known details, create the room.
- The supplied Ventus crop is recognizable in the glass panels, pill controls, lime actions, and connected stage flow.
- Questions are understandable, contextual, and capped; entered measurements remain authoritative.
- Progress reflects actual work and reconnects without duplicate paid calls.
- A usable scene opens immediately, including a clearly labeled partial scene; evidence details do not bury it.
- Original ratings lead to a user-selected improvement; original and improved scenes remain distinguishable.
- Refinement and manual edits keep the last valid scene on rejection.
- Routine screens contain short labels and brief helpers; important limitations remain discoverable and meaningful failures stay visible.
- Authentication and sharing are represented only when their real features exist.

## UX audit — October 25

- Removed repeated setup headings, decorative badges, connection status, and footer copy.
- Native selects retain keyboard/menu behavior with a custom chevron inset 16 px and 48 px trailing padding.
- Create room remains lime; empty-photo submission focuses actionable validation without uploading. Busy/recovery locks remain enforced.
- Ratings retain scores and recommendations; detailed explanations are collapsed, with manual-edit scope preserved in About.
- Header navigation keeps the guest form and active run mounted. Home navigation must not refresh or discard photos.
- Sign-in and registration designs use username/password only. Native dialogs trap focus, close with Escape, restore focus, and prevent background scrolling. Credentials are not submitted or persisted.
- My rooms and Explore have truthful empty/unavailable states. No fake saved projects or public entries.
- Viewer Save opens the account design; Share opens a private sharing draft with explicit, unchecked consent. Publishing is disabled; photos/chat are never selected implicitly.
- Account, persistence, and public-feed backends remain future milestones. Do not report these frontend designs as working authentication or storage.
- Streaming/chat integration and server-reported quota states remain in their planned steps; do not invent progress or remaining-generation counts.

## Material refinement — latest Control Center reference

The newest screenshot supersedes the earlier green-tinted glass treatment. Use translucent charcoal panels, a soft top and bottom rim, rounded capsules, and layered inset highlights. Keep Ventus lime for primary actions, while secondary control icons are mostly white. White circular utility icons may echo the reference without changing component layouts. Background blur affects only the backdrop; no text distortion, moving refraction, animated filters, or new graphics dependency.

Lime refinement: primary actions use a slightly more saturated `#CCFF32` with a restrained static halo; the headline accent has a faint glow. Hover keeps the same fill and glow to avoid flashing. Smoked glass colors are unchanged.

Transparency refinement: panels use 32% base opacity, secondary controls 22%, and dialogs 45%. A cool-neutral translucent gradient and localized backdrop blur retain visible background light and detail. Keep the neon-lime fill stable and fully readable; never apply a backdrop filter to the live WebGL ancestor.

Brand mark: a clean perspective room/box outline with neon-lime AI inside its front face. Use a pale outline and subtle translucent top/side faces; no separate 3D lettering. Keep the Roometry name, accessible home label, and matching favicon. Scale the mark to 42 px desktop and 38 px mobile.

Step 2 verification: deterministic real-browser run covered reconstruction → question → resume → completed scene → improvement → comparison → cancellation with an earlier candidate retained. Short test-only stream lifetimes exercised reconnection. A held construction also exercised the configured timeout without repeating analysis/construction; inputs stayed available. No OpenAI calls. Shared graph/lifecycle tests, SSE privacy/replay/revocation tests, client race/fallback tests, frontend suite, and production build passed. The temporary plan remains until step 4 review.

Selector refinement: all room-type, wall-visibility, and improvement-version dropdowns share a portaled liquid-glass listbox with keyboard navigation, type-ahead, selection checks, outside dismissal, and viewport-aware placement. Workflow labels use explicit two-word stage names. Verified pointer/keyboard selection and mobile layout without horizontal overflow.
