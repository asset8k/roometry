# Streaming and workspace milestone — verified October 25, 2026

The local-development milestone is complete: private SSE progress, clarification/resume, a viewer-first workspace, original ratings, independent Original / Improved / editable copies, and bounded current-scene chat refinements. Mobile uses the Room tools sheet. Authentication, saved projects, durable runs and production hosting remain later milestones.

## Verification

- Backend: 2,035 deterministic tests passed.
- Frontend: 487 tests passed with the saved live artifacts enabled, including the two normally opt-in artifact tests. Production TypeScript/build passed; the existing large-bundle warning remains.
- New journey tests cover upload → clarification → complete or partial scene → manual move and support propagation → lock/color preview → improvement → refinement → rejected candidate, without losing previous scenes or repeating reconstruction.
- Existing tests cover expiry, cancellation, malformed responses, ownership, reconnect/fallback and late-response races.
- Browser checks covered desktop, 390 px mobile without horizontal overflow, sheet/draft preservation, custom version selection, Original / Improved switching, navigation during generation, account-dialog Escape/focus restoration, and zero console errors.
- A foreign guest received 404 for the real room, refinement and both progress streams, without additional provider usage.

## One live flow

Used the existing five-photo bedroom regression case, the previously supplied dimensions, Sol 6.1 for initial construction, and Luna for other calls. Reference annotations were used only for offline evaluation.

1. Photo analysis ran once. Two clarification answers resumed the same run.
2. Initial construction required one repair. A geometry-valid partial scene retained 16 objects; one backpack observation remained unresolved. A visible partial-result label discloses this.
3. The requested warm-neutral improvement retained a candidate after one invalid material patch was rejected and corrected. Every position and rotation was preserved.
4. A manual desk move from Z=1.35 m to Z=1.40 m was validated; its supported objects followed. The desk was then locked.
5. One chat refinement changed only the rug to `#687C8F`. The manually edited desk, lock, support relationships and other objects were preserved. Both candidates remained available.
6. SSE observed all relevant stages and reconnected once with its cursor. Read/recovery paths did not repeat analysis or initial construction.

The automated bed-color fill initially changed only the DOM swatch, not React preview state. The color control now handles both input and committed-change events, with duplicate updates ignored. Browser panel-switching and snapshot tests verified the committed preview afterward; no additional paid refinement was performed.

## Exact test usage and estimated cost

| Stage | Calls | Estimated USD |
| --- | ---: | ---: |
| Reconstruction, including repairs/review/ratings | 6 | $0.0880779 |
| Improvement | 3 | $0.0034077 |
| Chat refinement | 2 | $0.0023723 |
| **Total** | **11** | **$0.0938579** |

Provider-reported usage: 206,231 input tokens and 19,865 output tokens. All 11 calls have complete usage. This estimate uses [OpenAI standard short-context pricing](https://developers.openai.com/api/docs/pricing), verified October 25; it is not a billing invoice or a guarantee of future per-run cost.

## Scope and limitations

All three scenes passed geometry checks. Independent reference checks passed major coverage, wall relationships, orientation and proportions, but did not fully pass appearance checks: chair primary-color interpretation, floor pattern, and an ambiguous ceiling-light anchor match. The expected track-light asset is present; the stable matcher selected another ceiling fixture. These results do not establish exact photo likeness.

Guest scenes and chat remain temporary and process-local. The account UI correctly states that account access is not yet implemented. No photos or chat were published.

Private live outputs, diagnostic renders, provider usage, SSE events and logs are in the ignored `artifacts/streaming-workspace-final-20261025/` folder. Replay its frontend contract checks without provider calls:

```bash
ROOMETRY_LIVE_ARTIFACTS="$PWD/artifacts/streaming-workspace-final-20261025" npm --prefix frontend test
```

The temporary design plan and isolated verification-server files have been removed. Durable interaction and API behavior are documented in README and tested in source.
