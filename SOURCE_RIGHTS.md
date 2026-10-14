# Source rights

Every source Signalpost reads in the submitted run path, how it is read, under what licence,
what is retained, and what may not be redistributed.

Two sources are contacted live during a run: the Brønnøysund open-data APIs and company-owned
websites. Nothing else is contacted. The connector scripts under `scripts/` reach other
sources but are not part of the submitted run path — see [Disabled connectors](#disabled-connectors).

## Brønnøysundregistrene (the registry)

| Endpoint | Purpose in a run | Code |
| --- | --- | --- |
| `https://data.brreg.no/enhetsregisteret/api/enheter/{org}` | Legal identity, form, status, industry, addresses, registry website, employee count | `registry.ENTITY_URL` |
| `https://data.brreg.no/enhetsregisteret/api/underenheter/{org}` | Fallback lookup when the number is a subunit | `registry.SUBENTITY_URL` |
| `https://data.brreg.no/enhetsregisteret/api/enheter/{org}/roller` | Company-centric public role holders | `registry.ROLES_URL` |
| `https://data.brreg.no/enhetsregisteret/api/underenheter?overordnetEnhet={org}` | Registered establishments | `registry.SUBUNITS_URL` |
| `https://data.brreg.no/enhetsregisteret/api/konsernstruktur/{org}` | Official group relationships | `registry.GROUP_URL` |
| `https://data.brreg.no/regnskapsregisteret/regnskap/{org}` | Normalised annual accounts | `registry.ACCOUNTS_URL` |

**Method.** Plain HTTPS GET against the documented JSON APIs. No bulk scraping, no
undocumented endpoints, no authentication. Requests carry the identifying user-agent
described in [`docs/SECURITY.md`](docs/SECURITY.md) and pass through the same egress control
as every other fetch.

**Licence.** The open data is published under
[NLOD 2.0](https://data.norge.no/nlod/en/2.0), which permits copying, distribution and
commercial use with attribution and without implying endorsement. Signalpost attributes every
registry-derived claim with its exact endpoint URL, retrieval time, content hash and an
RFC 6901 JSON pointer into the stored response.

**Retention.** When a run is given `--snapshots DIR`, registry responses are stored whole as
content-addressed snapshots (SHA-256, `fetch.SnapshotStore`) so a reviewer can re-resolve any
pointer. Snapshots live in that directory on local disk; the agent itself publishes them
nowhere. The submission packager does include official-register bodies; see
[What may be redistributed](#what-may-be-redistributed).

**Restrictions applied beyond the licence:**

- **Birth dates are not published as claims.** The roles endpoint returns `fodselsdato` for
  each role holder. `registry.py` never reads it into a claim. Role holders are recorded only in
  the company-centric context — name, role code, role title — which is the context in which the
  register publishes a company's officers. **The raw response still contains it:** a stored
  roles snapshot is the whole response body, and all 999 distinct `official_roles` bodies in
  the superseded run's store (`out/superseded/snapshots-submission-23a7c21/`, checked
  2026-09-14) contain the `fodselsdato` key. See the packaging note below.
- **A 410 is not treated as a value.** The registry's documentation says a `410 Gone` means any
  cached copy should be removed rather than retained as a last-known-good value.
  `RegistryClient.fetch_entity()` treats 410 like 404 (it falls back to the sub-entity
  register and never publishes the old record). **It does not delete anything:** no code
  removes an earlier snapshot body from the store, or an earlier envelope passed as
  `--previous-snapshot`, when a 410 is seen.
- **A 404 is not a zero.** An entity with no returned accounts record is `not_available`, not
  revenue 0. This is enforced structurally by `models.Claim.__post_init__` and tested in
  `tests/signalpost/test_availability.py::TestValueAndStateAgreement`.

The bulk entity snapshot (`.../enheter/lastned/csv`) was downloaded once, outside a run, to
build the sampling universe. Its SHA-256 and row count are recorded in
`data/corpus/selection.json`. It is not fetched during evaluation.

## Company-owned websites

**Method.** Signalpost fetches a small number of pages from a company's own site: the landing
page, then up to four more chosen from `contact`, `about`, `careers`, `news`, `team`,
`services` (`website.CRAWL_PRIORITY`, `DEFAULT_MAX_PAGES = 5`). It obeys `robots.txt`
(`fetch.RobotsPolicy`), identifies itself, and keeps a 0.6 s delay between requests to the
same host. Full mechanics are in [`CRAWLERS.md`](CRAWLERS.md).

**Rights basis.** Ordinary permitted public reading of pages a company publishes about itself,
at low volume, under the site's own robots policy. It is not a licence to redistribute the
page.

**Source class.** Website-derived claims are classed `company_owned` in the envelope
(`models.SourceClass`). That class carries a meaning: the company is asserting this about
itself. It is not independent verification, and this documentation does not present it as
such.

**Retention.** Response bodies are stored as local content-addressed snapshots so evidence can
be re-resolved. Bodies are capped at 4 MB per response (`fetch.DEFAULT_MAX_BYTES`).

**Redistribution.** Signalpost publishes *claims with locators*, not page copies. An envelope
carries the extracted value, the URL, the retrieval time, the content hash and a locator (CSS
selector, text span, JSON pointer). A short excerpt may be carried as evidence context. Whole
retrieved pages are not redistributed in the output. If a snapshot directory is shared with a
reviewer it contains third-party page bodies, and should be treated as a local working
artifact rather than as a publishable corpus.

**URLs are cleaned before publication.** Session identifiers, tracking parameters and cache
busters are stripped by `net.canonical_url` before a URL becomes a published value. The case
that motivated this — a WordPress `doing_wp_cron` parameter that made a stable website look
like it changed on every run — is recorded in `net.py` and tested in
`tests/signalpost/test_net.py::TestCanonicalisation`.

## Competition material

`docs/provenance/competition-material.json` records ten organiser artifacts with URL, byte
count, SHA-256 and retrieval time (all retrieved 2026-09-13). They were retrieved so the
requirements this project is built against can be checked against their sources rather than
against a paraphrase. They remain the organiser's material. The manifest records what was read
and when; it is provenance, not redistribution.

## Disabled connectors

The following exist under `scripts/` and are **not** invoked by `main.py`, by
`src/signalpost/runner.py`, or by anything else in the submitted run path. They were built or
sketched during exploration and are quarantined until their data-rights gates pass.

| Script | Source | Status and reason |
| --- | --- | --- |
| `run_brave_discovery.py` | Brave Search API | Disabled. Requires a contracted plan; result-storage rights depend on plan tier. Would need `BRAVE_SEARCH_API_KEY`. |
| `apply_search_discovery.py` | Exported search JSONL | Disabled. Accepts exported results only under a plan granting result-storage rights. |
| `run_linkedin_guest_jobs_connector.py`, `run_linkedin_guest_experiment.py`, `discover_linkedin_company_profiles.py` | LinkedIn guest endpoints | Disabled. Guest-endpoint reading is not a settled rights basis for this use. |
| `run_google_news_rss_connector.py` | Google News RSS | Disabled. Rights for building a stored corpus from aggregator output are not established. |
| `run_youtube_search_connector.py` | YouTube search | Disabled. Same reason. |
| `run_fagfolkguiden_reviews_connector.py` | Fagfolkguiden reviews | Disabled. Third-party review content; redistribution rights not established. |
| `normalize_google_maps_results.py` | Google Maps exports | Disabled. Normalisation helper only; no fetching, and no rights basis for storage. |
| `run_scrapy_websites.py` | Company websites via Scrapy/Playwright | Disabled. Optional `crawler` extra; the submitted path uses the in-repo fetcher. |
| `run_sentiment_model.py` | Hugging Face models | Disabled. Optional `sentiment` extra. See below. |

Because these are disabled, no claim in a submitted envelope carries a `licensed_api` source
class. A reviewer can confirm this from `out/eval100b-report.json`, whose `top_hosts` block
records only `data.brreg.no` and company hostnames.

**Google Custom Search JSON API** was evaluated and rejected as a path: it is
[closed to new customers and scheduled to discontinue on 1 January 2027](https://developers.google.com/custom-search/v1/overview).

**Sentiment models**, if ever enabled, would use
[`NOSIBLE/financial-sentiment-v1.2-base`](https://huggingface.co/NOSIBLE/financial-sentiment-v1.2-base)
(Apache-2.0) and
[`ltg/norbert3-base_sentence-sentiment`](https://huggingface.co/ltg/norbert3-base_sentence-sentiment)
(CC BY 4.0). [NoReC](https://github.com/ltgoslo/norec) is CC BY-NC 4.0 and is therefore a
research reference only, not a corpus for a commercial POC. None of this is in the submitted
path; see [`MODELS_AND_APIS.md`](MODELS_AND_APIS.md).

## Personal data

- Role holders' **birth dates are never read into a claim or published in an envelope**. They
  are present in stored roles response bodies, and in the submission package if those bodies
  are included; see [What may be redistributed](#what-may-be-redistributed).
- Named individuals appear only in the company-centric role context the register publishes,
  and in `site_person` claims taken from a company's own public team page.
- Published contact details are company contact points. `extract/fields.py` filters
  role-mailbox local parts (`noreply`, `postmaster`, `webmaster`, ...) and shared-infrastructure
  hosts (`wixpress.com`, `squarespace.com`, `sentry.io`, ...) out of contact candidates.
- The shareholder register and the beneficial-owner register are **not** used. The former
  requires a request to the Tax Administration with its own handling obligations; the latter is
  not a general public enrichment source for this work.

## What may be redistributed

| Artifact | Redistributable | Note |
| --- | --- | --- |
| Envelopes (`out/*.jsonl`) | Yes, with NLOD attribution for registry-derived claims | Claims and locators, not page copies |
| Run reports (`out/*-report.json`) | Yes | Counts and budget figures only |
| Corpus manifest (`data/corpus/selection.json`) | Yes | Derived from NLOD open data |
| Official-register snapshot bodies | Yes, under NLOD 2.0 | Included in the submission package (below). They contain role holders' birth dates. |
| Company-owned page bodies | No | Third-party pages read under permitted public access; not a licence to redistribute |
| Snapshot store (`out/snapshots-*/`) | No, as a whole | Mixes both of the above; a local working artifact |

**What the submission package contains.** `scripts/signalpost/package_submission.py` (commit
`1a583f5`) builds the ZIP. Read from the code:

- `data/envelopes.jsonl` — the envelopes, with each snapshot's `stored_path` rewritten to a
  package-relative path if its body is in the package, and set to null otherwise. URL and
  content SHA-256 stay on every snapshot. Each snapshot also gains a `body_in_package` flag.
- `data/profiles.jsonl` — a compact view derived from those claims, with no added values.
- `data/source-manifest.jsonl` — one row per distinct snapshot body: content SHA-256, source
  class, final URL, status, content type, retrieval time, the organisation numbers citing it,
  and where its body is in the package, or null.
- `evidence/snapshots/` — bodies only for snapshots whose source class is in the packaging
  configuration's `redistribute_source_classes`. Company-owned page bodies are not included.
- `refresh/` — previous and current snapshots of the refresh sample, all with `stored_path`
  null.

The class list itself is supplied by a configuration file passed with `--config`, which is not
in the repository. The coordinator reports it as `official_registry`, `official_roles`,
`official_accounts`, `official_subunits`; that is **pending verification** against a built
package (`dist/` was empty when this was written).

**Birth dates in the package.** If `official_roles` is in that list, the package redistributes
the roles response bodies, and with them role holders' birth dates (see Restrictions above).
NLOD 2.0 may permit that, but it is not the minimisation this document otherwise describes.
Whether to exclude `official_roles` bodies, or to redact `fodselsdato` before packaging, is an
open decision. Nothing in the packager currently redacts it.
| Competition material | No | Organiser's material; the manifest records provenance only |
