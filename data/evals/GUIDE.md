# Evaluation dataset

**25 frozen room cases for offline replay and recorded live evaluations.**
Seven living rooms, six bedrooms, five kitchens, four bathrooms and three offices.
Twenty images were generated with the built-in Imagegen tool; five are licensed
photographs. Five cases (`*_01`) are development cases; the other twenty are held
out in the original split. Following the first benchmark and failure analysis,
these reused cases are regression evidence, not an untouched holdout. The frozen
manifest records checksums, source permissions and test inputs; its status is a
snapshot from dataset preparation.

Open [the image gallery](index.html) in a browser to inspect all 25 rooms. Images
and annotations use matching case names. The gallery works locally without a
server or application API calls.

| Location | Contents | GitHub |
| --- | --- | --- |
| `manifest.json` | Splits, source credits, test inputs, visual references and checksums | Public |
| `cases/<case_id>.json` | References compatible with the existing offline evaluator | Public |
| `images/<case_id>/<case_id>.jpg` | The 25 approved images, with identifying metadata stripped | Public |
| `generation.json` | Exact prompts and built-in generation provenance | Public |
| `calibration-v1.json` | Versioned, photo-backed scoring corrections | Public |
| `private/` | Personal photos, annotations and reference scenes | Ignored |
| `../../artifacts/evaluations/` | Raw runs, screenshots, costs and saved account room links | Ignored |

The artifacts path above is relative to this folder. Historical code-only
fixtures in `data/fixtures/evaluation/` are not benchmark photo cases. Personal
multiview regressions remain separate and never count toward these 25 rooms.

## Annotation and measurement rules

References were authored by Codex after viewing each final image. They are **not
independently human-validated**, and generation prompts are not ground truth.
All public cases have one viewpoint; unseen walls, exact dimensions and fine
positions are uncertain. Do not advertise this small, partly synthetic sample as
production accuracy. Report the five photographed cases separately.

The manifest's `input` field is the application's input. Reference annotations,
visual descriptions, generation prompts and improvement goals must not enter the
reconstruction prompt. Keep references separate to avoid answer leakage.

Eight scenarios supply complete fictional user measurements, eight supply partial
measurements, and nine supply none. Scenario measurements are **test-user inputs,
not measured photo truth**. Original photograph dimensions are unknown. Follow
`clarification_policy`: preserve supplied dimensions, answer unknown axes/details
with uncertainty, and allow labeled approximate estimates. No precise image-based
size accuracy can be claimed from these inputs.

The `cases/` files cover inventory, broad colors and supported relationship kinds.
Additional layout, support and component expectations are in each manifest
entry's `visual_reference`. The batch runner checks explicit supports and passes
free-text features, photo-relative wall anchors and representation gaps to visual
review; it does not pretend to grade every descriptive field automatically.

- Reference IDs identify physical instances; they are not model-prescribed IDs.
  Repeated beds, chairs and monitors need independent matching before scoring.
- Countertops, vanities and grouped lighting can be segmented differently. Check
  retained features and coverage, not arbitrary mesh or cabinet-door counts.
- Unannotated small decor is optional. Check invented major furniture/plumbing
  without penalizing an incidental book, cup, plant or cushion.
- Roof skylights and sloping ceilings in `bedroom_04` are explicit representation
  gaps. Its furniture is assessable; roof openings are recorded for visual review
  rather than forced into vertical-wall inventory checks.
- Fitted carpet is floor finish; reflections and outdoor balcony furniture are
  not extra indoor objects. A nearly empty room checks invented furniture.

## Calibrated offline scoring

Pass `--calibration data/evals/calibration-v1.json` to `scripts/run_evals.py` in
batch mode. It checks the frozen dataset fingerprint and records the calibration
version, checksum and reasons in the new report. Original evidence is never
overwritten. Use the same policy and calibration for both benchmark rounds.

Version 1 permits the explicitly annotated segmented lighting and integrated
desk hutch, and enforces the five kitchens' visible basin/faucet features from
catalog geometry rather than labels. Repeated interchangeable instances are
matched without candidate placement; ambiguous identities leave only their own
relationships unassessed. Twin headboard-wall checks use rotated footprints.
The 90% inventory, 85% spatial and 8/10 visual gates remain unchanged. Existing
visual reviews may be reused only for identical photo/scene evidence; report
their reuse separately from any new judgment. Free-text visual expectations not
encoded as checks still require visual review.

## Improvement subset

Eight independent trials reuse original reconstructions, two for each category:

| Goal | Cases |
| --- | --- |
| Space and movement | `living_02`, `bedroom_03` |
| Storage and organization | `kitchen_03`, `office_02` |
| Workspace | `bedroom_02`, `office_01` |
| Aesthetics and cohesion | `living_04`, `bedroom_05` |

Meaningful improvements must preserve essential functions, confirmed dimensions,
fixed installations and locks. A rating increase alone does not establish quality.

## Image permissions and credits

The following photographs are included under their stated source licenses. Each
was resized without cropping, oriented from EXIF and re-encoded as JPEG; metadata
was stripped. **The same source license applies to each modified photograph.**
Share-alike licenses apply to the corresponding images, not automatically to the
application code. Attribution and license links must travel with redistributed
copies. No endorsement by the photographers is implied.

| Case | Photographer and original source | Image license |
| --- | --- | --- |
| `living_07` | [FASTILY](https://commons.wikimedia.org/wiki/File:Apartment_Living_Room_3_2018-09-28.jpg) | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) |
| `bedroom_06` | [Mx. Granger](https://commons.wikimedia.org/wiki/File:Bedroom_in_a_serviced_apartment_in_London.jpg) | [CC0](http://creativecommons.org/publicdomain/zero/1.0/deed.en) |
| `kitchen_05` | [Pbroks13](https://commons.wikimedia.org/wiki/File:Modern_Kitchen.jpg) | [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0) |
| `bathroom_04` | [OKJaguar](https://commons.wikimedia.org/wiki/File:Contemporary_bathroom.jpg) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0) |
| `office_03` | [Stubacca](https://commons.wikimedia.org/wiki/File:Home_office_small_office.JPG) | [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0) |

The twenty synthetic images are fictional project assets generated from text
only, without private reference photos. Their exact prompts are in
`generation.json`. The built-in tool did not expose its model identifier; none
was invented. No claim of exclusive rights or third-party license is made for
the generated assets. The project's reference annotations are provided under
[CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).

## Execution and privacy boundaries

The later live runner must use a **signed-in account**, save usable results to
**My rooms**, and title rooms after their primary image stem. Improvements are
versions of the same room. Save private room IDs/links only in local artifacts.
Saving must not publish anything to Explore. Batch execution is a separate step.

Never publish personal images, annotations, account tokens, saved room IDs, raw
logs or screenshots. Keep these under ignored `private/` or artifacts folders.
Do not bypass exclusions with `git add -f`. Ignore rules do not erase older Git
history; historical personal-room annotations need separate cleanup before that
history is published.

Dataset checks run offline:

```sh
uv run pytest -q tests/test_evaluation_dataset.py tests/test_evaluation_dataset_privacy.py
```

The reconstruction checks and chat-based visual review will reuse the same saved
outputs. No extra judge API is required. Raw outputs remain local; publish only a
separately reviewed, sanitized report. README remains unchanged.

## Offline runner

**This command never starts a generation or calls an API.** It reads previously
recorded application results; step 4 supplies the signed-in live recordings.

```sh
uv run python scripts/run_evals.py \
  --manifest data/evals/manifest.json \
  --recordings artifacts/evaluations/recordings/index.json \
  --output artifacts/evaluations/report-01 --render
```

`index.json` uses this shape. Paths are relative to its directory. Optional cost,
timing, draft and flow-check artifacts may be omitted; their checks stay unknown.

```json
{
  "schema_version": 1,
  "recording_mode": "live",
  "dataset_fingerprint_sha256": "<copy from manifest>",
  "account_id": "<local account ID; never a token>",
  "trials": [{
    "evaluation_id": "living_01_original",
    "case_id": "living_01",
    "kind": "reconstruction",
    "run": "living_01/run.json",
    "cost": "living_01/cost.json",
    "timing": "living_01/timing.json",
    "saved_room": {
      "room_id": "<saved project ID>", "account_id": "<same account ID>",
      "title": "living_01", "published": false
    }
  }]
}
```

Improvement entries use `kind: "improvement"` and `parent_evaluation_id` pointing
to their reconstruction entry. They contain the saved optimization response,
reuse that room ID/title, and use the goal frozen in the manifest. Only the eight
subset cases can have primary improvement trials. Explicit `repeat: true` entries
do not inflate primary success rates. Use `recording_mode: "mock"` for test data.

Cost files contain `calls` with provider response ID, run ID, operation, model,
input/output/cached/cache-write token counts and service tier. Failed paid calls
still count. Shared receipts need run IDs; duplicates count once; conflicting,
missing or unpriced usage makes the total a lower bound. Pinned October 28 rates
are estimates, not invoices. Timing uses `reconstruction_elapsed_seconds` or
`optimization_elapsed_seconds`; no latency is inferred from a missing record.
Flow checks accept optional recorder assertions for replay prevention,
clarification resumption and tool-error recovery. Final scenes alone cannot prove
these happened. Account checks likewise assess saved recorder receipts, not a
new authentication request.

The new output folder contains JSON/CSV results, a short summary, local renders,
`review-packet.json`, an unfilled `visual-review.json`, and a separate blind key.
Folders cannot be overwritten or exported into the public dataset. Failed runs
stay in attempted totals; unrecorded cases are separately labeled **not run**.
Generated/photographed and development/held-out results are reported separately.

| Layer | Checks and interpretation |
| --- | --- |
| Reconstruction code | Schema, geometry, confirmed dimensions, major inventory, broad wall/facing relationships, explicit supports, extra-major-object proxy |
| Appearance diagnostics | Broad RGB colors, proportions and asset form; minor failures alone do not reject an otherwise usable scene |
| Improvement code | Original preserved, room/openings/locks/fixed objects unchanged, geometry/clearance and essential functions retained; recomputed goal gain plus a physical change |
| Visual reconstruction | Five dimensions: major objects, layout, orientation/support, proportions, appearance; each 0 wrong/absent, 1 partly recognizable, 2 consistent with visible evidence |
| Visual improvement | Blinded A/B preference, meaningful goal improvement, preserved constraints, confidence and short visible evidence |

Initial gates are 90% major coverage and 85% assessed spatial relationships. A
visual reconstruction passes at 8/10 with no dimension at zero. Medium/high
confidence is required for combined success. These are explicit starting policy
thresholds, not measured production guarantees. Walking/door warnings in an
existing original remain visible; improvements must not worsen checked access.
Practical goal scores reuse application geometry proxies and are not independent
proof of usefulness. Aesthetics has no fabricated code score; it needs visual
review. Descriptions of unknown centimeters are not ground truth.

Identity matching uses analysis descriptions/colors before candidate placement
scoring. It never chooses pairings to maximize spatial correctness. Distinct tied
instances are flagged ambiguous; reviewed `identity_matches` can resolve them.
Interchangeable repeated items may be permuted. Compound fixtures' features are
reviewed visually rather than demanding an exact mesh count.

Policy v1.1 permits `verified_visible_extras` in a recording: retained entity IDs
mapped to short evidence from the source photo. Use this only when a visible
piece of furniture was absent from the reference list. Unknown or already-matched
IDs are rejected. Missing anchors, placements, supports and geometry still receive
the same checks; this correction cannot excuse an invented object. Review these
items against the photo before inspecting scores and preserve the evidence.

For chat review, inspect the photos and all views in `review-packet.json`, fill
the template, then replay with `--reviews path/to/visual-review.json` into a **new**
output folder. Do not read app scores or `blind-key.json` before choosing A/B.
The CPU renders simplify curves, lighting and transparency: use scene JSON for
occlusions and browser screenshots for inconclusive appearance. Review hashes
bind judgments to the exact dataset and scenes; stale/invalid judgments are
isolated. Pending reviews never become successful judgments automatically.

## Recorded human review

`results/eval-2-human.json` preserves the project owner’s pass/fail judgments for
all 25 original Eval 2 reconstructions: 20 pass and 5 fail. Four were described
as slight fails; these count as false while retaining that note. Code checks,
in-session LLM visual judgments and human judgments are three separate layers.
The human review was one unblinded owner review; it does not independently
validate reference annotations or improvement candidates. README updates and
production deployment are deferred. Private migration exports and the detailed
final report remain under ignored `artifacts/evaluations/eval-2-20261030/`.
