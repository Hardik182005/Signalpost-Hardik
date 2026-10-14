# Crawlers

Every outbound request in a run goes through one function: `Fetcher.get()` in
`src/signalpost/fetch.py`. There is no second HTTP path, no library making its own calls, and
no way for an adapter to bypass the budget or the URL policy. The safety controls on that
single egress point are documented in [`docs/SECURITY.md`](docs/SECURITY.md); this file covers
what gets fetched, in what order, how failures are handled, what is cached, and what it costs.

## Enabled adapters

| Adapter | Module | Targets | Requests per company |
| --- | --- | --- | --- |
| Registry | `registry.py` | `data.brreg.no` entity, roles, subunits, group structure, annual accounts | ~4 |
| NAV vacancies | `nav_jobs.py` | `pam-stilling-feed.nav.no` vacancy feed and ad records | 0–15 per company, plus a shared feed refresh per run |
| Website | `website.py` | The company's own site, `robots.txt` first | 0–6 |

That is the complete list. Nothing else is contacted. Each run report records every host
reached under `budget.top_hosts`.

Adapters that exist but are **not wired in** — Brave search, LinkedIn guest endpoints, Google
News RSS, YouTube, Fagfolkguiden, Scrapy/Playwright — live under `scripts/` and are listed with
their reasons in [`SOURCE_RIGHTS.md`](SOURCE_RIGHTS.md#disabled-connectors). They are not
imported by `main.py` or `runner.py`.

## NAV vacancies

NAV's vacancy register names each ad's employer by organisation number, which makes it the
one jobs source that matches a company exactly without a website. The feed API cannot be
queried by employer, so the adapter works from a declared cache:

1. **Cache** — `data/nav/nav-job-index.json.gz`, built by
   `scripts/signalpost/build_nav_job_index.py`: every ad active when it was built, with the
   employer organisation number from the ad's own record, and the feed cursor at build time.
2. **Refresh, once per run** — a background thread reads the feed forward from that cursor to
   its end (at most `MAX_REFRESH_PAGES = 80` pages), so ads published, stopped or expired
   since the cache was built are known before companies reach this stage.
3. **Per company** — ads whose employer number is the company's or one of its registered
   workplaces' (`underenheter`) are candidates; so are ads added since the cache whose display
   name equals one of the company's registered names. At most `MAX_ADS_PER_COMPANY = 15`.
4. **Verification and evidence** — each candidate's ad record is read during the run and
   snapshotted. It is published only if NAV still lists it `ACTIVE` **and** its employer
   number is in the company's set. The cache is never cited as evidence.

The employer homepage on a verified ad also becomes a website candidate
(`discovery.propose`, source `nav_employer_homepage`) and a supporting `official_listing`
signal in the identity gate. It is one evidence group, not proof: the site still needs a
second, independent group to be published. If the cache is missing, the adapter records
`skipped` and makes no request.

## Registry adapter

Six endpoints, called in a fixed order; see the table in
[`SOURCE_RIGHTS.md`](SOURCE_RIGHTS.md). The entity lookup runs first because everything else
depends on it — if it fails, the company terminates as `failed` with the reason
`"The official registry could not be read for this company"` rather than proceeding on
guesses.

**Timeouts and attempts are deliberately larger here than the fetcher default:**

```python
REGISTRY_ATTEMPTS = 4
REGISTRY_TIMEOUT = 20.0
```

The reason is recorded in `registry.py` as a measurement, not a hunch:

> Measured in a live 100-company run with the fetcher defaults (2 attempts, 12 s): 11
> companies lost their accounts or roles to a timeout or a connection reset from
> data.brreg.no, and a 12th lost its whole profile.

Losing a company's financials to a transient registry timeout is a coverage loss with no
research value, so the registry client asks with its own allowance. Tested:
`tests/signalpost/test_budget.py::TestRetryAttempts::test_the_registry_client_asks_with_its_own_allowance`.

Whether this removes registry timeouts is not established. `eval100b` ran before this
allowance existed and had 1 `failed` company and 17 `failed` registry-backed claims. The
superseded 1,000-company run, with the allowance, had none — but it also charged exactly 1,000
requests to each registry purpose, so no retry was needed, and the two earlier 100-company runs
without the allowance also had no `failed` claims. See [`LIMITATIONS.md`](LIMITATIONS.md).

## Website adapter

### Candidate order

`discovery.propose()` generates candidates with priors that control **ordering only**:

| Source of candidate | Prior |
| --- | --- |
| Website listed in the register | 1.0 |
| Domain of the registered email address | 0.80 |
| `.no` guesses from distinctive name tokens | 0.60 / 0.55 / 0.50 / 0.45 / 0.40 / 0.35 |
| One `.com` fallback | 0.30 |

At most 4 candidates (`DEFAULT_MAX_CANDIDATES`). Public mailbox domains (gmail, hotmail, ...)
never become candidates. Candidates are unique per registrable domain, so `example.no` and
`www.example.no` are not tried as two.

A prior is not evidence. Publication is decided entirely by
[`IDENTITY_RESOLUTION.md`](IDENTITY_RESOLUTION.md), and
`test_discovery.py::test_priors_are_ordering_only_and_never_publish` pins that.

**DNS pre-filtering.** Before spending a request, `discovery.discover()` resolves each
candidate. A name-guessed domain that does not resolve is dropped for free — a DNS lookup is
not an outbound HTTP request and costs nothing against the request budget. Two exceptions:

- a **registry-listed** site is kept even when DNS fails, because the register asserting it is
  itself meaningful and the failure should be reported rather than hidden;
- a bare domain that does not resolve is retried as `www.` before being dropped.

**Internationalised domains.** Norwegian names contain æ, ø and å. Candidates are converted to
punycode via IDNA before use (`to_ascii_host()`), and both transliterated (`smorbukk`) and
accented (`smørbukk`) forms are generated. Tests: `test_discovery.py::TestIdnaEncoding`,
`TestNameVariants`.

### Page order

Landing page first. Then up to four more from `website.CRAWL_PRIORITY`:

```
contact, about, careers, news, team, services
```

`DEFAULT_MAX_PAGES = 5` per company, and links are classified by URL and anchor text
(`extract/fields.py::classify_link`) rather than fetched speculatively.

Corroboration is separate and tighter: when the landing page leaves identity `ambiguous`, up to
**2** additional pages (`CORROBORATION_PAGES`) restricted to `contact` and `about`
(`CORROBORATION_KINDS`) are fetched, because those are the pages where a Norwegian company
states its organisation number. Only those two kinds are read
(`test_website.py::TestCorroboration::test_only_contact_and_about_pages_are_read`).

## Fallback order

### Transport fallback

1. Try `https://` on the canonical host.
2. If the host **refuses TLS** or presents an **invalid certificate**, make exactly **one**
   attempt on plain HTTP port 80.
3. Record the downgrade in the snapshot note rather than hiding it.

Measured hosts that needed this: `bergenproperty.no`, `oceancleaning.no`,
`sofiesgullogur.no`. Small Norwegian companies do sometimes still serve plain HTTP, and
refusing to read them at all would be a coverage loss for no safety gain — the content is
public either way, and the downgrade is visible.

Rules that keep this from becoming a loophole, all tested in
`test_website.py::TestPlainHttpFallback`:

- a successful HTTPS fetch **never** costs a second request
  (`test_a_successful_https_fetch_never_costs_a_second_request`);
- a candidate already on HTTP is not downgraded again
  (`test_a_candidate_already_on_http_is_not_downgraded_again`);
- an HTTP *answer* about the site — a 404, a 403 — is not retried on another port
  (`test_an_answer_about_the_site_is_not_retried_on_another_port`);
- if the fallback also fails, the **original** failure is reported, not the fallback's
  (`test_a_failing_fallback_reports_the_original_failure`);
- the downgrade is recorded, not hidden (`test_the_downgrade_is_recorded_rather_than_hidden`).

### Candidate fallback

If a candidate is rejected by the identity gate, the next candidate is assessed. If every
candidate is exhausted without a decisive or two-group verdict, the website claim is
`not_available` or `ambiguous` — never a best guess.

One distinction matters for honesty: if the **registry-vouched** site refuses access, the
claim is `blocked`, not `not_available`. The site exists and the register says it is theirs;
we were simply refused. `website._unverified_claims()` emits all five external families with
their specific reasons in that situation, so a reviewer can tell "no site" from "site we could
not read".

## Retries

| Setting | Value | Where |
| --- | --- | --- |
| Default attempts | 2 | `fetch.DEFAULT_MAX_ATTEMPTS` |
| Registry attempts | 4 | `registry.REGISTRY_ATTEMPTS` |
| Retryable statuses | 408, 425, 429, 500, 502, 503, 504 | `fetch.RETRYABLE_STATUS` |
| Terminal error kinds (never retried) | `refused`, `tls`, `unsupported_media` | `fetch.TERMINAL_ERROR_KINDS` |
| Max redirects | 4 | `fetch.DEFAULT_MAX_REDIRECTS` |
| Timeout | 12 s default, 20 s registry | `fetch.DEFAULT_TIMEOUT` |
| Max response bytes | 4 MB | `fetch.DEFAULT_MAX_BYTES` |
| Per-host delay | 0.6 s | `fetch.DEFAULT_HOST_DELAY` |

A closed port or an invalid certificate is a settled answer, so retrying it only burns budget.
A transient network error stays retryable. Tests:
`test_website.py::TestTerminalFailures::test_a_closed_port_and_an_invalid_certificate_are_not_retried`
and `test_a_transient_network_error_stays_retryable`.

**Every attempt costs a request, and so does every redirect hop.** This is required by the
declared envelope — 2,000 outbound requests including redirects and retries — and is enforced
rather than approximated:
`test_budget.py::TestRequestAccounting::test_redirects_and_retries_are_charged_by_count`.

Redirects are followed manually, one hop at a time, with the URL policy re-applied at each hop.
That is a safety measure and is documented in [`docs/SECURITY.md`](docs/SECURITY.md).

## Caching

**In-memory, per run.** The cache key is `f"{purpose}\x1f{url}"` — purpose and URL, not URL
alone, so a page fetched as a landing page and later as a corroboration page is not silently
conflated.

A cache hit returns `from_cache=True` and **costs no budget**, because no request leaves the
process.

**Snapshot store: content-addressed, on disk.** Bodies are written to
`root/<digest[:2]>/<digest>.bin` keyed by SHA-256 of the content. Two pages with identical
bytes are stored once, and the digest in the snapshot record is the same value a reviewer gets
by hashing the file. Enable with `--snapshots DIR`; without it only the snapshot metadata and
hash are kept (`eval100b` was run without it).

**Snapshot records in an envelope are deduplicated by snapshot id, not by URL.** The website
researcher can fetch the same URL twice — once in the crawl, once as a corroboration page — and
a dynamic page can return different bytes each time. Those are two snapshots, and the website
claim may cite either. Until commit `1e06568` the researcher kept one per URL and could drop the
one that evidence cited; `WebsiteResearcher._cited_snapshots()` now keeps every distinct
snapshot id from both lists (`test_website.py::TestCitedSnapshots`). Because the in-memory cache key
includes the purpose, a URL crawled under one purpose and fetched again as corroboration is a
cache miss and a second request; that is the likely route to the two fetches, inferred from the
cache key rather than traced in the two affected envelopes.

The offline audit `scripts/signalpost/check_integrity.py --snapshot-root` re-hashes stored
bodies against their records. On the superseded 1,000-company run, 4,556 distinct bodies
(756 MB on disk) all matched (`out/superseded/integrity-report-23a7c21.json`).

**`robots.txt` is fetched once per host and reused** (`fetch.RobotsPolicy`). At 169 robots
fetches for 128 landing-page fetches in `eval100b`, most companies are on their own host; the
cache pays off on repeated hosts.

**Resume, not re-crawl.** `store.completed_numbers()` reads back which companies an earlier
attempt finished, and a re-run skips them. `--no-resume` forces a clean run. A truncated final
line from a crash does not lose the intact lines before it
(`test_store.py::TestCrashTolerance::test_a_truncated_final_line_does_not_lose_the_intact_ones`).

## Budgets

The locked envelope was 100 inputs, 45 minutes, 2,000 outbound requests and $10 of
third-party spend. Builderr now supplies a shared batch of 1,000 companies (possibly 1,100)
with a budget it sets. `src/signalpost/budget.py` enforces whatever caps the run is given;
`runner.resolve_limits` decides them: a command-line flag, else the matching
`SIGNALPOST_*` environment variable, else a default that scales with the batch.

| Default | Value |
| --- | --- |
| Requests | 20 per input (the locked 2,000-per-100 ratio), never below 2,000 |
| Wall clock | 2,700 s (45 min) |
| Spend | $10.00 (actual: $0.00) |
| Concurrency | 8, or 24 when the batch has more than 200 inputs |
| `DEFAULT_SOFT_RATIO` | 0.90 |

A fixed 2,000-request default would have researched the first ~250 companies of a
1,000-company batch and terminated the rest as `failed`.

**Reservation before dispatch.** `reserve_request()` is a context manager that takes capacity
under a single lock *before* the request is made, commits on a clean exit, and releases on an
exception. A request that fails returns its reservation rather than leaking it. This is what
makes the cap hold under 8 concurrent workers instead of being a best-effort counter.

Tests: `test_budget.py::TestConcurrency::test_the_hard_limit_holds_under_concurrent_workers`
and `test_no_reservation_is_leaked_under_concurrent_failures`.

**Refusal, not overrun.** `charge()` and the request reservation refuse when the cap would be
exceeded. The limit is a limit:
`test_budget.py::TestRequestAccounting::test_the_limit_is_refused_rather_than_overrun`,
`TestSpend::test_spend_is_refused_rather_than_overrunning_the_cap` (and spending exactly the
cap is allowed).

**Soft stop at 90%.** Discretionary work — extra crawl pages — stops at
`DEFAULT_SOFT_RATIO` of the request budget and of the wall clock, leaving headroom for the
mandatory registry calls of the remaining companies. Tests: `TestSoftStop`.

**Fair share across the batch.** `runner._page_allowance()` recomputes each company's crawl
allowance from what remains:

```python
crawl_budget = per_company - 5   # ~4 registry requests plus robots, before any crawling
```

with reserves of `TIME_RESERVE_RATIO = 0.06` (minimum 20 s) and
`REQUEST_RESERVE_RATIO = 0.04`. A slow, link-rich company at the head of the batch cannot
starve the tail, and an exhausted budget still allows a floor of one page. Tests:
`TestFairShare::test_the_tail_of_the_batch_is_not_starved_by_the_head`,
`test_an_exhausted_budget_still_allows_a_floor_of_one`.

**Per-company accounting** uses thread-local usage (`budget.thread_usage()`), so the
`operations.requests` figure in each envelope is that company's own consumption rather than a
share of the total.

## Measured behaviour

From `out/eval100b-report.json` — command, dataset and date in [`EVAL.md`](EVAL.md). This run
predates commits `23a7c21` and `1e06568`
([which code produced which run](EVAL.md#which-code-produced-which-run)):

| Metric | Value |
| --- | --- |
| Requests used | 797 of 2,000 (7.97 per company) |
| Wall clock | 389.0 s of 2,700 s |
| Spend | $0.00 of $10.00 |
| Budget denials | none (`denials: {}`) |
| Seconds per company | median 18.02, p90 65.59, max 111.88 |
| `budget_exhausted_after` | null |

Requests by purpose:

| Purpose | Count |
| --- | --- |
| `robots` | 169 |
| `website_landing` | 128 |
| `registry_roles` | 110 |
| `registry_accounts` | 110 |
| `registry_entity` | 106 |
| `registry_subunits` | 105 |
| `website_contact` | 21 |
| `website_news` | 17 |
| `website_about` | 16 |
| `website_services` | 6 |
| `website_corroboration` | 4 |
| `website_team` | 3 |
| `website_careers` | 2 |

The shape is worth reading honestly: about 70% of the request budget goes to the registry and
to `robots.txt`, and the actual crawl is small. That is a direct consequence of publishing a
website for only 22 of 100 companies — most companies never reach the crawl stage, because no
candidate passed the identity gate. See [`LIMITATIONS.md`](LIMITATIONS.md).

The superseded 1,000-company run (code `23a7c21`; generation-run ceilings of 40,000 requests and
14,400 s, not the evaluator's; source `out/superseded/submission-profiles-23a7c21-report.json`;
regenerated values pending) had the same shape: 7,413 requests (7.41 per company), of which
4,000 were registry calls and 1,487 `robots.txt` fetches; 1,254 `website_landing`, 171
`website_contact`, 134 `website_about`, 124 `website_corroboration`, 92 `website_news`, 84
`website_services`, 40 `website_careers`, 27 `website_team`; `data.brreg.no` received 4,016
requests; 2,545.3 s wall clock; median 14.73 s, p90 43.06 s, max 111.45 s per company; no budget
denials; $0.00 under a $0.00 cap.

## Politeness

- `robots.txt` is fetched and obeyed before any page on a host.
- One identifying user-agent, with a contact URL, on every request (string in
  [`docs/SECURITY.md`](docs/SECURITY.md)).
- 0.6 s minimum between requests to the same company host (`fetch._HostThrottle`). The
  Brønnøysund open-data API, a machine API every company calls four times, is paced at
  0.1 s (`fetch.API_HOST_DELAYS`): at 0.6 s that single throttle took 2,410 s of a
  2,539 s 1,000-company run.
- At most 5 pages from any one company site, plus up to 2 corroboration pages.
- 4 MB response cap and a 12 s timeout, so a misbehaving host cannot hold a worker.
- Retries only on statuses that mean "try later".

The heaviest single company host in `eval100b` received 14 requests across the whole run.
