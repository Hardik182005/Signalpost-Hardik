# Limitations

What this system does not do, does not know, and gets wrong. Stated in the order a reviewer
would care about, not in the order that flatters the project.

## 1. There is no accuracy measurement

**No independent review of research quality has been performed.** Nobody has checked whether a
published website belongs to the company, whether a published phone number is current, or
whether a generated summary is right.

This means:

- there is **no** exact-entity precision figure;
- there is **no** wrong-company publication rate;
- there is **no** external company recall figure;
- the identity gate is a *design* that is tested against known failure patterns, not a design
  with a measured error rate in the wild.

The passing tests prove the rules are enforced. They do not prove the rules are right. The
distinction matters, and every number in [`EVAL.md`](EVAL.md) is labelled accordingly.

No LLM-generated labels are used as a substitute, because an LLM label is not a human gold
label — it can be wrong in the same correlated ways the pipeline is wrong. The review process
that *would* produce truth is described in [`EVAL.md`](EVAL.md#the-review-queue) and has not
been carried out.

## 2. External coverage is the weak half

In the `eval100b` run (100 companies, 2026-09-13, `out/eval100b-report.json`):

- **22 of 100 companies got a verified website.** 78 did not.
- **77 of 100 envelopes are `partial`.** 22 `completed`, 1 `failed`.
- Across the five external families, **238 of 814 claim slots are `available` (29.2%)**.
- **49 companies have zero external-family claims.**

The first 1,000-company run (on code `23a7c21`, **superseded** — see section 10; regenerated
values pending) showed the same shape, lower: **186 of 1,000 verified websites (18.6%)**, 814
`partial`, and 2,068 of 8,050 external claim slots available (25.7%); 599 of 1,000 companies had
no external-family claim. Source: `out/superseded/submission-profiles-23a7c21-report.json` and
`out/superseded/submission-profiles-23a7c21.jsonl`.

Almost every external family depends on first proving a website belongs to the company, so the
website rate is the ceiling everything downstream inherits.

Worse, the non-verified companies are **not distinguishable** in the coverage count between:

- companies that genuinely have no website;
- companies with a website we could not prove was theirs;
- companies whose site refused us.

The third case is separated (`blocked` rather than `not_available`), but the first two are not,
and that is a real reporting gap. The registry lists a website for only 12.1% of the frozen
corpus, so most companies require a guess that then has to clear the identity gate.

This is the direct, intended cost of the abstention policy in [`AGENT.md`](AGENT.md). Publishing
on a name match would raise coverage immediately and would reintroduce exactly the
wrong-company failure (GLØR AS / GLØR IKS) that the gate exists to prevent. The trade is
deliberate. It is still a limitation.

## 3. Sparse fields

Per-family availability against the slots the agent itself emitted. `eval100b` is the
100-company run above; the 1,000-company column is the **superseded** run (regenerated values
pending). The two runs used different code, so this is not a controlled comparison.

| Family | `eval100b` available / slots | Rate | Superseded 1,000 run rate | Assessment |
| --- | --- | --- | --- | --- |
| `people` | 374 / 381 | 98.2% | 99.95% (4,024 / 4,026) | Registry-backed, works |
| `financials` | 817 / 843 | 96.9% | 96.8% (8,711 / 9,000) | Registry-backed, works |
| `legal_identity` | 1,354 / 1,533 | 88.3% | 88.4% (13,556 / 15,330) | Registry-backed, works |
| `locations` | 289 / 377 | 76.7% | 74.1% (2,879 / 3,885) | Registry-backed, works |
| `web_presence` | 44 / 121 | 36.4% | 32.2% (387 / 1,200) | Weak |
| `contact` | 127 / 355 | 35.8% | 34.6% (1,267 / 3,660) | Weak |
| `activity` | 44 / 139 | 31.7% | 16.8% (197 / 1,170) | Weak |
| `business_description` | 21 / 99 | 21.2% | 16.8% (168 / 999) | Weak |
| `jobs` | 2 / 100 | **2.0%** | **4.8%** (49 / 1,021) | **Effectively not working** |

**`jobs` at 2.0% is a failure, not a sparse field.** Two published job claims across 100
companies. Some of that is real — most small Norwegian companies are not hiring, and most do
not run a careers page. But only 2 `website_careers` requests were spent in the entire `eval100b` run (40 across
1,000 companies in the superseded run), which means the careers-page discovery almost never
fired. The cause has not been isolated: it
could be link classification, the crawl priority order, or simply that these companies have no
careers page. It has not been diagnosed.

**`business_description` at 21.2%** is low for a field that should be available whenever a
landing page was read. The boilerplate filter (`looks_like_boilerplate`) is deliberately strict
so that navigation menus are not published as company descriptions; it is likely rejecting
usable prose too. The precision/recall balance of that filter has not been measured.

## 4. Registry reliability

Timeouts against `data.brreg.no` were measured, not theorised. The comment in `registry.py`
records the original measurement:

> Measured in a live 100-company run with the fetcher defaults (2 attempts, 12 s): 11
> companies lost their accounts or roles to a timeout or a connection reset from
> data.brreg.no, and a 12th lost its whole profile.

In `eval100b`, one company terminated `failed` with
`"The official registry could not be read for this company"`, having consumed 31 requests and
38.6 s trying, and 17 claims across 13 companies ended `failed`.

**`eval100b` ran before the mitigation, not after it.** The registry client's own allowance —
`REGISTRY_ATTEMPTS = 4`, `REGISTRY_TIMEOUT = 20.0` — is part of commit `23a7c21`, and
`registry.py` was last modified after `eval100b` finished (see
[`EVAL.md`](EVAL.md#which-code-produced-which-run)). The first 1,000-company run, which did use
it, had 0 `failed` envelopes and 0 `failed` claims. **That is an observation, not a
demonstrated effect:** the samples and days differ, and the two earlier 100-company runs without
the mitigation also had 0 `failed` claims. In that run each registry purpose was charged exactly
1,000 requests for 1,000 companies, so the extra attempts were never used; only the longer
timeout could have mattered. Whether the mitigation eliminates the problem, reduces it, or
merely coincided with a good day for `data.brreg.no` is unknown. That run has since been
superseded; the regenerated run's registry figures are pending.

There is also a per-company latency tail: in `eval100b`, median 18.02 s but p90 65.59 s and max
111.88 s; in the superseded 1,000-company run, median 14.73 s, p90 43.06 s, max 111.45 s. One
slow company can consume six times the median.

A separate, local failure mode was observed: in `eval100b`, the registry-declared website of
LYSE DRIFT AS was recorded as unreachable because of `PermissionError: [WinError 10013]`, a
socket-permission error on the machine running the agent. The agent treated it like any other
unreachable site and published a different candidate on supporting evidence. `fetch.py`
classifies such errors as `network` and allows a retry, but once attempts are exhausted the
candidate's verdict does not distinguish a local socket failure from the remote site being
down.

The annual-filing-years endpoint is rate-limited to roughly 30 requests/minute, and the
normalised accounts endpoint returns source errors for a small bank and insurer slice. Those
cases are reported as explicit source errors rather than as missing data, but they are not
routed to the official PDF fallback, which is unimplemented.

## 5. Disabled integrations

Nine connector scripts exist under `scripts/` and **none of them is in the submitted run
path**: Brave Search, three LinkedIn guest-endpoint scripts, Google News RSS, YouTube search,
Fagfolkguiden reviews, Google Maps normalisation, Scrapy/Playwright crawling, and the sentiment
model runner.

They are quarantined pending data-rights and held-out gates. The full table with per-connector
reasons is in [`SOURCE_RIGHTS.md`](SOURCE_RIGHTS.md#disabled-connectors).

The consequence is concrete: **the external sources in a run are the company's own website
and NAV's vacancy register.** There is no news, no reviews, no social listening, no
third-party directory. `activity` comes from the company's own pages or not at all. `jobs`
comes from the company's careers page and from NAV, which names employers by organisation
number; NAV holds a majority of publicly advertised Norwegian vacancies but not Finn.no's,
so a company hiring only through Finn.no shows no vacancies here.

NAV vacancies are found through a declared cache built at a fixed time
(`data/nav/nav-job-index.json.gz`). Ads published after the cache are matched only by the
display name NAV gives the employer, so an ad from a company whose ads use a brand name is
missed until the cache is rebuilt. Every published vacancy is still re-read from NAV in the
run itself.

## 6. No JBOX integration

**There is no JBOX integration of any kind.** No code, no connector, no exported artifact, no
evidence of one. If a prize category or eligibility item requires JBOX evidence, this entry
does not satisfy it. Status: not started, not planned within this round.

## 7. No language model

The submitted run path uses no LLM, no model provider and no personal API key (verified by
`grep -rn "openai\|anthropic" src/signalpost/`, which returns nothing). The only token it
uses is the public one NAV publishes for its vacancy feed.

The upside is in [`COSTS.md`](COSTS.md): $0.00, fully reproducible, incapable of hallucinating.

The limitation is real too:

- **Summaries read like templates**, because they are templates. They are repetitive across
  companies and will compare poorly against model-written prose.
- **No semantic understanding.** Extraction is regex and HTML structure. A fact stated in an
  unusual phrasing is missed, where a model might catch it.
- **No inference.** The system cannot conclude anything not literally present in a source.

`synthesis.render_llm_context()` exists as an optional rephrasing hook and has **no caller**.

## 8. The UI is barely verified

A read-only web interface exists under `src/signalpost/ui/` (commit `102a12a`), launched by
`uv run --extra ui serve.py --input out/submission-profiles.jsonl --port 8765` after
`uv sync --extra ui`. It binds `127.0.0.1` by default, and research from the UI is off unless
`--enable-research` is passed.

What has actually been checked:

- **46 tests** in `tests/signalpost/test_ui.py`, using FastAPI's test client over small fixture
  files: pagination, search, filters, unknown shown distinctly from zero, profile sections,
  evidence resolution, changes, CSV/JSONL downloads, escaping of hostile names and markup,
  non-http links rendered as text, research disabled by default (403) and writing nothing,
  and input files left byte-identical after browsing. They pass with the `ui` extra installed
  and are skipped without it.
- **A smoke check on 2026-09-14** with `out/eval100b.jsonl` on port 8792: the server bound
  `127.0.0.1`, printed `Loaded 100 companies` and `Research action: disabled (read-only)`, and
  `/health`, `/companies`, `/companies/979498047`, `/compare`, `/changes`, `/runs`,
  `/evidence`, `/download/companies.csv` and `/download/envelopes.jsonl` each returned 200.

What has **not** been checked:

- **whether anything the pages display is correct on real output.** The tests check rendering
  against fixtures; no person has read a page built from a real run against the envelope it came
  from.
- **the research and batch actions.** `--enable-research` lets the interface start the real
  pipeline. That path has not been exercised at all.
- **any browser.** No page has been opened in one, screenshotted, or checked for layout,
  accessibility or behaviour on a narrow screen.
- **the full 1,000-company file.** The smoke check loaded 100 envelopes; loading and page
  times over the regenerated 1,000-company output have not been measured.

Treat the interface as a convenience for reading run output, not as a verified deliverable.

## 9. Run-to-run variance is not characterised

Two 100-company runs hours apart over the same input produced 25 (`eval100refresh`) and 22
(`eval100b`) verified websites. **That difference was a code change, not variance:** the earlier
runs predate commit `49a0795`, which tightened the identity gate, and one of the three extra
websites was the wrong-company `glor.no` for GLØR AS.

The closest like-for-like observation is the first 100 companies in `eval100b` and in the
superseded 1,000-company run: the same 22 companies got a published website, and one of them
(LYSE DRIFT AS) got a different value because of the local socket error described in section 4.
Those runs also used different code.

No run has been repeated on identical code over identical input, so variance has not been
separated from code changes. Any single-run figure in [`EVAL.md`](EVAL.md) should be read with
that in mind.

## 10. The 1,000-company run was superseded by a defect fix

**The first 1,000-company run completed and was then superseded.** It ran on 2026-09-14,
09:19:56Z–10:02:22Z, on the code committed as `23a7c21`. Its files are archived under
`out/superseded/` with the suffix `-23a7c21`. The corpus is being regenerated on commit
`1e06568`, writing to `out/submission-profiles.jsonl` and
`out/submission-profiles-report.json`. **At the time of writing that run is in progress and
every regenerated figure is pending.** 1,000-company figures quoted anywhere in these documents
are from the superseded run and are labelled as such.

### Defect found and fixed: evidence citing a snapshot that was not stored

**What was wrong.** For 2 of 186 verified websites in the superseded run (935654866 and
997712897), the website claim's identity evidence cited a corroboration-page snapshot that was
not stored in the envelope. The evidence chain — claim to evidence to snapshot — was broken at
the last link for those two claims.

**Why.** Snapshots were deduplicated by URL. The website claim cites the corroboration pages;
when the crawl fetched the same URL again and the page rendered differently (a dynamic page), the
second fetch was a different snapshot, only the crawl's copy was kept, and the cited one was
dropped.

**Fix.** Commit `1e06568`: `WebsiteResearcher._cited_snapshots()` in `src/signalpost/website.py`
stores every snapshot from the page visits and the corroboration pages, deduplicated by snapshot
id. Tests: `tests/signalpost/test_website.py::TestCitedSnapshots`.

**How it was found.** Not by the run report. The new offline audit
`scripts/signalpost/check_integrity.py` flagged 2 `evidence_unresolved_snapshot` problems
(`out/superseded/integrity-report-23a7c21.json`).

**What this reveals beyond the bug:**

- **The in-run validator had detected it and nothing acted on it.** Both envelopes carry
  `operations.validation_problems: 1` and a `validation_failed` error, yet both terminated
  `completed`, and the run report still said `verification.ok: true`. `store.verify()` does not
  look at validation problems. That reporting gap is **not** fixed by `1e06568`.
- **`eval100b` has the same defect.** Company 935654866 carries `validation_problems: 1` there
  too, for the same reason — 1 of its 22 verified websites. `eval100b` is still the per-100
  evidence and has not been regenerated.
- **The archived integrity report overstates the problem count.** It also lists 1,000
  `financial_claim_missing_currency` and 1,000 `financial_claim_missing_unit` problems on
  boolean `accounts_audited` claims, from an earlier revision of the audit script. The committed
  script exempts booleans; re-run against the archived file, it reports only the 2 real
  problems. See [`EVAL.md`](EVAL.md#offline-run-audits).

The same audit re-hashed all **4,556** stored snapshot bodies of the superseded run against their
recorded SHA-256 with no mismatch, and `scripts/signalpost/check_idempotency.py` replayed
**40,291 claims over 1,000 companies with 0 false changes**
(`out/superseded/idempotency-report-23a7c21.json`). Both are internal-consistency checks: the
replay diffs stored claims against themselves, not against a re-crawl. Audit results for the
regenerated run (`out/integrity-report.json`, `out/idempotency-report.json`) are pending.

Note also that the corpus manifest describes 1,000 selected companies, and the run writes to
`out/submission-profiles.jsonl`. Any reference elsewhere to a `data/profiles/` directory does
not correspond to a path this code produces.

## 11. Known scope gaps

- **Historical financials are capped at 3 years** (`max_years=3`).
- **No official PDF fallback.** When the normalised accounts endpoint fails, there is no
  fallback to the annual-account PDF copy, even though the endpoint is documented.
- **No shareholder register, no beneficial-owner register.** Both are out of scope for rights
  and handling reasons; ownership beyond the official group structure is unavailable.
- **Group relationships come only from `konsernstruktur`.** When that endpoint returns nothing,
  the relationship is `not_available` — never inferred from shared names or addresses.
- **Job postings capped at 25 per company, activity items at 20.** A company with more is
  truncated.
- **Crawl depth is 5 pages** plus at most 2 corroboration pages. Facts deeper in a site are
  not found.
- **No JavaScript rendering** in the submitted path. A site that renders its content client-side
  yields little. The Scrapy/Playwright script that could address this is disabled.
- **Norwegian and English only** in practice; extraction heuristics are tuned for Bokmål
  conventions and are untested against Nynorsk-specific phrasing.

## 12. Unresolved organiser rules

These are contradictions or gaps in the published competition material that have **not been
resolved by the organiser**. They are recorded as unresolved rather than resolved by our own
interpretation, because guessing at a rule and then reporting against our guess would be a
worse failure than saying we do not know.

| Item | The problem | How we proceeded |
| --- | --- | --- |
| **Stale scoring language** | Material states "external intelligence is 55/100", which does not reconcile with the scoring v2 breakdown (35 coverage + 30 accuracy/identity/evidence + 20 refresh + 10 synthesis + 5 usability = 100). Two different weightings are in circulation. | Built to scoring v2. Claimed no score under either. |
| **Four-version allowance** | The allowance for multiple submitted versions is ambiguous about what counts as a version and how versions are scored against each other. | Prepared one submission. Made no assumption about version scoring. |
| **Sparse-field scoring** | It is not specified how a field that is legitimately `not_applicable` or genuinely absent for an entity is treated in coverage — whether abstention is neutral or penalised. This directly determines whether the abstention policy is correct or self-defeating. | Report all six availability states with reasons and let the scorer apply its own rule. |
| **Failed/missed-run averaging** | How a failed run, or a company that could not be researched, is averaged into a score is not stated. | Every company terminates with an envelope and an explicit state, so no company is silently absent whichever rule applies. |

Additionally, **official qualification status is `not_determined`**. No result has been
published for this entry, and the run report emits that string literally rather than a score.

## 13. Repository inconsistencies

Honest housekeeping items a reviewer will notice:

- **`pyproject.toml` still names the project `norway-company-agent-poc`, version 0.1.0**, not
  `signalpost`. The package metadata lags the rename.
- **The legacy `src/norway_company_agent/` package is still present** alongside
  `src/signalpost/`. The submitted run path is `signalpost` only, but
  `scripts/run_refresh_replay.py` imports the legacy package — which means
  `out/refresh-demo.json` is evidence about the starter code, not about the submitted refresh
  implementation. This is called out again in [`EVAL.md`](EVAL.md) because it would otherwise
  be easy to misread.
- **`docs/SECURITY.md` refers to a curated evidence set under `docs/evidence/`**, which does
  not exist in the tree.
- **The contracted operation keys are missing from the 100-company outputs.**
  `OUTPUT_CONTRACT.md` requires `requests`, `runtime_ms` and `third_party_cost_usd` in each
  envelope's `operations`. The current code emits them (`pipeline.py`), but they were introduced
  with commit `23a7c21`, so `out/eval100.jsonl`, `out/eval100-refresh.jsonl` and
  `out/eval100b.jsonl` do not carry them. Those runs' `verification.ok: true` does not attest
  them, because `store.verify()` checks only top-level keys.
- **There is no 100-company run of the current code.** `eval100b`, the per-100 evidence, predates
  both `23a7c21` and `1e06568`. See [`EVAL.md`](EVAL.md#which-code-produced-which-run).
- **Documentation cross-links are split across two directories.** Several files in `docs/`
  link to `SOURCE_RIGHTS.md`, `COSTS.md`, `MODELS_AND_APIS.md`, `EVAL.md`, `LIMITATIONS.md`
  and `SUBMISSION_CHECKLIST.md` as if they were siblings inside `docs/`, while the required
  file list places them at the repository root, where they are. Those relative links do not
  resolve.

## 14. What would change these limitations

In rough order of value:

1. A human-adjudicated accuracy review over a frozen sample, including hard cases. This is the
   single largest gap.
2. Diagnosing and fixing `jobs` at 2.0%.
3. Completing and reporting the regenerated 1,000-company run, and a 100-company run of the
   current code inside the evaluator envelope.
4. Making a detected validation problem visible in the run report and the terminal state,
   rather than only inside the envelope.
5. Characterising run-to-run variance over repeated runs of the same input.
6. Measuring the boilerplate filter's rejection rate to recover `business_description`
   coverage.
7. Enabling one lawful external source to lift external coverage above the company's own site.
8. Finishing and verifying the UI.
