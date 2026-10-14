# Build status

Updated: 2026-09-14. Working tree: `E:\All projects\Hardik SignalPost\signalpost`.
Branch `main`, remote `https://github.com/Hardik182005/signalpost.git`.

This file exists so the build can resume after a context limit. It records what is
finished and measured, what is running, and the exact next command. It is not a
statement about competition qualification, which remains `not_determined`.

## Readiness states

| State | Status | Basis |
|---|---|---|
| `implementation_complete` | reached for the engine; UI in progress | 378 tests pass; `src/signalpost/` covers the full pipeline |
| `local_validation_passed` | partial | live 100-company run measured; 1,000-company run in progress |
| `submission_artifacts_complete` | not reached | no ZIP, manifest or checksums yet |
| `reviewer_access_verified` | not reached | repository is private; no access decision authorised |
| `email_ready` | not reached | drafts not written |
| official qualification | `not_determined` | the organiser has confirmed nothing |

## Finished and measured

**Engine** — `src/signalpost/`: `models`, `registry`, `identity`, `website`, `discovery`,
`extract/`, `fetch`, `net`, `budget`, `refresh`, `store`, `synthesis`, `pipeline`,
`runner`, `corpus`. Entry point `main.py` (path shim; the project is deliberately not
installed as a package).

**Tests** — 378 pass.

```
.venv/Scripts/python.exe -m pytest -q      # 378 passed in 5.06s
```

**Requirement provenance** — `docs/provenance/competition-material.json` records URL,
retrieval time, byte count and SHA-256 for nine organiser documents.
`docs/provenance/README.md` quotes each binding requirement against its source.

**Eligible universe** — `data/downloads/signalpost-universe.jsonl.gz`,
SHA-256 `1c89710e5b01f8617e86d09fbdff4a52f2f8dbbba297e74f7164b5984f5a0384`,
411,160 eligible rows. Bulk registry: `data/downloads/brreg-enheter.csv.gz`.

**Submission corpus selection** — 1,000 unique eligible companies, reproducible.
Seed `signalpost-round-1`, algorithm `stratified-proportional-sha256-v1`, manifest
SHA-256 `620536d2f6e7e29e3f8a755910fa8501458b232f7e0bc16f12d66dfd180d1191`.
Composition: 70 sectors, 191 municipalities, 12 legal forms, 121 companies with a
registry-listed website, 152 with an employee count. Files in `data/corpus/`.

**Live 100-company run** (`out/eval100b-report.json`, 2026-09-13):

| Measure | Value |
|---|---|
| Envelopes | 100, exactly one per input, no duplicates |
| Terminal states | 22 completed, 77 partial, 1 failed |
| Outbound requests | 797 of the 2,000 ceiling (7.97 per company) |
| Wall time | 389 s of the 2,700 s ceiling |
| Third-party spend | $0.00 of the $10.00 ceiling |
| Claims | 3,072 available of 3,948; 3,073 evidence records |
| Verified websites | 22 |
| Per-company seconds | median 18.02, p90 65.59, max 111.88 |

**Refresh** — starter fixture replay reproduced (`out/refresh-demo.json`); a live
refresh over the same 100 companies recorded in `out/eval100-refresh-report.json`.

**Written documents** — `docs/PRIZES_AND_ELIGIBILITY.md`, `docs/SECURITY.md`,
`docs/provenance/`, `.env.example`, `.gitignore`, `.gitattributes`.

## Running now

**1,000-company submission corpus.** Started 2026-09-14 from the current worktree.

```
.venv/Scripts/python.exe main.py \
  --input data/corpus/corpus.jsonl \
  --output out/submission-profiles.jsonl \
  --run-id submission-1000 \
  --concurrency 8 --max-requests 40000 --max-seconds 14400 --max-spend-usd 0 \
  --snapshots out/snapshots-submission
```

Progress log: `out/submission-1000.log`. Observed throughput ~19 companies/min.
The run checkpoints; re-running the same command resumes rather than duplicating.
The request and time ceilings here are sized for a 1,000-company generation run and
are **not** the evaluator's per-100 limits, which the measured run above respects.

## Uncommitted work in the tree

`src/signalpost/{budget,fetch,pipeline,registry}.py`, `tests/signalpost/test_budget.py`:
registry retry hardening (4 attempts, 20 s timeout — 11 companies lost accounts or
roles to `data.brreg.no` timeouts in the measured run, and a 12th lost its whole
profile) and thread-local request/spend accounting so a company's own usage is
separable from the seven running beside it.

## Not started

- Final ZIP `dist/signalpost-submission-YYYYMMDD-<shortsha>.zip`, external checksum
  file, `submission-manifest.json`, `SHA256SUMS.txt`, integrity report.
- `SUBMISSION_README.md`, `SUBMISSION_CHECKLIST.md`.
- Email drafts `email/submission-email.txt` and `email/submission-email.eml`.
- Refresh pass over the 1,000-company corpus producing previous/current snapshots
  and `changes.jsonl`.
- Reviewer-access decision. The repository is private. No visibility change, reviewer
  invitation, push or deployment is authorised, and none has been performed.
- JBOX bonus: not pursued. No credentials or current technical documentation available.

## Next command

When the 1,000-company run finishes, verify cardinality against the manifest before
anything else:

```
.venv/Scripts/python.exe -c "import json,sys; sys.path.insert(0,'src'); from pathlib import Path; from signalpost.store import verify; expected=[l.strip() for l in Path('data/corpus/organisation-numbers.txt').read_text(encoding='utf-8').splitlines() if l.strip()]; print(json.dumps(verify(Path('out/submission-profiles.jsonl'), expected), indent=2))"
```

Then the refresh pass, then packaging.

## Standing constraints

The email must not be sent. Repository visibility must not change. No reviewer may be
invited and nothing may be deployed without explicit authorisation for that specific
action. No synthetic company, invented financial value, duplicated profile, fabricated
source URL or manipulated timestamp may enter submission data; if live access or budget
blocks completion, the bundle is labelled incomplete rather than padded.
