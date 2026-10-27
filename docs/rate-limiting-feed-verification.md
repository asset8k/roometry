# Rate limiting and public feed — October 27, 2026

The milestone's accounting, persistence and sharing contracts are verified. Local
development quotas remain **off**; the live test enabled them only in a disposable
instance. Production defaults and the explicit environment override are covered
by tests. See [allowances](ai-allowances.md) and [sharing](public-sharing.md).

- Backend: **2,193 passed**. The new combined journey covers clarification,
  improvement, a catalog TV in chat, exhausted allowance, free manual editing and
  saving, explicit publishing, anonymous reads, revocation and application restart.
- Frontend: **551 passed**, including both opt-in checks against the new live
  artifacts. TypeScript and production build passed. The existing bundle-size
  warning remains.
- Browser: desktop and 390 px mobile checked; no horizontal overflow or console
  errors. Chat/improvements disable at zero actions; a manual bed-color change
  still saved. The partial-scene label now survives save/reopen.
- Live progress: reconstruction and both follow-ups reached terminal SSE
  snapshots; reconnecting with their cursors caused no additional inference.

## One live room session

Used the existing five-photo bedroom case, the previously supplied 3 × 6 × 3 m
dimensions, Sol 6.1 for construction and Luna for other calls. Two clarification
answers resumed the original analysis. The result retained **16 objects and two
openings**; a backpack remained explicitly unresolved. Geometry passed and all
reference major objects were present. Offline reference checks still flagged
chair color, floor pattern and desk-to-window distance, so full fidelity did not
pass.

The openness request proposed no change because the room scored 9.3/10. The chat
request recognized the catalog TV, but both proposed placements failed access or
collision checks. No improvement or TV was accepted. An offline geometry probe
found a valid TV location after four probes, without model calls or scene changes.
**Choosing valid placements remains an optimization-quality follow-up.** This run
does not demonstrate successful autonomous optimization.

Reconstruction and two follow-ups consumed exactly three actions. A fourth
execution returned `429` before provider dispatch. Answers, retries, reads, SSE,
manual edits and save/reopen did not consume additional actions. A fresh app
instance still reported zero remaining room actions and two remaining daily
sessions. Foreign access was denied. The real room was never published; public
sharing used a separate synthetic fixture.

## Recorded API usage

| Stage | Calls | Estimated USD |
| --- | ---: | ---: |
| Reconstruction, repairs, review and ratings | 6 | $0.0899640 |
| Openness request | 1 | $0.0019821 |
| TV refinement and correction attempt | 2 | $0.0042732 |
| **Total** | **9** | **$0.0962193** |

Provider usage: 214,361 input tokens and 18,873 output tokens; all nine calls have
complete usage. This estimate uses [OpenAI standard short-context prices](https://developers.openai.com/api/docs/pricing),
verified October 27. It includes rejected attempts and is not a billing invoice.
The under-$0.10 target was met on this run; future repairs and accepted-candidate
reviews can change the cost.

Private outputs, usage records, evaluation findings, screenshots and logs are in
the ignored `artifacts/product-flow-final-20261027/` directory. Replay contracts
without provider calls:

```bash
ROOMETRY_LIVE_ARTIFACTS="$PWD/artifacts/product-flow-final-20261027" npm --prefix frontend test
uv run pytest tests/test_product_flow.py
```

README is unchanged. No external publishing or production configuration changes
were made.
