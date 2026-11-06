# Signalpost — November 2026 revision package

Author: Hardik Hinduja. Repository: https://github.com/Hardik182005/Signalpost-Hardik, branch
`feat/signalpost-jbox-feedback-nov2026`. `SUBMISSION_MANIFEST.json` names the exact
commit in `research_commit` (full SHA) and the commit the smoke test ran on in `smoke_run_commit`.
They differ only in documentation and packaging scripts, and the packager refuses to build if
research code or locked runtime dependencies changed between them.

This ZIP is a convenience bundle prepared for review. It is not an organiser-mandated format.
**Official qualification status: `not_determined`.**

## Install and run (tested, see `verification/clean-install.txt`)

```
uv sync --frozen
uv run python scripts/signalpost/run_batch.py --organisations <batch.jsonl|.json|.txt> \
    --output out/envelopes.jsonl --run-id <id> --expected-count <N>
```

The commands run from `source/`. They need Python 3.12+ and `uv`, and no account, API key,
Node, WebGL, JBox or database. Budgets scale with the batch: 20 requests per company,
2,700 s and $10 (actual spend $0). `SIGNALPOST_NEWS=0` turns off the GDELT news step.

## Layout

| Path | Contents |
|---|---|
| `source/` | `git archive` of the research commit: pinned `uv.lock`, no `.git`, no environment files |
| `smoke100/inputs.txt` | The 100 held-out organisation numbers. This is a local reproducibility aid, not the official batch |
| `smoke100/envelopes.jsonl` | Run `nov2026-final`, 6 Nov 2026: 100 envelopes, one per input |
| `smoke100/run-report.json` | Run report: 14 completed / 86 partial / 0 failed, 967 of 2,000 requests, 1,047 s of 2,700 s, $0.00 |
| `smoke100/integrity-report.json` | Every claim resolves to evidence and snapshots |
| `smoke100/refresh-validation.json` | Change-detector replay on real claims (unchanged / changed / failed source): passed |
| `smoke100/refresh-report.json`, `smoke100/idempotency-report.json` | Live refresh (0 material changes) and same-input idempotency (0 false changes) |
| `verification/tests.txt` | Full test suite: 521 passed |
| `verification/clean-install.txt` | Clean install from `git archive` with no extras, plus a one-company run |
| `verification/research-comparison.md` | Before (13 Oct) vs after (6 Nov) on the same 100 companies, with caveats |
| `verification/manual-review.md` | Review of all 14 published websites and 21 social profiles |
| `verification/ui-checks.md` | Browser checks of the public website |
| `website/` | Deployment notes, JBox build provenance, and the displayed dataset's manifest |
| `disclosures/` | Models and APIs, costs, source rights, limitations |
| `email/` | Two unsent draft emails |
| `CHECKSUMS.sha256` | SHA-256 of every file in the package except itself. The ZIP's own checksum is in the `.zip.sha256` file next to the ZIP |

File-name mapping from the brief: `refresh-validation.json` is `refresh-replay.json` in the
repository, `research-comparison.md` is `docs/evidence/nov2026/BEFORE_AFTER.md`, and
`manual-review.md` is `docs/evidence/nov2026/MANUAL_REVIEW.md`. No `website/public-sample/`
is included because the public dataset is served from the repository (see `website/DEPLOYMENT.md`).
