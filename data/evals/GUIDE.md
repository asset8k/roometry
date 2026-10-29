# Evaluation dataset

**25 annotated room cases; no live evaluation runs yet.**
Seven living rooms, six bedrooms, five kitchens, four bathrooms and three offices.
Twenty images were generated with the built-in Imagegen tool; five are licensed
photographs. Five cases (`*_01`) are development cases; the other twenty are held
out. The frozen manifest records checksums, source permissions and test inputs.

Open [the image gallery](index.html) in a browser to inspect all 25 rooms. Images
and annotations use matching case names. The gallery works locally without a
server or application API calls.

| Location | Contents | GitHub |
| --- | --- | --- |
| `manifest.json` | Splits, source credits, test inputs, visual references and checksums | Public |
| `cases/<case_id>.json` | References compatible with the existing offline evaluator | Public |
| `images/<case_id>/<case_id>.jpg` | The 25 approved images, with identifying metadata stripped | Public |
| `generation.json` | Exact prompts and built-in generation provenance | Public |
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
entry's `visual_reference`. These are reference data for the next runner step;
the existing single-case evaluator does not yet interpret every field.

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
