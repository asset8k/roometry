# Reconstruction quality: dimension estimates and omissions

Room sizes resolve in this order: entered/confirmed values, direct photo estimates,
supported wall-span estimates, then an explicitly assumed starting size after the
user chooses estimates. Existing user axes are never swapped or overwritten.

Wall-span recovery requires both shorter and longer walls, high-confidence span
evidence and visible metric references with full-wall coverage. Furniture must
also have an established parallel wall relationship. Conflicting references,
cropped/uncertain spans and catalogue defaults cannot establish room scale.
Recovered spans are rounded to 0.5 m and their provenance is passed to construction
and kept in scene assumptions. Without a supplied horizontal axis, width denotes
the shorter span. Height is estimated independently by photo analysis.

When scale remains unavailable, the existing 4 m width, 4 m length and 2.6 m height
starting values apply only to unknown axes after estimation consent. These are
reported in `assumed_axes`, not represented as photo measurements. No extra model
requests were introduced.

Server warnings beginning `Reconstruction omissions:` now include one bounded
report per constructed result: direct and dependent failure counts, original
entity IDs, root dependency chains, stage, geometry rule, selected asset/placement
when available, and catalogue match counts. Raw photo/model prose and account data
are excluded. Cached result reads do not emit another report. The frontend keeps
direct issues and dependent items separate in collapsed reconstruction details.

To inspect a private saved run containing `analysis` and `proposal` offline:

```sh
uv run python scripts/diagnose_reconstruction.py /path/to/run.json
```

An optional `draft` field provides rejected model selections. Without it those
selections remain unknown rather than inferred. This command makes no API calls.

The saved bedroom verification from Oct 27 was checked offline: its single omitted
bag was missing from the draft, despite a compatible catalogue model being
available. A read-only check of the latest locally persisted living-room run
found three omissions: a wall-mounted media unit had no compatible wall model
(the TV-stand family currently supports floor placement), while two supported
plants were missing from the draft despite compatible models being available.
That run used photo estimates of 6 × 7 × 3.5 m with no assumed axes, confirming
that the fixed fallback is not used for every unmeasured room.

The kitchen/bathroom runs were not available in the inspected local account.
Their screenshots alone cannot establish their specific rejection chains;
inspect their saved run or the new server diagnostics on the next verification.
Catalogue expansion, fixture assembly changes, and live multi-room verification
remain subsequent steps.
