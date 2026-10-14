# Data schema

The output is one JSON object per line (JSONL), exactly one line per input company. The schema
identifier is `signalpost.envelope/1`.

Definitions live in `src/signalpost/models.py`. Structural rules are enforced at construction
time, not by a later validation pass, so an envelope that violates them cannot be built. Tests:
`tests/signalpost/test_availability.py`.

## Envelope

| Key | Type | Meaning |
| --- | --- | --- |
| `schema_version` | string | `"signalpost.envelope/1"` |
| `organisation_number` | string | 9-digit Norwegian organisation number; the stable key |
| `run` | object | `run_id`, `started_at`, `completed_at`, `terminal_status`, `pipeline_version` |
| `identity` | object | Resolved names, addresses, subunits and the website verdict with its reason |
| `claims` | array | Every field considered, published or not |
| `evidence` | array | Claim-to-source links |
| `snapshots` | array | Retrieved source documents |
| `synthesis` | object | Generated summary, with claim keys per sentence |
| `changes` | array | Change events versus a previous snapshot |
| `refresh` | object | Change summary and idempotency flag |
| `errors` | array | What failed and why |
| `operations` | object | Per-company operational metrics |

### Compatibility with the required contract

`OUTPUT_CONTRACT.md` requires `organisation_number`, `run`, `claims`, `evidence`, `changes`,
`errors` and `operations`. All seven are present under those exact names. `identity`,
`snapshots`, `synthesis`, `refresh` and `schema_version` are additions, not replacements — a
consumer reading only the contracted keys sees exactly what it expects.

The three operational metric names the contract asks for are emitted by the current code under
their required names inside `operations`: `requests`, `runtime_ms`, `third_party_cost_usd`
(`pipeline.py`). The additional keys alongside them (`stage_seconds`, `terminal_reason`,
`claims_total`, ...) are extra diagnostics.

**Older outputs do not carry the three contracted keys.** They were introduced with commit
`23a7c21`. `out/eval100.jsonl`, `out/eval100-refresh.jsonl` and `out/eval100b.jsonl` were
produced before it; none of their 300 envelopes has `requests`, `runtime_ms` or
`third_party_cost_usd`. All 1,000 envelopes of the superseded 1,000-company run
(`out/superseded/submission-profiles-23a7c21.jsonl`) do carry them.

A run is verified on every execution by `store.verify()`, which checks one envelope per input,
no duplicates, a valid terminal state on each, and that each envelope has its top-level required
keys. Its result is written into the run report under `verification`, and `main()` exits
non-zero if `verification.ok` is false. **It does not check keys inside `operations` or any
other section, and it does not read `operations.validation_problems`.** So `verification.ok:
true` on the 100-company runs does not attest the contracted operation keys, and on any run it
does not attest that the evidence chain is intact.

## Availability states

Exactly six, no others. Enforced by `models.AVAILABILITY_STATES`; an unknown value raises at
claim construction.

| State | Meaning |
| --- | --- |
| `available` | We have the value and can show where it came from |
| `not_available` | Looked, did not find it |
| `blocked` | A source exists but refused access |
| `not_applicable` | The field does not apply to this entity |
| `ambiguous` | Evidence exists but does not identify the entity well enough to publish |
| `failed` | The attempt itself broke |

Test: `test_availability.py::TestStateVocabularies::test_exactly_the_six_organiser_availability_states_exist`.

### The value/state invariant

Two rules, enforced in `Claim.__post_init__`:

```python
if self.availability == "available" and self.value is None:
    raise ValueError(...)
if self.availability != "available" and self.value is not None:
    raise ValueError(...)
```

This is the structural version of "never report a missing measurement as zero". A claim cannot
be `not_available` and still carry a number, so a missing revenue cannot leak downstream as
0 NOK. Equally, `available` requires an actual value.

**Zero is a value.** A genuine 0 stays `available` — the rule is about absence, not about the
number zero. Tests: `TestValueAndStateAgreement::test_zero_is_a_value_and_stays_available`,
`TestValueAndStateAgreement::test_a_non_available_claim_must_not_smuggle_a_value`, and at the
parser level `test_extract.py::TestNorwegianNumbers::test_non_numbers_return_none_not_zero`.

Concretely: when the register reports `harRegistrertAntallAnsatte` as false, the employee
count becomes `not_available` with a reason, never 0.

## Terminal states

Exactly five (`models.TERMINAL_STATES`). Every company gets exactly one, always.

| State | Meaning |
| --- | --- |
| `completed` | Registry resolved and the external work succeeded |
| `partial` | Something was retrieved, something was not |
| `not_found` | The organisation number resolved to nothing |
| `blocked` | Access was refused |
| `failed` | The attempt broke |

`pipeline._stage()` wraps every stage so an exception still produces a terminal envelope rather
than a missing line, and `_terminal_state()` attaches a human-readable `terminal_reason`. In
the `eval100b` run the distribution was 22 `completed`, 77 `partial`, 1 `failed` — and the one
failure is still a well-formed envelope carrying
`"The official registry could not be read for this company"`. In the superseded 1,000-company
run it was 186 `completed`, 814 `partial`, 0 `failed` (regenerated run: pending).

A validation defect does not change the terminal state: an envelope whose evidence fails
`validate_result()` can still be `completed` (see [Validation](#validation)).

Test: `test_availability.py::TestStateVocabularies::test_exactly_the_five_terminal_states_exist`.

## Field families

Nine families. Five are flagged `external`, meaning the data comes from outside the register.

| Family | External | Label |
| --- | --- | --- |
| `legal_identity` | No | Legal identity and public brand |
| `financials` | No | Annual accounts and history |
| `people` | No | Leadership and roles |
| `locations` | No | Registered workplaces and locations |
| `web_presence` | **Yes** | Verified website and owned profiles |
| `business_description` | **Yes** | Business description |
| `contact` | **Yes** | Contact details |
| `jobs` | **Yes** | Hiring and job postings |
| `activity` | **Yes** | Dated public activity |

Test: `test_availability.py::TestStateVocabularies::test_the_five_externally_scored_families_are_marked`.

## Claim

| Field | Type | Notes |
| --- | --- | --- |
| `claim_key` | string | Stable semantic key. See below. |
| `organisation_number` | string | Scopes the claim. A claim about another company is a validation defect. |
| `field` | string | e.g. `revenue`, `website`, `legal_name` |
| `family` | string | One of the nine |
| `value` | any / null | Subject to the invariant above |
| `availability` | string | One of the six |
| `evidence_ids` | array | Required for every `available` claim |
| `confidence` | float / null | |
| `reporting_period` | string / null | `from/to`, e.g. `2025-01-01/2025-12-31` |
| `effective_date` | string / null | |
| `scope` | `company` / `group` / `subunit` | |
| `unit` | string / null | See financial units |
| `currency` | string / null | |
| `reason` | string / null | Why a non-available claim is not available |
| `derived_from` | array | Claim keys this was computed from |
| `identity_ref`, `extraction_method` | string / null | |
| `first_observed_at`, `last_observed_at` | string / null | First observation survives a re-run |

### Claim keys

A claim key is a stable *semantic* identity: `{org}:{field}[:{discriminators}]`, produced by
`models.claim_key()`, which casefolds and collapses whitespace in discriminators.

It deliberately contains **no** retrieval timestamp, HTML whitespace, ordering or tracking
parameter. That is what makes refresh idempotent: the same fact from the same source yields the
same key on every run, so nothing appears "changed" merely because it was fetched again.

Examples from the `eval100b` output:

```
979498047:revenue:2025:company
979498047:legal_name
979498047:website
```

Tests: `test_availability.py::TestClaimKeys` — stability across runs, insensitivity to
whitespace and case in discriminators, distinct keys for repeated fields, and company scoping.

## Evidence and snapshots

Three linked levels: a claim names evidence ids, evidence names a snapshot id, a snapshot
records the retrieval. Every published material claim must resolve down the whole chain, or
`validate_result()` reports a defect.

**Snapshot** — one retrieved document: `id`, `requested_url`, `final_url`, `status`,
`content_type`, `bytes_received`, `content_sha256`, `retrieved_at`, `source_class`,
`redirect_chain`, `from_cache`, `stored_path`, `note`. Bodies are stored content-addressed
(SHA-256), at `<snapshot dir>/<first two hex digits>/<sha256>.bin`, **only when the run is given
`--snapshots DIR`**; `stored_path` is then relative to that directory's parent. Without the flag
`stored_path` is null and only the metadata and hash are kept: all 543 snapshot entries in
`out/eval100b.jsonl` have a null `stored_path`, so that run's bodies cannot be re-hashed.

**Snapshots in an envelope are deduplicated by snapshot id, not by URL.** The same URL fetched
twice can yield two different snapshots (a page that renders differently per request), and
evidence may cite either. Until commit `1e06568` the website researcher kept one snapshot per
URL, which dropped a snapshot that evidence cited; see
[`LIMITATIONS.md`](LIMITATIONS.md#10-the-1000-company-run-was-superseded-by-a-defect-fix).

**Evidence** — the claim-to-source link: `id`, `snapshot_id`, `source_url`, `source_class`,
`retrieved_at`, `content_sha256`, `locator`, `claim_span`, `extraction_method`.

**Locator** — *where inside* the document. A bare URL is not sufficient provenance for a
specific number. Kinds: `json_pointer`, `css_selector`, `text_span`, `table_cell`, `pdf_page`,
`row_key`.

A real chain from `out/eval100b.jsonl`, company 979498047:

```json
claim    { "claim_key": "979498047:revenue:2025:company", "value": 63150968.0,
           "unit": "absolute", "currency": "NOK",
           "reporting_period": "2025-01-01/2025-12-31",
           "evidence_ids": ["ev-4b20e3498f74ec0e"] }

evidence { "id": "ev-4b20e3498f74ec0e", "snapshot_id": "snap-ceabf707403471ca",
           "source_url": "https://data.brreg.no/regnskapsregisteret/regnskap/979498047",
           "source_class": "official_accounts",
           "content_sha256": "882d3d38d0f4240326dec782abb29410b015dafd4351de1a144d1696a1621e02",
           "locator": { "kind": "json_pointer",
             "value": "/0/resultatregnskapResultat/driftsresultat/driftsinntekter/sumDriftsinntekter" } }

snapshot { "id": "snap-ceabf707403471ca", "status": 200,
           "content_type": "application/json", "bytes_received": 1352,
           "content_sha256": "882d3d38d0f4240326dec782abb29410b015dafd4351de1a144d1696a1621e02",
           "retrieved_at": "2026-09-13T15:13:56Z" }
```

A reviewer can re-fetch that URL, apply that RFC 6901 pointer, and get 63150968.0 back.

Derived claims are the one exception to citing a source: they may cite other claims through
`derived_from` instead. Test:
`TestEvidenceResolution::test_a_derived_claim_may_cite_other_claims_instead_of_a_source`.

Non-available claims need no evidence — there is nothing to point at
(`test_a_non_available_claim_needs_no_evidence`).

### Source classes

`models.SourceClass`: `official_registry`, `official_accounts`, `official_roles`,
`official_subunits`, `official_group`, `company_owned`, `licensed_api`, `permitted_public`,
`derived`.

The distinction is meaningful. `official_accounts` is a filed statement to the register;
`company_owned` is the company describing itself on its own site. A reader can tell which kind
of statement they are looking at without inspecting the URL. In the submitted run path,
`licensed_api` never appears — see [`SOURCE_RIGHTS.md`](SOURCE_RIGHTS.md).

## Financial units

Financial claims are explicit about scale, currency and period, because the silent failure mode
here is a factor of 1,000.

| Field | Value in practice | Why |
| --- | --- | --- |
| `unit` | `"absolute"` for registry accounts | The normalised accounts API reports absolute NOK |
| `currency` | `"NOK"` by default, taken from the record when stated | |
| `reporting_period` | `"{from}/{to}"`, e.g. `"2025-01-01/2025-12-31"` | Two figures from different periods are different claims |
| `scope` | `"group"` when `regnskapstype == "KONSERN"`, else `"company"` | A consolidated figure is not a company figure |

Lines extracted (`registry.FINANCIAL_LINES`): `revenue`, `operating_costs`,
`operating_profit`, `profit_before_tax`, `net_profit`, `total_assets`, `total_equity`,
`total_equity_and_liabilities`. Up to 3 years. `accounts_audited` is carried separately,
derived from the register's `revisjon.ikkeRevidertAarsregnskap` flag (negated).

For figures read from a **page** rather than the API, `extract/fields.py::parse_norwegian_number`
handles the Norwegian conventions — `.` as thousands separator, `,` as decimal — and detects
stated scale from phrases like `"Tall i hele 1 000"` or `"i mill"`. Critically, it **reports
the detected scale rather than silently applying it**, so a mis-detected scale is visible
instead of multiplying a number by 1,000 invisibly. Tests:
`test_extract.py::TestNorwegianNumbers::test_stated_scale_is_reported_rather_than_applied_silently`
and `test_no_scale_hint_reports_none`.

No financial figure is ever estimated, interpolated or carried forward from a previous year. If
the register returns nothing, the claim is `not_available` with a reason.

## Identity block

Carries the resolved `names` (including former names), `cities`, `postal_codes`,
`registry_website`, `subunit_numbers`, `resolved`, and the `website_verdict` — the candidate,
the decision, the confidence and the **reason in words**, e.g.
`"3 independent evidence groups (contact, location, name; score 1.10)"`. A reviewer can see why
a site was or was not published without re-running anything. See
[`IDENTITY_RESOLUTION.md`](IDENTITY_RESOLUTION.md).

## Synthesis block

`organisation_number`, `headline`, `summary`, `sentences`, and a `method` of
`"deterministic-template"`. Every sentence carries the `claim_keys` it was rendered from and a
`topic`, so each sentence traces to claims, then to evidence, then to a snapshot. Nothing in
the summary is generated by a model — see [`MODELS_AND_APIS.md`](MODELS_AND_APIS.md).

## Refresh block

`total_changes`, `material_changes`, `by_type`, `by_family`, `idempotent`, `material_fields`,
`compared_against`, `note`. On a first observation `compared_against` is null and the note says
so. See [`REFRESH.md`](REFRESH.md).

## Operations block

`requests`, `runtime_ms` and `third_party_cost_usd` under the contract's names, plus
`dns_lookups`, `candidates_considered`, `candidates_fetched`, `pipeline_version`,
`terminal_reason`, `stage_seconds`, `wall_seconds`, `requests_used`, `validation_problems`,
`claims_total`, `claims_available`, `evidence_total`, `snapshots_total`. `dns_lookups` and the
two `candidates_*` keys appear only when the website stage ran (999 of 1,000 envelopes in the
superseded 1,000-company run, 99 of 100 in `eval100b`). The 100-company runs lack the three
contracted keys (see [compatibility](#compatibility-with-the-required-contract)).

## Validation

`models.validate_result()` checks, per company: the terminal state is one of the five; no
duplicate claim keys; no claim attributed to another company; every evidence id referenced by a
claim exists; every material claim carries evidence; every evidence record resolves to a
snapshot. Defects are counted into `operations.validation_problems` and described in an
`errors` entry of type `validation_failed` (marked `recoverable`). **Neither changes the
terminal state, and neither is read by `store.verify()`**, so a run report does not surface
them. Measured: `eval100b` has 1 envelope with `validation_problems` > 0, the superseded
1,000-company run has 2, all `completed`, all the cited-snapshot defect.

The offline audit `scripts/signalpost/check_integrity.py` checks the same chain across a whole
output file, plus stored-body hashes, and exits non-zero on any problem; see
[`EVAL.md`](EVAL.md#offline-run-audits).

`store.verify()` then checks the file as a whole: envelope count, unique companies, terminal
state distribution, duplicates, invalid terminal states, missing required keys, and
`exactly_one_per_input`. Tests: `test_store.py::TestVerification` and
`test_store.py::TestUniqueness`, which includes
`test_concurrent_writers_cannot_duplicate_a_company`.
