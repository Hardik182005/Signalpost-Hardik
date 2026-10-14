# Signalpost

A research agent for Norwegian companies. Given a nine-digit organisation number, it assembles
what can be established about that company from the official register and the company's own
website, and writes one terminal JSONL envelope per input — every claim carrying the source,
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
- Reads the Brønnøysund open-data APIs for legal identity, roles, annual accounts, group
  structure and registered subunits.
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
- Writes deterministic template summaries. **No language model is involved at any point.**
- Enforces the evaluator's request, time and spend budgets by reservation before dispatch, so
  a limit is refused rather than overrun.

Nine field families are emitted. Four are registry-backed and work well
(`legal_identity`, `financials`, `people`, `locations`); five depend on the company's own
website and are weak (`web_presence`, `contact`, `business_description`, `activity`, `jobs`).
The measured per-family rates are in [`EVAL.md`](EVAL.md).

## Setup

Python 3.12+ and [`uv`](https://docs.astral.sh/uv/). No account, no API key, no environment
variable.

```
uv sync
```

The project is deliberately not installed as a package (`[tool.uv] package = false`), so
`uv sync` only resolves dependencies and the checkout itself is the unit of reproduction.
`main.py` puts `src/` on `sys.path` and reconfigures stdout and stderr to UTF-8 (for Norwegian
names on a Windows console); it has no other behaviour.

**Credentials required: none.** There is no `.env`, no secret to supply, and nothing in
`src/signalpost/` reads an environment variable. Verify it:

```
grep -rn "environ\|getenv\|openai\|anthropic" src/signalpost/
```

That returns nothing. See [`MODELS_AND_APIS.md`](MODELS_AND_APIS.md).

## Running the agent

The evaluator command, exactly as it was run to produce the measured results below:

```
uv run main.py --input data/corpus/corpus.jsonl --limit 100 \
               --output out/eval100b.jsonl \
               --run-id eval100b
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

Defaults match the locked evaluator envelope: `--max-requests 2000`, `--max-seconds 2700`,
`--max-spend-usd 10.0`, `--concurrency 8`, `--max-pages 5`. Default output is `out/run.jsonl`.

**There is no `--report` flag.** The run report path is derived from the output stem, so
`--output out/eval100b.jsonl` writes `out/eval100b-report.json`.

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

From `out/eval100b-report.json`, run 2026-09-13T15:12:36Z to 15:19:05Z:

| Measure | Value |
| --- | --- |
| Envelopes | 100 (22 `completed`, 77 `partial`, 1 `failed`) |
| Claims available / emitted | 3,072 / 3,948 |
| Evidence records | 3,073 |
| Verified websites | 22 of 100 |
| Outbound requests | 797 of the 2,000 cap |
| Wall clock | 389.0 s of the 2,700 s cap |
| Third-party spend | $0.00 of the $10.00 cap |
| Exactly one envelope per input | true |

These are **live operational metrics**. They say what the run did. They say nothing about
whether the published facts are correct — no human accuracy review has been performed, so
there is no precision figure, no recall figure and no wrong-company rate. That distinction is
the subject of [`EVAL.md`](EVAL.md), which separates fixture checks, automated integrity
checks, live operational metrics and independently reviewed research quality, and marks the
last of the four **unavailable**.

`eval100b` predates the two most recent pipeline commits (`23a7c21`, `1e06568`), and one of its
22 verified websites (935654866) carries the evidence defect described below. There is no
100-company run of the current code; see
[`EVAL.md`](EVAL.md#which-code-produced-which-run).

**The 1,000-company run.** A first run over the full frozen corpus completed on 2026-09-14
(1,000 envelopes, exactly one per input, 0 `failed`, $0.00 under a $0 cap, 7,413 requests,
2,545.3 s, on generation-run ceilings of 40,000 requests and 14,400 s rather than the evaluator's
per-100 limits). **It has been superseded.** An offline integrity audit found that for 2 of its
186 verified websites the identity evidence cited a snapshot that was not stored in the envelope;
the cause was snapshot deduplication by URL, fixed in commit `1e06568`. Its files are archived
under `out/superseded/` with the suffix `-23a7c21`. The corpus is being regenerated on the fixed
commit; the regenerated totals will be in `out/submission-profiles-report.json` and are
**pending** at the time of writing. Details and every superseded figure, labelled, are in
[`EVAL.md`](EVAL.md#run-submission-1000--full-corpus).

## Tests

```
uv run pytest tests/ -q
```

Offline and deterministic. The agent's tests cover the availability/value invariants,
claim-key stability, evidence resolution, the identity gate's known failure patterns, budget
behaviour under concurrency, URL safety, prompt-injection handling, refresh idempotency, and
storage of every snapshot a website claim cites. The suite also holds tests for the web
interface and the packager, and 104 tests (`tests/test_poc.py`) for the legacy
`norway_company_agent` package, which is not in the submitted run path. On 2026-09-14 at
`102a12a`: 437 passed, with the `ui` extra installed; without it the 46 UI tests are skipped.
Per-file counts are in [`EVAL.md`](EVAL.md#1-automated-integrity-checks).

They prove the rules are enforced. They do not prove the rules are right.

Two offline audits check a finished run's output rather than the code:

```
uv run python scripts/signalpost/check_integrity.py out/submission-profiles.jsonl \
    --snapshot-root out --expected data/corpus/organisation-numbers.txt \
    --report out/integrity-report.json
uv run python scripts/signalpost/check_idempotency.py out/submission-profiles.jsonl \
    --report out/idempotency-report.json
```

The first follows every claim to its evidence and snapshot and re-hashes stored bodies; the
second diffs each envelope's claims against themselves and counts false changes. On the
superseded 1,000-company run they found the 2 evidence defects above, re-hashed 4,556 stored
bodies without a mismatch, and replayed 40,291 claims with 0 false changes. Neither checks
whether a claim is true. Results for the regenerated run are pending.

## Web interface

A read-only interface over a finished run's output:

```
uv sync --extra ui
uv run --extra ui serve.py --input out/submission-profiles.jsonl --port 8765
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
- **22 of 100 companies got a verified website** in `eval100b` (186 of 1,000 in the superseded
  1,000-company run). The external half of the coverage is weak, and `jobs` (2.0% of slots in
  `eval100b`) is not working.
- **The only external source in a run is the company's own website.** Nine connector scripts
  under `scripts/` (Brave, LinkedIn, news, reviews, and others) are disabled and not in the
  submitted run path.
- **Registry timeouts are real.** In `eval100b`, which ran before the registry retry settings
  were raised, one company terminated `failed` and 13 lost registry-backed claims. The superseded
  1,000-company run, with the raised settings, had 0 `failed` — an observation over a different
  sample, not a demonstrated fix.
- **A detected evidence defect did not reach the run report.** The in-run validator recorded
  the cited-snapshot defect inside the affected envelopes, but they stayed `completed` and the
  report said `verification.ok: true`. The defect is fixed; that reporting gap is not.
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
