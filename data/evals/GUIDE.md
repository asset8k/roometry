# Evaluation dataset

`manifest.json` defines the public benchmark. It currently contains **zero ready
cases**; the counts are targets for the next step, not completed evaluations.

| Location | Contents | GitHub |
| --- | --- | --- |
| `cases/<case_id>.json` | Independent reference annotations | Public |
| `images/<case_id>/` | Approved source images | Public |
| `private/` | Personal photos, annotations, and reference scenes | Ignored |
| `../../artifacts/evaluations/` | Raw runs, renders, usage, and account room links | Ignored |

The artifacts path above is relative to this folder. Historical code-only
fixtures live separately in `data/fixtures/evaluation/`; they are not benchmark
photo cases. Personal regressions are additional local checks and never count
toward the 25 public rooms.

## Adding a public case

- Use stable IDs such as `living_01`; name the primary image `living_01.jpg`.
  Additional views use `living_01_view_02.jpg` in the same image folder.
- Record the ID, room type, development/held-out split, primary image, all image
  paths, annotation path, source type, and publication permission in the manifest.
  Include attribution/license for photographed rooms or generation provenance
  for synthetic rooms. Use paths relative to `data/evals/`.
- Only add images whose publication is authorized. Remove identifying metadata
  and inspect visible personal information before admitting an image.
- Annotate what the actual images show. A generation prompt is not ground truth;
  do not invent measured dimensions or assume generated views are consistent.
- Keep reference annotations separate from the application's generation inputs.
  Freeze the 20 held-out cases before tuning; use the five development cases for
  iteration. Adding images and annotations is the next implementation step.

## Local and execution boundaries

Keep personal images, annotations, account details, tokens, saved room IDs, logs,
and screenshots in `private/` or the ignored artifacts folder. Never put personal
case entries in the public manifest, embed private data in test fixtures, or use
`git add -f` to bypass these exclusions. `.gitignore` does not erase Git history.

The future live runner must require a signed-in account, save usable scenes to
**My rooms**, and use the primary image stem as the room title. Improvements are
saved versions of that room. Record the room links in local results. Saving must
not publish anything to Explore. These are manifest requirements; account-based
batch execution is not implemented by this dataset-structure step.

Code-based checks and the chat-based visual review reuse the same saved outputs.
Raw outputs stay local; only a separately reviewed, sanitized report may be
published. No paid API calls or image generation are needed to check this setup.
