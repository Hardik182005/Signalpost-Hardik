# November 2026 revision — evidence

All runs used the 100 held-out organisation numbers in `inputs.txt` (the same file as
`data/holdout/organisation-numbers.txt`) at commit `772f48d`, with default budgets, on 6 Nov 2026.

| File | What it is | Command |
|---|---|---|
| `envelopes.jsonl.gz` | 100 terminal envelopes, run `nov2026-final` (10:08–10:25 UTC) | `uv run python scripts/signalpost/run_batch.py --organisations data/holdout/organisation-numbers.txt --output out/nov2026-final/envelopes.jsonl --run-id nov2026-final --expected-count 100` |
| `envelopes-report.json` | Run report: 14 completed / 86 partial / 0 failed, 967 of 2,000 requests, 1,047 s of 2,700 s, $0.00 | (written by the run) |
| `refresh.jsonl.gz`, `refresh-report.json` | Live refresh against that output (10:25–10:43 UTC): 24 changes, **0 material**. All 24 are `news` availability flips between "HTTP 429" and "timeout" | same command with `--output out/nov2026-final/refresh.jsonl --run-id nov2026-final-refresh --previous-snapshot out/nov2026-final/envelopes.jsonl` |
| `refresh-replay.json` | Change detector replayed on real claims: unchanged 100 companies → 0 events. Changed revenue (+10 %) 76/76 detected as material. Failed website source 14/14 detected as non-material availability change. **passed** | `uv run python scripts/signalpost/refresh_replay.py out/nov2026-final/envelopes.jsonl --report out/nov2026-final/refresh-replay.json` |
| `integrity-report.json` | Every claim resolves to evidence and snapshots; no duplicate keys. **ok** | `uv run python scripts/signalpost/check_integrity.py …` |
| `idempotency-report.json` | 100 companies, 5,853 claims, 0 false changes | `uv run python scripts/signalpost/check_idempotency.py …` |
| `BEFORE_AFTER.md`, `before-after.json` | 13 Oct baseline vs this run, with caveats | `uv run python scripts/signalpost/before_after.py …` (usage in the script header) |
| `MANUAL_REVIEW.md` | Review of all 14 published websites and 10 social sets, with method and flags | — |
| `tests.txt` | `uv run pytest tests/ -q`: **521 passed** | — |
| `clean-install.txt` | `git archive` of the commit into an empty directory, then `uv sync --frozen` (no extras: no torch, scrapy, playwright or fastapi) and a one-company run | — |
| `ui-checks.md` | Browser checks of the public JBox website | — |

Official qualification status: `not_determined`.
