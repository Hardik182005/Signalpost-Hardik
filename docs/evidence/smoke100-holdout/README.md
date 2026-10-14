# Submission smoke test — 100 held-out companies

`inputs.txt` is `data/holdout/organisation-numbers.txt`: 100 companies drawn from the
eligible universe by `stratified-proportional-sha256-v1` with seed `signalpost-holdout-1`
(`data/holdout/selection.json`). None of them is in the development corpus.

Commands, run 2026-10-13 from the repository root with default budgets:

```
uv run python scripts/signalpost/run_batch.py --organisations data/holdout/organisation-numbers.txt \
    --output out/smoke100/envelopes.jsonl --run-id smoke100-holdout --expected-count 100
uv run python scripts/signalpost/run_batch.py --organisations data/holdout/organisation-numbers.txt \
    --output out/smoke100/refresh.jsonl --run-id smoke100-holdout-refresh --expected-count 100 \
    --previous-snapshot out/smoke100/envelopes.jsonl
uv run python scripts/signalpost/check_integrity.py out/smoke100/envelopes.jsonl \
    --expected data/holdout/organisation-numbers.txt --report out/smoke100/integrity-report.json
uv run python scripts/signalpost/check_idempotency.py out/smoke100/envelopes.jsonl \
    --report out/smoke100/idempotency-report.json
```

| File | What it shows |
| --- | --- |
| `envelopes.jsonl.gz` | One terminal envelope per input company |
| `envelopes-report.json` | 100 envelopes, exactly one per input; 770 requests; 135.5 s; $0.00 |
| `refresh-report.json` | The rerun against that output: 0 changes, 0 false changes |
| `integrity-report.json` | Every claim resolves to its evidence; no duplicate claim keys |
| `idempotency-report.json` | 5,754 claims replayed, 0 false changes |

These are operational checks. No human has reviewed whether each published fact is true.
