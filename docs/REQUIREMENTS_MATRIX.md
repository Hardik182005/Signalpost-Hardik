# Requirements matrix

Every binding requirement, the code that implements it, the evidence that it works, and the
submission file that documents it.

Three kinds of row appear here and are labelled in the **Kind** column, because conflating them
would overstate what is required of us:

| Kind | Meaning |
| --- | --- |
| **Mandatory** | A rule stated by the organiser. Non-compliance is disqualifying. |
| **Choice** | Our implementation decision. A different entry could reasonably do otherwise. |
| **Optional** | An enhancement beyond what is required. |

Evidence is one of: a test file (offline, deterministic), a run report (live measurement), or a
manifest. Where a requirement is **not** satisfied or **not** verifiable, the row says so
rather than being omitted.

**Official qualification status: `not_determined`.** No row in this table asserts a score, a
qualification, or an independently verified result.

## Output contract

| # | Requirement | Kind | Implementation | Evidence | Documented in |
| --- | --- | --- | --- | --- | --- |
| 1 | Exactly one terminal envelope per input company | Mandatory | `pipeline._stage()` wraps every stage; `store.RunStore` enforces uniqueness on write | `tests/signalpost/test_store.py::TestUniqueness`, `TestVerification`; `out/eval100b-report.json` → `verification.exactly_one_per_input: true`, 100/100 | [`../DATA_SCHEMA.md`](../DATA_SCHEMA.md) |
| 2 | Exactly the six availability states | Mandatory | `models.AVAILABILITY_STATES`; invalid values raise at construction | `test_availability.py::TestStateVocabularies::test_exactly_the_six_organiser_availability_states_exist` | [`../DATA_SCHEMA.md`](../DATA_SCHEMA.md) |
| 3 | Exactly the five terminal states | Mandatory | `models.TERMINAL_STATES`; `pipeline._terminal_state()` | `test_availability.py::TestStateVocabularies::test_exactly_the_five_terminal_states_exist`; `out/eval100b-report.json` → 22/77/1 | [`../DATA_SCHEMA.md`](../DATA_SCHEMA.md) |
| 4 | Required envelope sections present | Mandatory | `models.build_envelope()`; `store.verify()` checks per file | `test_availability.py::TestEnvelopeShape::test_the_organiser_required_sections_are_all_present`; `verification.envelopes_missing_required_keys: []` | [`../DATA_SCHEMA.md`](../DATA_SCHEMA.md) |
| 5 | Operational metrics under their contracted names (`requests`, `runtime_ms`, `third_party_cost_usd`) | Mandatory | `pipeline.py` operations block (introduced in commit `23a7c21`) | Present in all 1,000 envelopes of the superseded run `out/superseded/submission-profiles-23a7c21.jsonl`. **Absent from all 300 envelopes of `out/eval100.jsonl`, `out/eval100-refresh.jsonl` and `out/eval100b.jsonl`**, which predate that commit. `store.verify()` checks only top-level keys, so `verification.ok` does not attest these. Presence in the regenerated run: pending. | [`../DATA_SCHEMA.md`](../DATA_SCHEMA.md#compatibility-with-the-required-contract) |
| 6 | Additional envelope keys (`identity`, `snapshots`, `synthesis`, `refresh`, `schema_version`) | Choice | `models.build_envelope()` | Same | [`../DATA_SCHEMA.md`](../DATA_SCHEMA.md) |

## Never fabricate, never zero-fill

| # | Requirement | Kind | Implementation | Evidence | Documented in |
| --- | --- | --- | --- | --- | --- |
| 7 | An unavailable measurement is never reported as zero | Mandatory | `Claim.__post_init__` refuses a value on any non-`available` claim; `parse_norwegian_number` returns `None`, not 0 | `test_availability.py::TestValueAndStateAgreement::test_a_non_available_claim_must_not_smuggle_a_value`; `test_extract.py::TestNorwegianNumbers::test_non_numbers_return_none_not_zero` | [`../DATA_SCHEMA.md`](../DATA_SCHEMA.md) |
| 8 | A genuine zero stays a value | Mandatory | Same invariant, inverted | `TestValueAndStateAgreement::test_zero_is_a_value_and_stays_available`; `TestNorwegianNumbers::test_zero_is_a_value_not_a_missing_field` | [`../DATA_SCHEMA.md`](../DATA_SCHEMA.md) |
| 9 | `available` requires a value | Mandatory | `Claim.__post_init__` | `TestValueAndStateAgreement::test_available_without_a_value_is_refused` | [`../DATA_SCHEMA.md`](../DATA_SCHEMA.md) |
| 10 | No fabricated financials | Mandatory | `registry.py` emits only figures the register returned, with period, scope, unit and currency. No interpolation, no carry-forward, no derivation. | `test_availability.py::TestValueAndStateAgreement`; `out/eval100b.jsonl` financial claims carry `unit`, `currency`, `reporting_period`, `scope` | [`../DATA_SCHEMA.md`](../DATA_SCHEMA.md), [`../AGENT.md`](../AGENT.md) |
| 11 | An absent employee count is not 0 | Mandatory | `harRegistrertAntallAnsatte is False` → `not_available` | `registry.py`; `test_availability.py` invariant | [`../SOURCE_RIGHTS.md`](../SOURCE_RIGHTS.md) |
| 12 | Detected figure scale is reported, not silently applied | Choice | `parse_norwegian_number(..., context=)` returns `(value, scale_hint)` | `TestNorwegianNumbers::test_stated_scale_is_reported_rather_than_applied_silently`, `test_no_scale_hint_reports_none` | [`../DATA_SCHEMA.md`](../DATA_SCHEMA.md) |

## Identity before publication

| # | Requirement | Kind | Implementation | Evidence | Documented in |
| --- | --- | --- | --- | --- | --- |
| 13 | No claim published without tying the source to the exact legal entity | Mandatory | `identity.IdentityResolver._decide()`; `PUBLISH_THRESHOLD = 1.0` | `test_identity.py::TestWebsiteGate`; `test_website.py::TestCorroboration` | [`../IDENTITY_RESOLUTION.md`](../IDENTITY_RESOLUTION.md) |
| 14 | Organisation number validated properly, not by length | Mandatory | `valid_org_number()` — MOD-11 weights (3,2,7,6,5,4,3,2), check digit 10 invalid, first digit 8 or 9 | `test_identity.py::TestOrganisationNumbers`, incl. `test_nine_digit_runs_that_are_not_org_numbers_are_ignored` | [`../IDENTITY_RESOLUTION.md`](../IDENTITY_RESOLUTION.md) |
| 15 | A source self-identifying as another entity is rejected unconditionally | Choice | `_decide()` conflict branch, evaluated before any score | `TestNameIsOneFact::test_a_page_naming_the_stem_under_another_legal_form_is_refused` | [`../IDENTITY_RESOLUTION.md`](../IDENTITY_RESOLUTION.md) |
| 16 | Repeated name evidence counts once (signal grouping) | Choice | `SIGNAL_GROUPS`; publication needs 2 independent groups | `TestNameIsOneFact::test_the_name_repeated_in_text_title_and_domain_does_not_publish`; `TestWebsiteGate::test_supporting_signals_must_pair_up` | [`../IDENTITY_RESOLUTION.md`](../IDENTITY_RESOLUTION.md) |
| 17 | Discovery priors never contribute to the identity score | Choice | `discovery.propose()` priors are ordering-only | `test_discovery.py::TestPrecisionGuards::test_priors_are_ordering_only_and_never_publish` | [`../AGENT.md`](../AGENT.md) |
| 18 | Near-tied candidates resolve to ambiguous, not to a pick | Choice | `choose_best()`, 0.02 confidence band | `identity.py` | [`../IDENTITY_RESOLUTION.md`](../IDENTITY_RESOLUTION.md) |
| 19 | A linked social profile is not published on the link alone | Choice | `assess_profile()` requires the handle to name the company or brand | `TestProfileGate::test_a_link_from_the_site_alone_does_not_publish`; `test_website.py::TestSocialProfileEmission::test_theme_vendor_and_opaque_channel_are_not_published` | [`../IDENTITY_RESOLUTION.md`](../IDENTITY_RESOLUTION.md) |
| 20 | No material wrong-company publication | Mandatory | The gate above | **Not independently measured.** Tests cover known failure patterns; the real-world rate is unknown. | [`../LIMITATIONS.md`](../LIMITATIONS.md) |

## Evidence

| # | Requirement | Kind | Implementation | Evidence | Documented in |
| --- | --- | --- | --- | --- | --- |
| 21 | Every published material claim carries resolvable evidence | Mandatory | `models.validate_result()`; `Claim.is_material` | `test_availability.py::TestEvidenceResolution::test_a_published_fact_without_evidence_is_a_defect`, `test_evidence_that_does_not_exist_is_a_defect`, `test_evidence_pointing_at_no_snapshot_is_a_defect`; `test_website.py::TestCitedSnapshots`. **Not fully satisfied in measured runs:** evidence cited a snapshot not stored in the envelope for 1 company in `out/eval100b.jsonl` (935654866) and 2 in the superseded 1,000-company run (935654866, 997712897). Cause fixed in commit `1e06568`; the in-run validator recorded these (`operations.validation_problems`, `validation_failed` error) but the envelopes stayed `completed` and `verification.ok` stayed true — that reporting gap is not fixed. | [`../DATA_SCHEMA.md`](../DATA_SCHEMA.md#validation), [`../LIMITATIONS.md`](../LIMITATIONS.md#10-the-1000-company-run-was-superseded-by-a-defect-fix) |
| 22 | Evidence locates the value inside the source, not just the URL | Mandatory | `models.Locator` — `json_pointer`, `css_selector`, `text_span`, `table_cell`, `pdf_page`, `row_key` | `TestEvidenceResolution::test_a_locator_is_carried_so_the_exact_span_can_be_found`; real chain in `out/eval100b.jsonl` (org 979498047 revenue → RFC 6901 pointer) | [`../DATA_SCHEMA.md`](../DATA_SCHEMA.md) |
| 23 | Source retrieval is recorded with URL, time and content hash | Mandatory | `models.Snapshot`; content-addressed `fetch.SnapshotStore` (SHA-256). Bodies are written only when the run is given `--snapshots DIR`; snapshots in an envelope are deduplicated by id | Any envelope's `snapshots` array (URL, time, hash). `out/eval100b.jsonl` stored no bodies (all 543 `stored_path` null). Superseded 1,000-company run: 4,556 distinct stored bodies re-hashed to their recorded SHA-256 with no mismatch (`out/superseded/integrity-report-23a7c21.json`). Regenerated run: pending, `out/integrity-report.json`. | [`../DATA_SCHEMA.md`](../DATA_SCHEMA.md), [`../CRAWLERS.md`](../CRAWLERS.md) |
| 24 | Source class distinguishes official from company-owned | Choice | `models.SourceClass`, 9 values | Any envelope's `evidence` array | [`../SOURCE_RIGHTS.md`](../SOURCE_RIGHTS.md) |
| 25 | A claim may not be attributed to another company | Mandatory | `validate_result()` | `TestClaimIntegrity::test_a_claim_about_another_company_is_a_defect` | [`../DATA_SCHEMA.md`](../DATA_SCHEMA.md) |
| 26 | Derived claims may cite claims instead of a source | Choice | `Claim.derived_from` | `TestEvidenceResolution::test_a_derived_claim_may_cite_other_claims_instead_of_a_source` | [`../DATA_SCHEMA.md`](../DATA_SCHEMA.md) |
| 26a | Offline audit of a finished run's evidence chain | Optional | `scripts/signalpost/check_integrity.py`: claim → evidence → snapshot resolution, snapshot body re-hash (`--snapshot-root`), financial period/unit/currency, UTC timestamps, http(s) URLs, duplicate claim keys, unavailable claims carry no value and a reason; `--expected` runs `store.verify()`; exits 1 on any problem. Automated integrity only — it does not check whether a claim is true. | Superseded run: `out/superseded/integrity-report-23a7c21.json` — 2 real `evidence_unresolved_snapshot` problems. That archived report also lists 2,000 financial currency/unit problems on boolean `accounts_audited` claims from an earlier revision of the script; the committed script, re-run on the archived file, reports only the 2. Regenerated run: pending, `out/integrity-report.json`. | [`../EVAL.md`](../EVAL.md#offline-run-audits) |

## Refresh

| # | Requirement | Kind | Implementation | Evidence | Documented in |
| --- | --- | --- | --- | --- | --- |
| 27 | Previous state is accepted as input | Mandatory | `--previous-snapshot`; `store.load_previous()` | `out/eval100-refresh-report.json` → `config.previous_snapshot: "out/eval100.jsonl"` | [`../REFRESH.md`](../REFRESH.md) |
| 28 | Claim keys are stable across runs | Mandatory | `models.claim_key()` — no timestamps, whitespace, ordering or tracking parameters | `test_availability.py::TestClaimKeys` (5 tests) | [`../REFRESH.md`](../REFRESH.md) |
| 29 | A re-run over unchanged sources yields zero false changes | Mandatory | `refresh.normalise_value()`; `replay_check()` runs in-pipeline | `test_refresh.py::TestIdempotency` (3 tests), `TestFalseChangeImmunity` (5 tests) | [`../REFRESH.md`](../REFRESH.md) |
| 30 | Real changes are detected and classified | Mandatory | `refresh.diff()`; `CHANGE_TYPES` | `test_refresh.py::TestRealChanges` (4 tests, incl. bankruptcy) | [`../REFRESH.md`](../REFRESH.md) |
| 31 | Material and cosmetic changes are distinguished | Choice | `MATERIAL_FIELDS` (21), `COSMETIC_FIELDS` (3) | `test_refresh.py::TestMateriality` | [`../REFRESH.md`](../REFRESH.md) |
| 32 | Losing access to a source is not a business change | Choice | `_is_material()` — `availability_changed` is never material | `TestMateriality::test_losing_access_to_a_source_is_not_a_fact_about_the_company` | [`../REFRESH.md`](../REFRESH.md) |
| 33 | First-observation time survives a re-run | Choice | `carry_observation_times()` | `test_refresh.py::TestObservationTimes` | [`../REFRESH.md`](../REFRESH.md) |
| 34 | Live refresh produces near-zero false changes | Mandatory | As above | `out/eval100-refresh-report.json` → 1 change over 100 companies, and that one was our own URL canonicalisation landing between runs, not source churn. A like-for-like zero against re-crawled live sources is **pending verification**. | [`../REFRESH.md`](../REFRESH.md) |
| 34a | Offline idempotency audit of a finished run | Optional | `scripts/signalpost/check_idempotency.py`: rebuilds each envelope's claims and runs `refresh.replay_check()` against the same envelope, counting false changes | Superseded run: `out/superseded/idempotency-report-23a7c21.json` — 1,000 companies, 40,291 claims, 0 false changes. **This is a self-diff of stored claims, not a re-crawl**; it tests key and value normalisation stability, not behaviour against live sources. Regenerated run: pending, `out/idempotency-report.json`. | [`../EVAL.md`](../EVAL.md#offline-run-audits), [`../REFRESH.md`](../REFRESH.md) |

## Budget and the locked envelope

| # | Requirement | Kind | Implementation | Evidence | Documented in |
| --- | --- | --- | --- | --- | --- |
| 35 | 100 inputs / 45 min / 2,000 requests / $10 spend | Mandatory | `budget.py` defaults: 2000, 2700 s, $10.0 | `test_budget.py::TestLockedEnvelope::test_defaults_match_the_published_limits` | [`../CRAWLERS.md`](../CRAWLERS.md) |
| 36 | Redirects and retries count as requests | Mandatory | Charged per attempt and per hop | `TestRequestAccounting::test_redirects_and_retries_are_charged_by_count` | [`../CRAWLERS.md`](../CRAWLERS.md) |
| 37 | The limit is refused, not overrun | Mandatory | Reservation-before-dispatch under one lock | `TestRequestAccounting::test_the_limit_is_refused_rather_than_overrun`; `TestSpend::test_spend_is_refused_rather_than_overrunning_the_cap` | [`../CRAWLERS.md`](../CRAWLERS.md) |
| 38 | The cap holds under concurrency | Mandatory | Single-lock reservation; release on exception | `TestConcurrency::test_the_hard_limit_holds_under_concurrent_workers`, `test_no_reservation_is_leaked_under_concurrent_failures` | [`../CRAWLERS.md`](../CRAWLERS.md) |
| 39 | Run completes inside the envelope | Mandatory | — | `out/eval100b-report.json`: 797/2000 requests, 389.0/2700 s, $0.00/$10.00, `denials: {}` | [`../COSTS.md`](../COSTS.md) |
| 40 | Batch tail is not starved by its head | Choice | `runner._page_allowance()`, soft ratio 0.90, time/request reserves | `TestFairShare` (2 tests), `TestSoftStop` (2 tests) | [`../CRAWLERS.md`](../CRAWLERS.md) |
| 41 | Declared third-party spend | Mandatory | Real ledger; measured $0.00 | `out/eval100b-report.json` → `budget.spend_usd: 0.0` | [`../COSTS.md`](../COSTS.md) |

## Source rights and security

| # | Requirement | Kind | Implementation | Evidence | Documented in |
| --- | --- | --- | --- | --- | --- |
| 42 | Sources used lawfully with declared licences | Mandatory | BRREG under NLOD 2.0; company sites under robots.txt | `out/eval100b-report.json` → `top_hosts` shows only `data.brreg.no` and company hosts | [`../SOURCE_RIGHTS.md`](../SOURCE_RIGHTS.md) |
| 43 | robots.txt obeyed | Mandatory | `fetch.RobotsPolicy`, fetched before any page on a host | 169 `robots` requests in `eval100b` | [`../CRAWLERS.md`](../CRAWLERS.md) |
| 44 | Identifying user-agent with contact | Mandatory | `fetch.py` | [`SECURITY.md`](SECURITY.md) | [`SECURITY.md`](SECURITY.md) |
| 45 | URL safety: scheme, port, credentials, private addresses | Mandatory | `net.py` — allowlists, blocklists, public-routability check per resolved address | `test_net.py::TestSchemeAndCredentials`, `TestPrivateAddresses` | [`SECURITY.md`](SECURITY.md) |
| 46 | DNS-rebinding closure and per-hop redirect revalidation | Choice | Pinned-IP connections with real-hostname SNI; manual one-hop redirects, max 4 | `test_net.py`; [`SECURITY.md`](SECURITY.md) | [`SECURITY.md`](SECURITY.md) |
| 47 | Secrets never enter URLs, snapshots or stored metadata | Mandatory | `net.SENSITIVE_QUERY_KEYS`, `SENSITIVE_HEADERS` | `test_net.py::TestRedaction` | [`SECURITY.md`](SECURITY.md) |
| 48 | Untrusted page content cannot steer the agent | Mandatory | `extract/html.py` — extractors only read typed values; `detect_injection()` records, `strip_injection_bait()` neutralises | `test_extract.py::TestPromptInjection` (3 tests) | [`SECURITY.md`](SECURITY.md), [`../AGENT.md`](../AGENT.md) |
| 49 | Personal data minimised | Choice | Role holders' birth dates never read; role-mailbox and platform addresses filtered | `registry.py`; `extract/fields.py` | [`../SOURCE_RIGHTS.md`](../SOURCE_RIGHTS.md) |
| 50 | No credentials required to run | Choice | No env reads anywhere in `src/signalpost/` | `grep -rn "environ\|getenv\|openai\|anthropic" src/signalpost/` returns nothing (2026-09-14) | [`../MODELS_AND_APIS.md`](../MODELS_AND_APIS.md) |

## Corpus and scale

| # | Requirement | Kind | Implementation | Evidence | Documented in |
| --- | --- | --- | --- | --- | --- |
| 51 | At least 1,000 profiles plus a manifest | Mandatory | `corpus.select()`, `stratified-proportional-sha256-v1`, seed `signalpost-round-1` | Manifest: `data/corpus/selection.json` (1,000 selected, universe SHA-256 recorded). **Profiles:** a first run produced 1,000 envelopes, one per input (186 `completed`, 814 `partial`, 0 `failed`), and has been **superseded** by the defect fix in `1e06568`; archived as `out/superseded/submission-profiles-23a7c21.jsonl` and `-report.json`. The regenerated run writes `out/submission-profiles.jsonl`; its totals will be in `out/submission-profiles-report.json` and are **pending**. | [`../EVAL.md`](../EVAL.md) |
| 52 | Corpus selection is reproducible | Choice | Deterministic SHA-256 rank within proportional NACE strata | `data/corpus/selection.json` records seed, algorithm, universe hash, manifest hash | [`../EVAL.md`](../EVAL.md) |
| 53 | Exactly 100 terminal envelopes for a 100-input run | Mandatory | `store.verify()` | `out/eval100b-report.json` → 100 envelopes, `exactly_one_per_input: true`, `missing: []`, `unexpected: []` | [`../EVAL.md`](../EVAL.md) |

## Documentation deliverables

| # | File | Kind | Status |
| --- | --- | --- | --- |
| 54 | [`../README.md`](../README.md) | Mandatory | Written |
| 55 | [`../AGENT.md`](../AGENT.md) | Mandatory | Written |
| 56 | [`../CRAWLERS.md`](../CRAWLERS.md) | Mandatory | Written |
| 57 | [`../IDENTITY_RESOLUTION.md`](../IDENTITY_RESOLUTION.md) | Mandatory | Written |
| 58 | [`../DATA_SCHEMA.md`](../DATA_SCHEMA.md) | Mandatory | Written |
| 59 | [`../REFRESH.md`](../REFRESH.md) | Mandatory | Written |
| 60 | [`../EVAL.md`](../EVAL.md) | Mandatory | Written |
| 61 | [`../SOURCE_RIGHTS.md`](../SOURCE_RIGHTS.md) | Mandatory | Written |
| 62 | [`../MODELS_AND_APIS.md`](../MODELS_AND_APIS.md) | Mandatory | Written |
| 63 | [`../COSTS.md`](../COSTS.md) | Mandatory | Written; superseded 1,000-run figures labelled as such; regenerated 1,000-run totals pending |
| 64 | [`SECURITY.md`](SECURITY.md) | Mandatory | Written (pre-existing) |
| 65 | [`../LIMITATIONS.md`](../LIMITATIONS.md) | Mandatory | Written |
| 66 | `../SUBMISSION_README.md` | Mandatory | Owned elsewhere |
| 67 | `../SUBMISSION_CHECKLIST.md` | Mandatory | Owned elsewhere |
| 68 | This file | Mandatory | Written |
| 69 | [`PRIZES_AND_ELIGIBILITY.md`](PRIZES_AND_ELIGIBILITY.md) | Mandatory | Written (pre-existing) |
| 70 | [`BUILD_STATUS.md`](BUILD_STATUS.md) | Mandatory | Owned elsewhere |
| 71 | `../DEMO_WALKTHROUGH.md` | Optional | Not written. Explicitly optional in the brief. |
| 72 | Architecture diagram and screenshots | Optional | Not included. No screenshot has been taken, and the UI's page content is unverified. |
| 73 | Video or pitch deck | Optional | Not produced. Not a listed gate. |

## Requirements not satisfied

Listed separately so they are not buried in a table of green rows.

| # | Requirement | Status | Detail |
| --- | --- | --- | --- |
| 74 | Independently reviewed research quality | **Not satisfied** | No human accuracy review has been performed. No exact-entity precision, no wrong-company rate, no external recall. [`../EVAL.md`](../EVAL.md), [`../LIMITATIONS.md`](../LIMITATIONS.md) |
| 75 | ≥ 1,000 completed profiles | **Pending regeneration** | The first 1,000-company run completed (1,000 envelopes, 0 `failed`) but is superseded: 2 of its envelopes carry the evidence defect fixed in `1e06568`. The regenerated run on that commit was in progress at the time of writing; its report will be `out/submission-profiles-report.json`. "Completed" here means one terminal envelope per input; 814 of the superseded envelopes were `partial`. |
| 75a | A validation defect detected in-run is visible in the run report | **Not satisfied** | `validate_result()` defects are counted in `operations.validation_problems` and logged as a `validation_failed` error, but they do not change the terminal state and `store.verify()` does not read them, so `verification.ok` was true on runs containing them (`out/eval100b-report.json`, `out/superseded/submission-profiles-23a7c21-report.json`). Detection currently depends on running `check_integrity.py` afterwards. [`../LIMITATIONS.md`](../LIMITATIONS.md#10-the-1000-company-run-was-superseded-by-a-defect-fix) |
| 75b | Per-100 evidence produced by the current code | **Not satisfied** | `out/eval100b-report.json` predates commits `23a7c21` and `1e06568`; it lacks the contracted operation keys and contains one evidence defect. No 100-company run of the current code exists. [`../EVAL.md`](../EVAL.md#which-code-produced-which-run) |
| 76 | JBOX evidence/status | **Not satisfied** | No JBOX integration exists. [`../LIMITATIONS.md`](../LIMITATIONS.md) |
| 77 | UI command `uv run serve.py` | **Partly verified** | Starts and serves after `uv sync --extra ui`; routes return 200 (smoke-checked 2026-09-14). Page correctness, the research action, browser behaviour and any UI test remain unverified. [`../LIMITATIONS.md`](../LIMITATIONS.md) |
| 78 | Weighted external company recall ≥ 60% | **Cannot be evaluated here** | Requires ground truth we do not have. Not claimed. |
| 79 | External exact-entity precision ≥ 95% | **Cannot be evaluated here** | Same. |
| 80 | Coverage ≥ 21/35 | **Cannot be evaluated here** | Coverage uses recall terms that require ground truth. Our availability proxy is in [`../EVAL.md`](../EVAL.md), clearly labelled as not the metric. |
| 81 | Score ≥ 65/100 and qualification | **Not claimed** | Official qualification status is `not_determined`. |

## Unresolved organiser rules

Recorded as unresolved rather than settled by our own interpretation.

| Item | The contradiction or gap | How we proceeded |
| --- | --- | --- |
| **Stale scoring language** | "External intelligence is 55/100" (in `external-connectors.md`, quoting organiser material) does not reconcile with scoring v2: 35 coverage + 30 accuracy/identity/evidence + 20 refresh + 10 synthesis + 5 usability. | Built to scoring v2; claimed no score under either weighting. |
| **Four-version allowance** | Ambiguous about what constitutes a version and how versions are scored relative to one another. | Prepared a single submission; made no assumption. |
| **Sparse-field scoring** | Unspecified whether a legitimately absent or `not_applicable` field is neutral or penalised in coverage. This determines whether abstention is correct or self-defeating. | Emit all six states with reasons; let the scorer apply its rule. |
| **Failed/missed-run averaging** | Unspecified how a failed run or an unresearchable company averages into a score. | Every company terminates with an envelope and an explicit state, so none is silently absent under any rule. |

See also the unresolved-by-organiser section of
[`PRIZES_AND_ELIGIBILITY.md`](PRIZES_AND_ELIGIBILITY.md).
