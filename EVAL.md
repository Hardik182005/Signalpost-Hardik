# Evaluation

## What this document can and cannot tell you

Four kinds of evidence appear below, and they are not interchangeable. Mixing them is how a
project talks itself into believing it is more validated than it is, so they are kept apart:

| Category | What it proves | Status here |
| --- | --- | --- |
| **Fixture checks** | Behaviour against saved responses. No network. | Available — 1 artifact, with caveats |
| **Automated integrity checks** | The code enforces its own rules, and a run's output is internally consistent. No network. | Available — the test suite (483 passed on 2026-10-13; 104 of them exercise the legacy `norway_company_agent` package, not the submitted run path); two offline run audits, run on the submission smoke test |
| **Live operational metrics** | What actually happened against real sources | Available — the runs of 2026-10-13 below, and the earlier runs further down, each labelled with the code that produced it |
| **Independently reviewed research quality** | Whether the published facts are *correct* | **Unavailable.** No independent review has been performed. |

The fourth row is the important one. **There is no accuracy measurement in this project.**
Nothing here establishes that a published website belongs to the company, that a published
phone number is current, or that a summary is right. The identity gate is a *design* for
avoiding wrong-company publication, and the tests show the design is implemented — they do not
show the design works on the open web.

**Official qualification status: `not_determined`.** No score, no recall figure, and no
qualification claim appears in this repository. Nothing has been verified by the organiser or
by any third party.

## Follow-up audit of 2026-10-14

See [the measured improvement audit](docs/evidence/codex-20261014/README.md) for the
fresh baseline, revised run, precision rechecks, and controlled synthesis comparison.
The final suite passes 506 tests. Six mixed-scope financial summaries, three understated
workplace totals, and incomplete citations in 28 count-bearing sentences are corrected
on the same saved 1,000-company claims (these checks overlap). No official score or
independent recall measurement is inferred from this.

## Runs of 2026-10-13 (prior working tree)

Changes measured here, against the September runs below: request budgets that scale with the
batch, registry pacing for the open-data API, NAV vacancies, stricter job and address
extraction, the rewritten summary, and output resumption limited to the same run.

| Run | Inputs | Envelopes | Requests | Wall clock | Websites | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `smoke100-holdout` | 100 held-out (`data/holdout/`) | 100, one per input | 770 / 2,000 | 135.5 s | 14 | Integrity audit clean; idempotency 0 false changes; refresh rerun 0 changes. [Evidence](docs/evidence/smoke100-holdout/) |
| `v2-100` + refresh | first 100 of corpus | 100 + 100 | 765 | 204.5 s | 21 | Live refresh rerun: 0 changes |
| `v3-1000` | 1,000 corpus | 1,000, one per input | 7,442 / 20,000 | 532.3 s | 182 | 14 duplicate `accounts_audited` keys found by the integrity audit, fixed after this run |

The same 1,000 companies took 2,539 s in September, of which ~2,410 s was the 0.6 s throttle
on the single registry host. NAV vacancy coverage on the corpus is small and exact: 8 of
1,000 companies had an active NAV ad naming them or a registered workplace as employer, and
all 8 got published vacancies, 12 ads in all.

Job postings from company sites dropped from 34 to 13 between the September run and
`v3-1000` because the listing extractor now reads only careers pages and rejects calls to
action, staff listings and benefit lines; every one of the 34 September postings was reviewed
and the large majority were not vacancies.

## Corpus and splits

| Split | Size | File | Purpose |
| --- | --- | --- | --- |
| Universe | 411,160 eligible rows | `data/downloads/signalpost-universe.jsonl.gz` | Sampling frame |
| Frozen corpus | 1,000 companies | `data/corpus/corpus.jsonl` | The submitted corpus |
| Evaluation slice | First 100 of the corpus | `--limit 100` over the same file | The 100-input runs |
| Smoke slice | 10 | `--limit 10` | Development checks |

The corpus was selected by `corpus.select()`, algorithm
`stratified-proportional-sha256-v1`, seed `signalpost-round-1`. The manifest is
`data/corpus/selection.json`:

| Field | Value |
| --- | --- |
| Universe SHA-256 | `1c89710e5b01f8617e86d09fbdff4a52f2f8dbbba297e74f7164b5984f5a0384` |
| Eligible rows | 411,160 |
| Selected | 1,000 |
| Manifest SHA-256 (`selection.json` → `manifest_sha256`) | `620536d2f6e7e29e3f8a755910fa8501458b232f7e0bc16f12d66dfd180d1191` — SHA-256 of the 1,000 organisation numbers joined by `\n`, **without** a trailing newline |
| SHA-256 of the file `data/corpus/organisation-numbers.txt` | `40583e0f7067c4cedb98b8a7057c2f123c4cc196de890854601656f7468111e4` — the same 1,000 numbers, one per line, file ending in a newline |
| Generated | 2026-09-13T13:48:44Z |
| Sectors | 70 distinct NACE divisions |
| Municipalities | 191 |
| Registry website present | 121 (12.1%) |
| Employee count present | 152 (15.2%) |

Stratification is proportional over two-digit NACE division, and selection within a stratum is
by SHA-256 rank of the organisation number — deterministic and re-derivable from the seed, not
a stored random draw. Top municipalities: Oslo 191, Bergen 64, Trondheim 36, Stavanger 31,
Bærum 28, Kristiansand 27, Asker 24, Sandnes 20. Legal forms: AS 914, BRL 23, ESEK 21, NUF 11,
STI 11, SA 6, FLI 6, DA 2, ENK 1, ASA 1, SAM 1, KS 1.

The two hashes cover the same list and differ only by that final newline; both were recomputed
on 2026-09-14 from the files in `data/corpus/`. Quote the one whose definition you are checking.

**There is no held-out gold split, because there is no gold data.** The 100-company slice is
not a test set in any meaningful sense — it is the first 100 rows of the corpus, used to
measure operational behaviour. No labels exist for it.

## 1. Automated integrity checks

```
uv run pytest tests/ -q
# 437 passed, 2 warnings in 16.65s
```

Run 2026-09-14 against the working tree at commit `102a12a`. No network access; these are
offline structural checks. The count changes as tests are added; the command is the reference,
not the number.

**Not all 437 test the submitted agent.** Per-file counts from `uv run pytest tests/ -q
--collect-only` on the same date:

| File | Tests | What it pins |
| --- | --- | --- |
| `tests/test_poc.py` | 104 | The legacy `norway_company_agent` package (Brave search, sentiment, external connectors). **Not in the submitted run path.** |
| `tests/signalpost/test_ui.py` | 46 | The optional web interface (commit `102a12a`) |
| `tests/signalpost/test_packaging.py` | 11 | Submission packager paths and safe extraction; the review queue builder (commit `1a583f5`) |
| `tests/signalpost/test_availability.py` | 36 | Six availability states, five terminal states, the value/state invariant, evidence resolution, claim-key stability, envelope shape |
| `tests/signalpost/test_budget.py` | 21 | The locked envelope defaults, reservation-before-dispatch under concurrency, refusal rather than overrun, redirect/retry charging, soft stop, fair share |
| `tests/signalpost/test_identity.py` | 41 | Normalisation, MOD-11, the website gate, "the name is one fact", the profile gate |
| `tests/signalpost/test_net.py` | 50 | Scheme/port/credential refusal, private-address refusal, secret redaction, URL canonicalisation |
| `tests/signalpost/test_website.py` | 18 | Plain-HTTP fallback rules, terminal failures, social-profile emission, corroboration, every cited snapshot stored (`TestCitedSnapshots`) |
| `tests/signalpost/test_extract.py` | 55 | Norwegian number parsing, job titles, boilerplate, parked domains, prompt injection, social handles |
| `tests/signalpost/test_refresh.py` | 18 | Idempotency, false-change immunity, real changes, materiality, observation times |
| `tests/signalpost/test_store.py` | 15 | Write uniqueness under concurrency, resumption, crash tolerance, verification |
| `tests/signalpost/test_discovery.py` | 22 | IDNA, name variants, precision guards, priors-never-publish |

That leaves 276 tests on the agent's own modules (the last nine rows).

What this category proves: the rules described in [`AGENT.md`](AGENT.md),
[`IDENTITY_RESOLUTION.md`](IDENTITY_RESOLUTION.md) and [`DATA_SCHEMA.md`](DATA_SCHEMA.md) are
actually enforced by the code, including under concurrency.

What it does not prove: that those rules produce correct research about real companies.

### In-run integrity checks

These run during every execution, not only in the test suite:

- `models.validate_result()` per company — terminal state valid, no duplicate claim keys, no
  cross-company attribution, every material claim carries resolvable evidence, every evidence
  record resolves to a snapshot. Defect count lands in `operations.validation_problems`.
- `store.verify()` per run — envelope count, unique companies, duplicates, invalid terminal
  states, missing required keys, `exactly_one_per_input`. Written into the run report under
  `verification`; `main()` exits non-zero if `ok` is false.
- `refresh.replay_check()` per company — diffing a company against its own output must yield
  zero changes.

In all three completed live runs, `verification.ok` was `true`, `exactly_one_per_input` was `true`,
and duplicates, missing, unexpected and invalid-terminal-state lists were all empty.

**What `verification.ok` does not cover.** `store.verify()` checks the file's cardinality and
that each envelope has its top-level keys. It does not read `operations.validation_problems`,
and a `validation_failed` entry in an envelope's `errors` does not change its terminal state.
So a run can report `verification.ok: true` while individual envelopes carry evidence defects.
That is exactly what happened; see the defect below.

### Offline run audits

Two scripts added in commit `1e06568` audit a finished run's output file. Neither makes a
network request, and neither changes the output.

**`scripts/signalpost/check_integrity.py`** — per envelope: every available claim cites
evidence (or is derived); every cited evidence id exists; every evidence record's snapshot is
in the same envelope with the same `content_sha256`; financial claims carry a reporting period,
and monetary amounts carry a currency and unit (boolean flags such as `accounts_audited` are
exempt); claim and evidence timestamps match a UTC `Z` pattern; evidence URLs are http(s);
claim keys are unique; unavailable claims carry no value and do carry a reason; stored snapshot
paths are relative. With `--snapshot-root`, each distinct stored body is re-hashed against its
recorded SHA-256. With `--expected`, it also runs `store.verify()` against a manifest. It exits
non-zero if any problem is found.

```
uv run python scripts/signalpost/check_integrity.py out/submission-profiles.jsonl \
    --snapshot-root out --expected data/corpus/organisation-numbers.txt \
    --report out/integrity-report.json
```

**`scripts/signalpost/check_idempotency.py`** — rebuilds each envelope's stored claims and
diffs them against the same envelope with `refresh.replay_check()`, counting change events. Any
event is a false change.

```
uv run python scripts/signalpost/check_idempotency.py out/submission-profiles.jsonl \
    --report out/idempotency-report.json
```

**What these audits do not prove.** The integrity audit checks that the evidence chain is
internally consistent and that stored bodies have not changed since retrieval. It says nothing
about whether a claim is true or about the right company; the script's own report says so. The
idempotency audit diffs stored claims against themselves: it shows that claim keys and value
normalisation are stable when re-read, which is the precondition for refresh precision. It is
**not** a re-crawl of unchanged sources, and it is not a measurement of false changes against
the live web.

#### Results on the superseded 1,000-company run

Input: `out/superseded/submission-profiles-23a7c21.jsonl` (SHA-256
`9d265fbbaefc98b4904fafe989ff7f15d052e4bf5659700515f09c7ef8994c36`; the reports record the
path as `out/submission-profiles.jsonl` because they were produced before it was archived — the
SHA-256 matches).

| Audit | Result | Evidence |
| --- | --- | --- |
| Idempotency | 1,000 companies, 40,291 claims replayed, **0 false changes** | `out/superseded/idempotency-report-23a7c21.json` (checked 2026-09-14T11:45:39Z) |
| Snapshot body re-hash | **4,556** distinct stored bodies re-hashed; 0 missing, 0 mismatches | `out/superseded/integrity-report-23a7c21.json` (checked 2026-09-14T11:47:16Z) |
| Evidence chain | **2 problems**: `evidence_unresolved_snapshot` for 935654866 and 997712897 | same |
| Cardinality (`--expected`) | 1,000 envelopes, 1,000 unique, 0 missing, 0 unexpected, 0 duplicates | same |

Counts from the same report: 1,000 envelopes, 40,291 claims (31,238 available), 31,247
evidence records, 4,565 snapshot entries across envelopes (4,556 distinct bodies).

**The archived integrity report also lists 1,000 `financial_claim_missing_currency` and 1,000
`financial_claim_missing_unit` problems, all on `accounts_audited` claims.** Those came from an
earlier revision of the script that did not exempt boolean values; the committed script does.
Re-running the committed script against the archived file on 2026-09-14 (once without
`--snapshot-root`, and once with a snapshot root pointing at
`out/superseded/snapshots-submission-23a7c21`) reported only the 2 `evidence_unresolved_snapshot`
problems, and 4,556 bodies re-hashed. That re-run's report was written to a scratch directory,
not into the repository; the command is reproducible from the file and commit above.

The results for the regenerated run will be written to `out/integrity-report.json` and
`out/idempotency-report.json`. **Pending** — those files do not exist at the time of writing.

### Defect found and fixed: cited snapshots not stored

**Found** by the integrity audit above, on the superseded 1,000-company run: for 2 of 186
verified websites (935654866 and 997712897), the website claim's identity evidence cited a
corroboration-page snapshot that was not in the envelope. The evidence pointed at nothing, so
the chain could not be followed to a stored retrieval.

**Cause.** `WebsiteResearcher` collected snapshots from the crawl's page visits, keyed by URL.
The website claim also cites the corroboration pages. When the crawl fetched a corroborating URL
a second time and the page rendered differently (a dynamic page), the two fetches were different
snapshots with different hashes; only the crawl's copy was stored, and the corroboration copy
the evidence cited was dropped.

**Fix.** Commit `1e06568`, `src/signalpost/website.py`: `WebsiteResearcher._cited_snapshots()`
collects snapshots from both the page visits and the corroboration pages, deduplicated by
snapshot id rather than by URL. Tests: `tests/signalpost/test_website.py::TestCitedSnapshots`.

**It was also present, and also detected in-run, earlier.** The in-run validator caught both
cases: each envelope carries `operations.validation_problems: 1` and an `errors` entry of type
`validation_failed` ("evidence '…' references unknown snapshot '…'"). Both envelopes still
terminated `completed`, and the run report's `verification.ok` stayed `true`, because neither
reads that signal. Checking `eval100b` for the same signal (2026-09-14) finds one case: 935654866
again, `validation_problems: 1`, terminal state `completed`. So `eval100b`, the per-100 evidence,
contains the same defect in 1 of its 22 verified websites. `eval100b` has not been regenerated.

**Not fixed by this commit:** the gap that let a detected defect leave the run reporting
`completed` and `verification.ok: true`. A reader of the run report alone would not see it.

## 2. Live operational metrics

### Run `eval100b` — the headline run

```
uv run main.py --input data/corpus/corpus.jsonl --limit 100 \
               --output out/eval100b.jsonl --run-id eval100b
```

Dataset: first 100 rows of `data/corpus/corpus.jsonl`.
Date: 2026-09-13, started 15:12:36Z, completed 15:19:05Z.
Evidence: `out/eval100b-report.json` and `out/eval100b.jsonl`.
Pipeline version 1.0.0.

| Metric | Value |
| --- | --- |
| Envelopes | 100 |
| Terminal states | 22 `completed`, 77 `partial`, 1 `failed` |
| Claims available / total | 3,072 / 3,948 |
| Evidence records | 3,073 |
| Verified websites | 22 (22.0%) |
| Requests | 797 of 2,000 (7.97 per company) |
| Wall clock | 389.0 s of 2,700 s |
| Third-party spend | $0.00 of $10.00 |
| Budget denials | none |
| Seconds per company | median 18.02, p90 65.59, max 111.88 |
| `budget_exhausted_after` | null |
| Verification | `ok: true` |
| Official qualification field in report | `not_determined` |

### Run `eval100refresh` — refresh over the same 100

```
uv run main.py --input data/corpus/corpus.jsonl --limit 100 \
               --output out/eval100-refresh.jsonl --run-id eval100refresh \
               --previous-snapshot out/eval100.jsonl
```

Date: 2026-09-13, completed 14:32:55Z. Evidence: `out/eval100-refresh-report.json`.

| Metric | Value |
| --- | --- |
| Envelopes | 100 (25 `completed`, 75 `partial`) |
| Claims available / total | 3,193 / 4,064 |
| Verified websites | 25 (25.0%) |
| **Changes total / material** | **1 / 1** |
| Requests | 743 (7.43 per company) |
| Wall clock | 312.7 s |
| Spend | $0.00 |

The single change was a URL canonicalisation artifact, not a real-world event. It is analysed
in full in [`REFRESH.md`](REFRESH.md#measured-results), including why a clean zero-change
figure against a like-for-like baseline is **pending verification**.

Note the two runs differ: `eval100b` verified 22 websites, `eval100refresh` verified 25 over
the same input. **That difference is not run-to-run variance; the runs used different code.**
`eval100refresh` completed at 14:32:55Z, before commit `49a0795` ("Refuse wrong-entity sites,
gate social profiles, corroborate ambiguous landings", 2026-09-13T15:16:51Z). Of the three
websites it published that `eval100b` did not, one is `https://glor.no/` for GLØR AS
(994492144) — the wrong-company case described in
[`IDENTITY_RESOLUTION.md`](IDENTITY_RESOLUTION.md). The other two (ARCTIC MASCOT AS,
REGIONPLANKONTORET AS) are `ambiguous` under the current gate; whether those two earlier
publications were right has not been reviewed. `eval100refresh` is therefore evidence about the
refresh mechanism, not about the current identity gate.

### Which code produced which run

This matters because the measured runs do not all come from the same code. Timestamps below are
UTC; commit dates are from `git log`, file times from the working tree.

| Run | When | Code state | How that is established |
| --- | --- | --- | --- |
| `eval100`, `eval100refresh` | 2026-09-13, completed by 14:32:55Z | Before commit `49a0795` (identity-gate tightening) | Commit timestamp; outputs publish `glor.no`, which the current gate refuses |
| `eval100b` | 2026-09-13, 15:12:36Z–15:19:05Z | Before the changes later committed as `23a7c21` ("Harden registry retries and book request usage per worker thread") | Its envelopes lack the `requests`, `runtime_ms` and `third_party_cost_usd` operation keys that `23a7c21` introduced; `registry.py`, `fetch.py`, `budget.py` and `pipeline.py` were last modified 15:21–15:23Z, after the run finished |
| `submission-1000`, first run (**superseded**) | 2026-09-14, 09:19:56Z–10:02:22Z | The code committed unchanged as `23a7c21` at 11:45:08Z (then uncommitted) | At the time of checking, no source file outside `ui/` had been modified after 2026-09-13 15:23Z and `git diff 23a7c21 -- src/signalpost main.py` was empty; all 1,000 envelopes carry the new operation keys. Archived under `out/superseded/` with suffix `-23a7c21` |
| `submission-1000`, regenerated | 2026-09-14, in progress at the time of writing | Commit `1e06568` (the cited-snapshot fix above) | **Pending** — to be confirmed from `out/submission-profiles-report.json` when the run completes |

**Consequence: there is no 100-input run of the current code inside the evaluator envelope.**
The per-100 evidence, `eval100b`, comes from an earlier code state (before `23a7c21` and before
`1e06568`). The 1,000-company runs use generation-run ceilings (below). The superseded run's
per-company figures — 7.41 requests and a median of 14.73 s — suggest a 100-input run would fit
the envelope comfortably, but that is a projection, not a measurement.

### Run `submission-1000` — full corpus

> **Status of the figures in this section.** Every number below is from the **first
> 1,000-company run, on code `23a7c21`, which has been superseded** because of the cited-snapshot
> defect described above. Its files are archived as
> `out/superseded/submission-profiles-23a7c21.jsonl`,
> `out/superseded/submission-profiles-23a7c21-report.json`,
> `out/superseded/submission-1000-23a7c21.log` and
> `out/superseded/snapshots-submission-23a7c21/`. The corpus is being regenerated on commit
> `1e06568` with the same command, writing to the canonical paths named below. **Regenerated
> values are pending**; where a table has a "Regenerated" column, it will be filled from
> `out/submission-profiles-report.json` when that run completes. The superseded values should
> not be quoted as the final 1,000-company result.

```
.venv/Scripts/python.exe main.py --input data/corpus/corpus.jsonl     --output out/submission-profiles.jsonl --run-id submission-1000     --concurrency 8 --max-requests 40000 --max-seconds 14400     --max-spend-usd 0 --snapshots out/snapshots-submission
```

Invoked with the project virtualenv's interpreter directly rather than through `uv run`; the
entry point and interpreter are the same.
Dataset: all 1,000 rows of `data/corpus/corpus.jsonl`.
Canonical evidence paths (regenerated run): **`out/submission-profiles-report.json`**,
`out/submission-profiles.jsonl`, log `out/submission-1000.log`, snapshots
`out/snapshots-submission/`, audits `out/integrity-report.json` and
`out/idempotency-report.json`.
Superseded run: 2026-09-14, started 09:19:56Z, completed 10:02:22Z, pipeline version 1.0.0,
evidence at the `out/superseded/…-23a7c21` paths above.

| Metric | Superseded run (`23a7c21`) | Regenerated run (`1e06568`) |
| --- | --- | --- |
| Envelopes | 1,000 (1,000 unique) | pending |
| Terminal states | 186 `completed`, 814 `partial`, 0 `failed` | pending |
| Claims available / total | 31,238 / 40,291 | pending |
| Claim states | 31,238 `available`, 8,310 `not_available`, 723 `ambiguous`, 20 `blocked`, 0 `failed`, 0 `not_applicable` | pending |
| Evidence records | 31,247 | pending |
| Verified websites | 186 (18.6%) | pending |
| Requests | 7,413 of a 40,000 ceiling (7.41 per company) | pending |
| Wall clock | 2,545.3 s of a 14,400 s ceiling | pending |
| Third-party spend | $0.00, under a cap set to $0.00 | pending |
| Budget denials | none | pending |
| Seconds per company | median 14.73, p90 43.06, max 111.45 | pending |
| Changes total / material | 0 / 0 — no `--previous-snapshot`, so every company is a first observation; not refresh evidence | pending |
| `budget_exhausted_after` | null | pending |
| Verification | `ok: true`, `exactly_one_per_input: true`, 0 missing, 0 unexpected, 0 duplicates | pending |
| Envelopes with `validation_problems` > 0 | 2 (the cited-snapshot defect) | pending |
| Official qualification field in report | `not_determined` | pending |

For the superseded run, the organisation numbers in the output were also checked independently
against `data/corpus/organisation-numbers.txt` (2026-09-14): the two sets are identical, 1,000
each.

**The ceilings in this run are not the evaluator's.** 40,000 requests and 14,400 s were sized
for generating a 1,000-company corpus. The evaluator envelope is 100 inputs / 2,000 requests /
2,700 s, and this run is not evidence of compliance with it. The per-100 evidence remains
`out/eval100b-report.json`, with the code-version caveat above.

`--max-spend-usd 0` makes the spend ledger refuse any positive charge (`budget.charge()` raises
once spend would exceed the cap), so $0.00 here is enforced, not merely observed.

**Registry failures.** The superseded run had 0 `failed` envelopes and 0 `failed` claims. `eval100b` had
1 `failed` envelope and 17 `failed` claims across 13 companies. The registry retry settings
(`REGISTRY_ATTEMPTS = 4`, `REGISTRY_TIMEOUT = 20.0`) were in place for that run and not for
`eval100b`. **That is an observation, not a demonstrated effect.** The samples differ, the days
differ, and `eval100` and `eval100refresh` — also without the new settings — had 0 `failed`
claims too. Registry reliability varies between runs by itself. One further detail argues
against reading it as the retry setting's effect: every attempt is charged to the budget under
its purpose, and in the superseded run `registry_entity`, `registry_roles`, `registry_accounts`
and `registry_subunits` each show exactly 1,000 requests for 1,000 companies — so no registry
retry was spent at all. Only the longer timeout could have made a difference, and whether it did
is not measured. (`eval100b`, by contrast, shows 106, 110, 110 and 105 for 100 companies.)

**The same 100 companies in both runs.** The first 100 corpus rows appear in both `eval100b`
and the superseded run; this comparison has not yet been repeated on the regenerated run. A website was published for the same 22 companies in both. For one of them the
value differed: LYSE DRIFT AS (917837570) got `https://www.lysedrift.no/` in `eval100b` and
`https://lyseentreprenor.no/` in the superseded run. In `eval100b` the registry-declared
`www.lyseentreprenor.no` was rejected as unreachable with
`PermissionError: [WinError 10013]` — a socket-permission error on the machine running the
agent, not a response from the site — and `lysedrift.no` then published on three supporting
evidence groups. In the superseded run the registry-declared site was reachable and published on the
decisive `registry_website` signal. Both pages name Lyse Drift at the registered address;
which is the company's primary site has not been reviewed. The company that terminated `failed`
in `eval100b` (998645417) is `partial` in the superseded run.

## 3. Local proxy metrics

Everything in this section is **our own measurement against our own reference set**. None of it
is a score, and none of it is recall against truth.

### The metric we cannot compute

The rubric defines coverage as:

```
coverage = 0.70 × company_recall + 0.30 × claim_recall
```

Both terms are *recall*, which requires a denominator of facts that are known to exist. We do
not have one. Nobody has established, for these 100 companies, which of them really have a
website, a public phone number, or open vacancies. **So company recall and claim recall are
unavailable, and no number in this repository should be read as either.**

### The proxy we can compute: availability rate

**Reference set:** the 100 companies of the `eval100b` run.
**Denominator:** the claim slots Signalpost itself emitted — that is, fields the agent
considered for those companies.
**Weights:** the 0.70 / 0.30 split above, applied to availability rather than recall.
**Source:** `out/eval100b.jsonl`, computed 2026-09-14.

| Family | Available | Total slots | Rate |
| --- | --- | --- | --- |
| `people` | 374 | 381 | 98.2% |
| `financials` | 817 | 843 | 96.9% |
| `legal_identity` | 1,354 | 1,533 | 88.3% |
| `locations` | 289 | 377 | 76.7% |
| `web_presence` | 44 | 121 | 36.4% |
| `contact` | 127 | 355 | 35.8% |
| `activity` | 44 | 139 | 31.7% |
| `business_description` | 21 | 99 | 21.2% |
| `jobs` | 2 | 100 | 2.0% |
| **All families** | **3,072** | **3,948** | **77.8%** |
| **Five external families** | **238** | **814** | **29.2%** |

Company-level, external families only: **51 of 100** companies have at least one available
external-family claim. Distribution: 49 companies with 0 external families covered, 29 with 1,
2 with 2, 15 with 3, 5 with 4, none with all 5.

Applying the rubric's weights to these availability figures gives **0.70 × 0.5100 +
0.30 × 0.2924 = 0.4447**.

**Read that number with the following four caveats, all of which are disqualifying for any
score-like interpretation:**

1. It is **not** the coverage metric. It substitutes availability for recall.
2. **We choose the denominator.** The claim slots are the ones our own code decided to emit. A
   different field list would produce a different percentage from identical research. A
   self-selected denominator cannot support a comparative claim.
3. It says nothing about correctness. A published-but-wrong phone number counts as "available"
   here.
4. It is one run of 100 companies on one day.

It is reported because reporting nothing would be worse, and because the *shape* is
informative: internal registry families are well covered, external families are not, and `jobs`
is close to empty.

### The same proxy over the 1,000-company run

> Figures below are from the **superseded** run (`23a7c21`), computed 2026-09-14 from what is
> now `out/superseded/submission-profiles-23a7c21.jsonl`. Values for the regenerated run are
> **pending**.

**Reference set:** the 1,000 companies of `submission-1000` (superseded run).
**Denominator:** the claim slots Signalpost emitted for them.
**Weights:** the same 0.70 / 0.30 split, applied to availability.
**Source:** `out/superseded/submission-profiles-23a7c21.jsonl`; for the regenerated run,
`out/submission-profiles.jsonl` (pending).
All four caveats above apply unchanged; in addition, this run used different code from
`eval100b` (see [which code produced which run](#which-code-produced-which-run)), so the two
tables are not a controlled comparison.

| Family | Available (superseded) | Total slots (superseded) | Rate (superseded) | Rate (regenerated) |
| --- | --- | --- | --- | --- |
| `people` | 4,024 | 4,026 | 99.95% | pending |
| `financials` | 8,711 | 9,000 | 96.8% | pending |
| `legal_identity` | 13,556 | 15,330 | 88.4% | pending |
| `locations` | 2,879 | 3,885 | 74.1% | pending |
| `contact` | 1,267 | 3,660 | 34.6% | pending |
| `web_presence` | 387 | 1,200 | 32.2% | pending |
| `activity` | 197 | 1,170 | 16.8% | pending |
| `business_description` | 168 | 999 | 16.8% | pending |
| `jobs` | 49 | 1,021 | 4.8% | pending |
| **All families** | **31,238** | **40,291** | **77.5%** | pending |
| **Five external families** | **2,068** | **8,050** | **25.7%** | pending |

Company-level, external families only (superseded run): **401 of 1,000** companies have at
least one available external-family claim. Distribution: 599 companies with 0 external families
covered, 217 with 1, 17 with 2, 124 with 3, 35 with 4, 8 with all 5. Regenerated run: pending.

Applying the same weights to the superseded run: **0.70 × 0.4010 + 0.30 × 0.2569 = 0.3578**
(regenerated run: pending). Lower than the
100-company figure, on every external family except `jobs`. The first 100 companies of the
corpus are not representative of the other 900 in this respect, which is itself a reason not to
read the 100-company proxy as a population estimate.

### What the shape actually shows

The honest reading of that table is that **external coverage is the weak half of this system**.
Only 22 of 100 companies got a verified website in `eval100b`, and 186 of 1,000 in the
superseded `submission-1000` run (regenerated run pending); since almost every external family depends on having a verified site,
everything downstream inherits that ceiling. `jobs` at 2.0% (100 companies) and 4.8% (1,000
companies, superseded run) is effectively not working. This is discussed as a limitation
rather than a metric in [`LIMITATIONS.md`](LIMITATIONS.md).

## 4. Fixture checks

`out/refresh-demo.json` — a saved-response replay over one company (923609016), two modules:

| Field | Value |
| --- | --- |
| Corpus | "evaluator-owned deterministic old/new source snapshot fixture" |
| Profiles | 1 |
| Expected / observed changes | 2 / 2 |
| TP / FP / FN | 2 / 0 / 0 |
| Precision / recall | 1.0 / 1.0 |
| `evidence_complete` | true |
| `idempotent_rerun` | true |
| `qualification_passed` | true |

**Three things this artifact is not:**

1. **Not a live measurement.** `old_requests: 2` and `new_requests: 2` are saved-response
   reads, not network calls.
2. **Not a qualification result.** `qualification_passed: true` is scoped to that one sample
   check. Official qualification status is `not_determined`.
3. **Not evidence about the submitted code.** `scripts/run_refresh_replay.py` imports
   `norway_company_agent.refresh.diff_datasets` — the **legacy starter package**, not
   `src/signalpost/`. This file therefore validates the starter's refresh implementation, not
   the refresh code documented in [`REFRESH.md`](REFRESH.md).

Precision and recall of 1.0 over a two-event fixture from a different codebase is not a
meaningful accuracy claim, and it is listed here only so that a reviewer who finds the file
knows exactly what it is.

## 5. What has not been measured

Stated plainly, because these absences are the most important part of this document.

| Not measured | Why it matters |
| --- | --- |
| **Accuracy of any published claim** | No human has checked whether a published website, phone number or description is correct. |
| **Wrong-company publication rate** | The identity gate is designed to prevent it and tested against known failure patterns. Its real-world rate is unknown. |
| **External company recall** | Requires knowing which companies really have a website. No such labelled set exists here. |
| **External exact-entity precision** | Same. Requires adjudicated ground truth. |
| **Claim recall against truth** | Same. |
| **Run-to-run stability** | No two measured runs used identical code, so variance has not been isolated from code changes. Over the same 100 companies, `eval100b` and the superseded `submission-1000` run published websites for the same 22, with one differing value. Not characterised. |
| **Accuracy at 1,000** | Operational metrics exist for the superseded 1,000-company run, and are pending for its regeneration. Correctness is exactly as unmeasured there as at 100. |
| **Idempotency against unchanged live sources** | The offline audit diffs stored claims against themselves. No re-crawl of unchanged sources has been compared claim-for-claim with its baseline on the current code. |
| **UI behaviour beyond a route smoke check** | See below. The pages have never been reviewed for correctness. |

### On LLM-generated labels

No LLM-generated labels are used anywhere in this evaluation, and none would be acceptable if
they were. An LLM-generated label is not an independent human gold label — a model asked
whether a site belongs to a company can be wrong in exactly the same correlated ways the
extraction pipeline is wrong, which makes it a measurement of agreement, not of truth.

### The review queue

Human-reviewed truth is **unavailable**. A review queue now exists as a tool; no review has been
done with it.

```
uv run python scripts/signalpost/build_review_queue.py out/submission-profiles.jsonl \
    --output out/review-queue.jsonl --summary out/review-queue-summary.json
```

`scripts/signalpost/build_review_queue.py` (commit `1a583f5`, tests in
`tests/signalpost/test_packaging.py::TestReviewQueue`) draws a frozen sample, seed
`signalpost-review-1`, of up to 130 items in six strata:

| Stratum | Items | Question put to the reviewer |
| --- | --- | --- |
| `website_published` | 40 | Is this the website of exactly this legal entity? |
| `website_withheld_ambiguous` | 20 | The agent withheld this candidate. Is it this company's website? |
| `website_none_found` | 15 | No website was published. Does this company have one? |
| `financial_revenue` | 25 | Does the cited filing report this revenue, currency, unit and period? |
| `contact_from_website` | 20 | Is this contact detail on the cited page, and does it belong to this entity? |
| `people_role` | 10 | Does the cited register record show this person in this role? |

Within a stratum, candidates are ordered by SHA-256 of `seed|stratum|claim_key` and at most one
item is taken per company, so the same input and seed give the same queue. Three of the six
strata are abstentions or misses, so a completed review could estimate recall as well as
precision. Each item carries the claim, its evidence (URL, source class, retrieval time, content
SHA-256, locator) and, for website items, the identity verdict. `reviewer_1`, `reviewer_2`
(verdict, notes, time) and `adjudicated_verdict` are empty; the summary file records
`review_status: "unreviewed"`, `reviewed_items: 0`, and computes no metric.

**Where it stands.** The queue for the regenerated run (`out/review-queue.jsonl`,
`out/review-queue-summary.json`) had not been built at the time of writing: it depends on the
regenerated corpus, which is **pending**. As a check of the tool only, the builder was run on
2026-09-14 against the superseded file `out/superseded/submission-profiles-23a7c21.jsonl`
into a scratch location (not kept in `out/`): it drew 130 items, the full quota in every
stratum. Because the queue is drawn from a run's output, the regenerated queue's items will
differ.

The queue is a precondition for an accuracy number, not one.

The material needed for adjudication already exists in the output, which is the point of the
evidence chain: every published claim carries an evidence id → a snapshot id → a source URL, a
retrieval timestamp, a content SHA-256 and a locator. A reviewer opening a website claim sees
the exact candidate URL, the identity verdict, its confidence, and the **reason in words**
(for example, `"3 independent evidence groups (contact, location, name; score 1.10)"`).

What a defensible review still needs beyond the queue:

- the sample to include deliberate hard cases — parent/franchise relationships, name
  collisions — which a hash-ordered draw does not guarantee;
- two independent human reviewers per item, blind to each other, with adjudication of
  disagreements (the protocol the summary file records);
- separate counts for exact-entity precision, wrong-company publications (target: zero), and
  recall against the reviewed subset;
- every judgement recorded against the claim key and the content hash it was made on, so the
  review stays valid as sources change.

None of that has been done. Until it is, this project has **no accuracy number**, and the
absence is reported here rather than filled with a proxy.

## 6. Reproducing these results

**Offline checks** — no network, deterministic:

```
uv sync
uv run pytest tests/ -q
```

Expect no failures (437 passed on 2026-09-14 at `102a12a`, in an environment synced with
`--extra ui`). `tests/signalpost/test_ui.py` calls `pytest.importorskip` for `fastapi`,
`jinja2` and `httpx`, so after a plain `uv sync` its 46 tests are **skipped**, not passed; use
`uv sync --extra ui` to run them (`httpx` is in the dev group, which `uv sync` installs by
default).

**Offline audits of a finished run** — no network:

```
uv run python scripts/signalpost/check_integrity.py out/submission-profiles.jsonl \
    --snapshot-root out --expected data/corpus/organisation-numbers.txt \
    --report out/integrity-report.json
uv run python scripts/signalpost/check_idempotency.py out/submission-profiles.jsonl \
    --report out/idempotency-report.json
```

Both exit non-zero on any problem. To audit the superseded run, pass
`out/superseded/submission-profiles-23a7c21.jsonl`; its `stored_path` values begin
`snapshots-submission\`, so `--snapshot-root` must be a directory in which that name resolves to
`out/superseded/snapshots-submission-23a7c21`.

**A live 100-company run** — needs network, no credentials:

```
uv run main.py --input data/corpus/corpus.jsonl --limit 100 \
               --output out/check.jsonl --run-id check
```

Then read `out/check-report.json`. Figures will not match `eval100b` exactly, because the live
web changes between runs; `verification.ok` should be `true`, there should be exactly 100
envelopes, and spend should be `0.0`.

**A refresh** — see [`REFRESH.md`](REFRESH.md#replay-instructions-for-a-reviewer).

**Deterministic re-run against stored snapshots**: add `--offline`.

## 7. Remaining uncertainty

1. **Is the research correct?** Unknown. No independent review. This is the largest gap.
2. **How often does the identity gate publish the wrong company?** Unknown in the wild. Known
   to reject the specific failure patterns that have been encoded as tests.
3. **How much does abstention cost in real recall?** Unknown. 78 of 100 (and 814 of 1,000 in the superseded run)
   companies had no published website; the split between "genuinely has none" and "has one we could not prove"
   has not been established.
4. **Does it hold at 1,000?** The superseded run was operationally complete: 1,000 envelopes, exactly one per input, 0 `failed`, $0.00, under generation-run ceilings — but 2 envelopes carried an evidence defect that its own report did not surface. The regenerated run is pending. On coverage, the proxy was lower at 1,000 than at 100. On correctness, unknown at either scale.
5. **How stable is a run?** Not characterised. The 22-versus-25 difference between the
   100-company runs was a code change, not variance. The closest like-for-like observation is
   the same 100 companies in `eval100b` and the superseded `submission-1000` run: identical set of 22 published
   websites, one differing value caused by a local socket error. No repeated runs of identical
   code over identical input exist.
6. **Does the UI work?** Partly verified. It is committed (`102a12a`) and has 46 tests in
   `tests/signalpost/test_ui.py`, run with FastAPI's test client against small fixture files.
   A smoke check on 2026-09-14 ran `uv run --extra ui serve.py --input out/eval100b.jsonl
   --port 8792`: it bound `127.0.0.1`, printed `Loaded 100 companies` and
   `Research action: disabled (read-only)`, and returned HTTP 200 on `/health`, `/companies`,
   `/companies/979498047`, `/compare`, `/changes`, `/runs`, `/evidence`,
   `/download/companies.csv` and `/download/envelopes.jsonl`. That, and what the tests assert,
   is the whole of what has been checked. **No person has read the pages against the envelopes
   for correctness, no browser has rendered them, and the research action has not been run.**
7. **Registry reliability.** Timeouts against `data.brreg.no` were measured. One company in
   `eval100b` terminated `failed` and 13 lost at least one registry-backed claim. The registry
   client's attempts and timeout were raised afterwards; the (superseded) 1,000-company run that
   followed had 0 `failed` envelopes and 0 `failed` claims. Whether the change caused that is not
   established — two earlier runs without it also had 0 `failed` claims.
