# Signalpost

A research agent for Norwegian companies. Given a nine-digit organisation number, it assembles
what can be established about that company from the official register, NAV's vacancy register
and the company's own website, and writes one terminal JSONL envelope per input — every claim carrying the source,
the retrieval time, a content hash and a locator that points at the exact span the value came
from.

The design principle throughout is that **an unproven claim is worse than a missing one**. The
agent abstains rather than guesses, and it reports the abstention with a reason instead of a
zero. That trade is deliberate and it is expensive; the cost is measured in
[`LIMITATIONS.md`](LIMITATIONS.md).

**Official qualification status: `not_determined`.** No score, ranking or independent
verification is claimed anywhere in this repository.

## What it actually does

- Resolves identity against the organisation number as the stable legal key, validating it by
  MOD-11 rather than by length.
- Reads the Brønnøysund open-data APIs for legal identity, registered activity, roles, annual
  accounts (several years where filed), group structure and registered subunits.
- Reads NAV's open vacancy register, which names each ad's employer by organisation number,
  and publishes a company's open vacancies only after re-reading each ad in the run and
  confirming it is active and that the employer is the company or one of its registered
  workplaces. See [`CRAWLERS.md`](CRAWLERS.md#nav-vacancies).
- Proposes website candidates (registry-declared, mailbox domain, name variants including
  æ/ø/å as punycode), pre-filters them by DNS, and then **refuses to publish any of them**
  unless the identity gate is satisfied — one decisive signal, or two independent evidence
  groups. See [`IDENTITY_RESOLUTION.md`](IDENTITY_RESOLUTION.md).
- Crawls an accepted website for contact details, description, people, activity and job
  postings, obeying `robots.txt`, at most 5 pages plus 2 corroboration pages.
- Records a snapshot entry (URL, status, retrieval time, SHA-256) for the responses its evidence
  cites, and with `--snapshots DIR` also stores each body content-addressed, so a published
  value can be traced back to the bytes it was read from. That chain was broken for 2 of 186
  verified websites in the superseded 1,000-company run; the cause is fixed in `1e06568`.
- Detects change against a previous run's output using stable semantic claim keys, and
  separates material change from cosmetic churn. See [`REFRESH.md`](REFRESH.md).
- Writes a deterministic summary that answers, in order: what the company is and does, who
  runs it, how big it is, how its latest filed numbers look (with margin and equity ratio
  calculated from those figures), where to find it, whether it is hiring, what it has
  published lately, what changed since the last run, and what could not be established.
  Every sentence lists the claims it was built from. **No language model is involved.**
- Enforces the evaluator's request, time and spend budgets by reservation before dispatch, so
  a limit is refused rather than overrun.

Nine field families are emitted. Four are registry-backed and work well
(`legal_identity`, `financials`, `people`, `locations`); five depend on the company's own
website and are weak (`web_presence`, `contact`, `business_description`, `activity`, `jobs`).
The measured per-family rates are in [`EVAL.md`](EVAL.md).

## Setup

Python 3.12+ and [`uv`](https://docs.astral.sh/uv/). No account and no API key.

```
uv sync
```

The project is deliberately not installed as a package (`[tool.uv] package = false`), so
`uv sync` only resolves dependencies and the checkout itself is the unit of reproduction.
`main.py` puts `src/` on `sys.path` and reconfigures stdout and stderr to UTF-8 (for Norwegian
names on a Windows console); it has no other behaviour.

**Credentials required: none.** The NAV vacancy feed is read with the public token NAV
publishes for open use, fetched by the run itself. Every environment variable the run reads
is optional; see [`MODELS_AND_APIS.md`](MODELS_AND_APIS.md#environment-variables).

## Running the agent

The evaluator command — Builderr's batch file in, one envelope per company out:

```
uv run python scripts/signalpost/run_batch.py --organisations <batch.jsonl|.json|.txt> \
    --output out/envelopes.jsonl --run-id <id> --expected-count <N>
```

It accepts the starter's input forms, refuses a malformed batch before making any request,
and passes every other option through to `main.py`. The direct form:

```
uv run main.py --input data/corpus/corpus.jsonl --output out/run.jsonl --run-id run-1
```

Full option list (`uv run main.py --help`):

```
uv run main.py [-i INPUT] [-o OUTPUT] [--run-id ID] [--limit N]
               [--concurrency N] [--max-requests N] [--max-seconds N]
               [--max-spend-usd N] [--max-pages N]
               [--snapshots DIR] [--previous-snapshot PATH] [--cutoff DATE]
               [--no-financials] [--no-structure] [--no-resume]
               [--offline] [--quiet]
               [organisation_numbers ...]
```

**Budgets scale with the batch.** Unless a flag or a `SIGNALPOST_*` variable sets them:
20 outbound requests per input company (the locked 2,000-per-100 ratio), 2,700 s of wall
clock, $10 of spend (actual: $0), and 8 companies in flight — 24 above 200 inputs. A
1,000-company batch therefore runs with a 20,000-request cap, not 2,000.

**Resuming.** A run resumes an existing output file only when that file is an interrupted
attempt at the same `--run-id` over the same companies. Any other file at the output path —
for example yesterday's batch — is renamed `<name>.superseded-<time>.jsonl` and kept, never
returned as this run's answer.

**There is no `--report` flag.** The run report path is derived from the output stem, so
`--output out/envelopes.jsonl` writes `out/envelopes-report.json`.

### Inputs

Any one of:

| Form | Example |
| --- | --- |
| Corpus `.jsonl` or `.jsonl.gz` | `--input data/corpus/corpus.jsonl` |
| A text file, one organisation number per line (`#` comments allowed) | `--input data/corpus/organisation-numbers.txt` |
| Positional organisation numbers | `uv run main.py 998645417 979498047` |
| stdin | `cat numbers.txt \| uv run main.py -o out/run.jsonl` |

Inputs are de-duplicated in order, so a company mentioned twice still produces exactly one
envelope. `--limit N` takes the first N after de-duplication.

The frozen 1,000-company corpus is in `data/corpus/`, with its selection manifest
(`selection.json`: algorithm `stratified-proportional-sha256-v1`, seed `signalpost-round-1`,
universe SHA-256 recorded, 411,160 eligible rows).

### Outputs

| File | Contents |
| --- | --- |
| `<output>.jsonl` | One terminal envelope per input company |
| `<output>-report.json` | Run report: config, results, budget usage by purpose and host, verification, `official_qualification` |
| `--snapshots DIR` | Content-addressed raw response bodies, only if this flag is given |

Each envelope carries the top-level keys `schema_version`, `organisation_number`, `run`,
`identity`, `claims`, `evidence`, `snapshots`, `synthesis`, `changes`, `refresh`, `errors` and
`operations`. The terminal state — one of `completed`, `partial`, `not_found`, `blocked`,
`failed` — is at `run.terminal_status`; there is no top-level `status` and no `coverage` key. Every claim carries one of six availability states —
`available`, `not_available`, `blocked`, `not_applicable`, `ambiguous`, `failed` — and a claim
that is not `available` is structurally forbidden from carrying a value. The full schema is in
[`DATA_SCHEMA.md`](DATA_SCHEMA.md), and compatibility with the starter's contract is noted
against [`OUTPUT_CONTRACT.md`](OUTPUT_CONTRACT.md).

### Refresh

Pass a previous run's `.jsonl` as the previous state. There is no database and no server.

```
uv run main.py --input data/corpus/corpus.jsonl --limit 100 \
               --output out/refresh.jsonl --run-id refresh-1 \
               --previous-snapshot out/eval100b.jsonl
```

### Offline replay

`--offline` makes no network requests and runs against stored snapshots, for a deterministic
comparison. `--cutoff DATE` fixes what "today" means for recency, so a replay is reproducible.

## Measured results

**Submission smoke test — 100 held-out companies** (`data/holdout/`, a stratified sample of the
universe that shares no company with the development corpus), run 2026-10-13 with the
evaluator command and default budgets. Report and envelopes:
[`docs/evidence/smoke100-holdout/`](docs/evidence/smoke100-holdout/).

| Measure | Value |
| --- | --- |
| Envelopes | 100, exactly one per input (14 `completed`, 86 `partial`, 0 `failed`) |
| Claims available / emitted | 4,761 / 5,754, each with evidence |
| Verified websites | 14 of 100 |
| Outbound requests | 770 of the 2,000 cap |
| Wall clock | 135.5 s of the 2,700 s cap (median 5.7 s, p90 14.0 s per company) |
| Third-party spend | $0.00 |
| Refresh rerun against that output | 0 changes, 0 false changes (`refresh-report.json`) |
| Integrity audit | every claim resolves to its evidence, no duplicate claim keys |

**Official batch size — the 1,000-company development corpus**, same code, default budgets
(`out/dev/v3-1000-report.json`): 1,000 envelopes, exactly one per input, 0 `failed`;
7,442 requests of a 20,000 cap; **532 s** wall clock at concurrency 24 (the same corpus took
2,539 s before the registry pacing fix); 182 verified websites; 192 company-owned social
profiles; 25 vacancies at 13 companies, 12 of them at 8 companies confirmed by NAV against the employer's
organisation number. The integrity audit of that run found 14 duplicate claim keys — company
and group accounts for one year sharing an `accounts_audited` key — fixed before the smoke
test above, which audits clean.

These are **live operational metrics**. They say what the run did, not whether every
published fact is correct — no independent human accuracy review has been performed, so
there is no measured precision or wrong-company rate. [`EVAL.md`](EVAL.md) separates fixture
checks, automated integrity checks, live operational metrics and independently reviewed
research quality, and marks the last **unavailable**.

## Tests

```
uv run pytest tests/ -q
```

Offline and deterministic. The agent's tests cover the availability/value invariants,
claim-key stability, evidence resolution, the identity gate's known failure patterns, budget
behaviour under concurrency, URL safety, prompt-injection handling, refresh idempotency, and
storage of every snapshot a website claim cites. The suite also holds tests for the web
interface and the packager, and 104 tests (`tests/test_poc.py`) for the legacy
`norway_company_agent` package, which is not in the submitted run path. On 2026-10-13:
483 passed, with the `ui` extra installed.

They prove the rules are enforced. They do not prove the rules are right.

Two offline audits check a finished run's output rather than the code:

```
uv run python scripts/signalpost/check_integrity.py out/envelopes.jsonl \
    --expected <inputs.txt> --report out/integrity-report.json
uv run python scripts/signalpost/check_idempotency.py out/envelopes.jsonl \
    --report out/idempotency-report.json
```

The first follows every claim to its evidence (and, with `--snapshot-root`, re-hashes stored
bodies); the second diffs each envelope's claims against themselves and counts false changes.
Neither checks whether a claim is true. Results for the submission smoke test are in
[`docs/evidence/smoke100-holdout/`](docs/evidence/smoke100-holdout/).

## Web interface

A read-only interface over a finished run's output:

```
uv sync --extra ui
uv run --extra ui serve.py --input out/smoke100/envelopes.jsonl --port 8765
```

Pages: `/companies`, `/companies/{org}`, `/compare`, `/changes`, `/runs`, `/evidence`; CSV and
JSONL downloads under `/download/`. It binds `127.0.0.1` unless `--host` is given. It opens the
`.jsonl` it is given plus the `*-report.json` files beside it and writes nothing back. The one
action that would perform work, running the pipeline for a company, is off unless
`--enable-research` is passed.

The `ui` extra (`fastapi`, `uvicorn`, `jinja2`, `python-multipart`) is optional. The agent
(`main.py` and everything outside `src/signalpost/ui/`) imports none of it. The locked runtime
dependency set without extras (`uv export --frozen --no-dev --no-hashes`) is identical at
`102a12a` and at `1e06568`: 121 lines each, no difference (checked 2026-09-14). `httpx` was added
to the dev group for the UI tests.

**Verified only partly.** Commit `102a12a` adds 46 tests (`tests/signalpost/test_ui.py`), and a
route smoke check on 2026-09-14 against `out/eval100b.jsonl` returned 200 on the pages above.
No person has checked what the pages display against the envelopes, and no browser has rendered
them. See [`LIMITATIONS.md`](LIMITATIONS.md#8-the-ui-is-barely-verified).

## Known limitations

The short version; [`LIMITATIONS.md`](LIMITATIONS.md) is the long one and is the file to read
before trusting any number here.

- **No accuracy measurement of any kind.** No independent review, no gold labels.
- **Website coverage is the weak half.** 14 of 100 held-out companies and 182 of 1,000 corpus
  companies got a verified website. Most companies in the universe are small and have none;
  of those that do, the ones whose domain is neither registered nor guessable from the name
  are missed, because no search engine is used. Website-derived families (contact,
  description, social profiles, activity) inherit that ceiling.
- **External sources are the company's own website and NAV's vacancy register.** Nine
  connector scripts under `scripts/` (Brave, LinkedIn, news, reviews, and others) are disabled
  and not in the run path.
- **NAV vacancies come from a declared cache** refreshed from the feed at run time; an ad posted
  after the cache under a brand name is missed until the cache is rebuilt
  (`scripts/signalpost/build_nav_job_index.py --refresh`).
- **Registry timeouts are real.** A registry read that fails after its retries terminates the
  company as `failed` with the reason stated.
- **No JBOX integration.**
- **No language model**, which means no hallucination and no cost, and also templated prose and
  no semantic understanding.
- **Four organiser rules are unresolved** and are recorded as unresolved rather than guessed.

## Documentation

| File | Subject |
| --- | --- |
| [`AGENT.md`](AGENT.md) | Pipeline stages, discovery, extraction, abstention, termination |
| [`IDENTITY_RESOLUTION.md`](IDENTITY_RESOLUTION.md) | The identity gate, signals, groups, failure cases |
| [`CRAWLERS.md`](CRAWLERS.md) | Adapters, retries, caching, budgets, politeness |
| [`DATA_SCHEMA.md`](DATA_SCHEMA.md) | Envelope, claims, states, evidence, locators |
| [`REFRESH.md`](REFRESH.md) | Claim keys, normalisation, materiality, measured results |
| [`EVAL.md`](EVAL.md) | What was measured, what was not, and the local proxies |
| [`LIMITATIONS.md`](LIMITATIONS.md) | What this does not do and gets wrong |
| [`SOURCE_RIGHTS.md`](SOURCE_RIGHTS.md) | Sources, licences, redistribution, personal data |
| [`MODELS_AND_APIS.md`](MODELS_AND_APIS.md) | Every network API and model provider — and their absence |
| [`COSTS.md`](COSTS.md) | Measured spend, basis, pricing date, resource use |
| [`docs/REQUIREMENTS_MATRIX.md`](docs/REQUIREMENTS_MATRIX.md) | Requirement → code → evidence → file |
| [`docs/SECURITY.md`](docs/SECURITY.md) | URL safety, secret handling, untrusted content |
| [`docs/PRIZES_AND_ELIGIBILITY.md`](docs/PRIZES_AND_ELIGIBILITY.md) | Eligibility items and their status |

## Licence and attribution

Registry data is Brønnøysundregistrene open data under NLOD 2.0. Company websites are read
under their own terms and `robots.txt`; what the envelopes publish is claims with locators, not
page copies. When `--snapshots` is given, raw response bodies are also kept locally as a working
artifact for evidence checking; that store as a whole is not for redistribution. The submission
package includes official-register bodies only, not company page bodies. Details and the full source table are in [`SOURCE_RIGHTS.md`](SOURCE_RIGHTS.md).
