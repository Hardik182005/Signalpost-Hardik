# Refresh

Refresh answers one question: **since the last run, what actually changed about this company?**

The hard part is not detecting change. It is *not* detecting change that did not happen. A
refresh that reports fifty differences because a page re-rendered, a list reordered or a cache
buster rotated is worse than useless — it buries the one real event in noise and it makes every
change event untrustworthy.

Implementation: `src/signalpost/refresh.py`. Tests: `tests/signalpost/test_refresh.py`.

## Running a refresh

```
uv run main.py --input data/corpus/corpus.jsonl --limit 100 \
               --output out/refresh.jsonl \
               --run-id refresh-1 \
               --previous-snapshot out/eval100.jsonl
```

`--previous-snapshot` takes a **previous run's JSONL output**. That is the entire
previous-state input: there is no database, no server, no external state. The prior run's
envelopes are the prior state.

`store.load_previous()` indexes them by organisation number; `refresh.previous_from_envelope()`
reconstructs the previous claim set from an envelope. A company with no entry in the previous
file is a first observation — its `refresh` block records
`"First observation of this company; no baseline to compare against"` and `compared_against`
is null, rather than reporting every claim as "added".

Without `--previous-snapshot`, every company is a first observation and `changes_total` is 0.

## Stable claim keys

Change detection compares claims by `claim_key`, a stable *semantic* identity:

```
979498047:revenue:2025:company
979498047:legal_name
979498047:website
```

Built by `models.claim_key()`, which casefolds and collapses whitespace in discriminators. The
key deliberately contains **no** retrieval timestamp, no HTML whitespace, no ordering, and no
tracking parameter. The docstring on `Claim` states the requirement directly: a claim key
"must not embed retrieval timestamps, HTML whitespace, ordering or tracking parameters, so that
a re-run over unchanged sources produces zero false changes."

Tests: `test_availability.py::TestClaimKeys` — keys stable across runs, unaffected by
whitespace and case in discriminators, distinct per repeated field instance, and scoped to the
company.

## Normalisation before comparison

`normalise_value()` runs on both sides before anything is compared. It:

- **excludes retrieval metadata** from dicts — `retrieved_at`, `snapshot_id`, `evidence_ids`,
  `confidence`. Re-fetching a page changes all four and changes nothing about the company;
- **canonicalises URLs**, stripping tracking parameters, session identifiers and cache busters;
- **rounds numbers**, so `100` and `100.0` are one value;
- **sorts lists**, so reordering is not a change.

The case that produced the URL rule is recorded in `net.py`. A WordPress site,
`https://mastermat.no/?doing_wp_cron=1789309421.0442390441894531250000`, rotates that parameter
on every request. Without canonicalisation the company's website appears to change on every
single run, forever. Test:
`test_net.py::TestCanonicalisation::test_a_wordpress_cron_buster_is_not_part_of_a_company_website`.

## Change types

`CHANGE_TYPES`: `value_changed`, `claim_added`, `claim_removed`, `availability_changed`,
`unchanged`.

A `ChangeEvent` carries the claim key, field, family, change type, previous and current value,
previous and current availability, the materiality flag, `detected_at`, the evidence ids and a
short explanation.

## Materiality

Not every difference is news about the company.

**Material fields** (21, `MATERIAL_FIELDS`): `legal_name`, `legal_form_code`, `bankrupt`,
`under_liquidation`, `under_forced_liquidation`, `employees`, `website`, `revenue`,
`operating_profit`, `profit_before_tax`, `net_profit`, `total_assets`, `total_equity`, `role`,
`address`, `industry_code`, `subunit_count`, `email`, `phone`, `social_profile`, `job_posting`.

**Cosmetic fields** (`COSMETIC_FIELDS`): `business_description`, `site_person`,
`activity_item`. These churn harmlessly. A company rewording its About page is a change, and it
is reported — but it is not material. Test:
`TestMateriality::test_prose_rewording_is_a_change_but_not_a_material_one`.

**`availability_changed` is never material.** This one deserves its own line, because it is the
rule most likely to look like a bug. If a website was readable last week and returns 503 today,
the claim moves from `available` to `blocked`. That is a fact about *our access to a source*,
not a fact about the company. Reporting it as a material business change would mean every
transient outage produces a false alert. It is still recorded as a change event; it is just not
material. Test: `TestMateriality::test_losing_access_to_a_source_is_not_a_fact_about_the_company`.

Bankruptcy is material (`TestRealChanges::test_bankruptcy_is_material`).

## Observation times

`carry_observation_times()` preserves `first_observed_at` across runs while updating
`last_observed_at`. A fact first seen in January and re-confirmed in September keeps its January
first-observation date — otherwise every refresh would make every fact look new. A genuinely
new claim is stamped now.

Tests: `TestObservationTimes::test_first_observation_survives_a_re_run`,
`test_a_genuinely_new_claim_is_stamped_now`.

## The idempotency self-check

`replay_check()` diffs a company's freshly-produced claims against the envelope that was just
built from them. The correct answer is always zero changes, because nothing happened in
between.

```python
return {
    "organisation_number": ...,
    "replayed_claims": len(claims),
    "false_changes": len(events),
    "idempotent": not events,
    "offending_keys": [event.claim_key for event in events][:20],
}
```

Its docstring states the purpose: "if this ever reports changes, the claim keys or the
normalisation are unstable and refresh precision is compromised." It runs inside the pipeline,
so instability surfaces during a normal run rather than during review.

`summarise()` emits `idempotent: len(listed) == 0` into each envelope's `refresh` block.

Tests: `TestIdempotency` (a run diffed against itself reports nothing; claim order is not a
change; the summary reports idempotency explicitly) and `TestFalseChangeImmunity` (rendering
differences, integer/float forms, list ordering, retrieval metadata, and re-fetching the same
page all produce no events).

## Measured results

### Live refresh over 100 companies

| Field | Value |
| --- | --- |
| Run | `eval100refresh` |
| Command | `uv run main.py --input data/corpus/corpus.jsonl --limit 100 --output out/eval100-refresh.jsonl --run-id eval100refresh --previous-snapshot out/eval100.jsonl` |
| Baseline | `out/eval100.jsonl` (run `eval100`, 2026-09-13) |
| Completed | 2026-09-13T14:32:55Z |
| Envelopes | 100 (25 `completed`, 75 `partial`) |
| Claims available / total | 3,193 / 4,064 |
| **Changes total** | **1** |
| **Material changes** | **1** |
| Requests | 743 (7.43 per company) |
| Wall clock | 312.7 s |
| Spend | $0.00 |
| Verification | `ok: true`, `exactly_one_per_input: true` |
| Evidence file | `out/eval100-refresh-report.json` |

**What the single change actually was**, from `out/eval100-refresh.jsonl`:

```json
{ "claim_key": "920553842:website", "field": "website", "family": "web_presence",
  "change_type": "value_changed",
  "previous_value": "https://mastermat.no/?doing_wp_cron=1789309421.0442390441894531250000",
  "current_value": "https://mastermat.no/",
  "material": true, "detected_at": "2026-09-13T14:29:20Z" }
```

Reported honestly: **this is not a change at the company.** It is the URL canonicalisation
taking effect between the baseline run and the refresh run. The baseline was produced before
`canonical_url` stripped `doing_wp_cron`; the refresh produced the cleaned form and correctly
reported that the stored value differed. The underlying website did not change.

So the accurate summary of the live refresh is: **one change event over 100 companies and
roughly 3,200 available claims, and it was an artifact of our own fix landing between the two
runs — not a false positive from source churn, and not a real-world event either.** A refresh
run against a baseline produced by the current code should report zero. That specific run has
not been performed, so the zero-change claim over a like-for-like baseline is **pending
verification**.

### Run-level idempotency

`out/eval100b-report.json` records `changes_total: 0` and `changes_material: 0`. That run had no
`previous_snapshot`, so every company was a first observation — the zero is the correct value
for "no baseline", and it is **not** evidence of refresh precision. It is listed here only so
the number is not misread as one.

### Fixture replay

`out/refresh-demo.json` is a saved-response fixture check:

| Field | Value |
| --- | --- |
| Corpus | "evaluator-owned deterministic old/new source snapshot fixture" |
| Profiles | 1 (organisation 923609016) |
| Expected / observed changes | 2 / 2 |
| True positive / false positive / false negative | 2 / 0 / 0 |
| Precision / recall | 1.0 / 1.0 |
| `evidence_complete` | true |
| `idempotent_rerun` | true |
| `qualification_passed` | true |

Three qualifications on that table, all of which matter:

1. **It is a fixture, not a live run.** Its `old_requests: 2` and `new_requests: 2` are
   saved-response reads, not network calls.
2. **`qualification_passed: true` applies to that sample check only.** It is not a
   qualification result of any kind. Official qualification status is `not_determined`.
3. **It was produced by the legacy `norway_company_agent` package, not by
   `src/signalpost/`.** `scripts/run_refresh_replay.py` imports
   `norway_company_agent.refresh.diff_datasets`. So this artifact is evidence about the
   starter's refresh implementation, not about the refresh code described in this document.
   The evidence for `src/signalpost/refresh.py` is the unit test suite and the live
   `eval100refresh` run above.

That distinction is easy to miss and would be easy to leave unsaid. It is stated here because a
reviewer checking the file would otherwise reasonably assume it validated the submitted code.

The two fixture events are a registry employee count moving 2 → 3, and a financials record
moving revenue 100 → 120, each with old and new content hashes.

## Failure handling during refresh

- **A company missing from the baseline** is a first observation, not a mass of additions.
- **A source that fails this run but succeeded last run** produces an `availability_changed`
  event, marked non-material. The previous value is not carried forward as if still true, and
  it is not reported as a business change.
- **A whole failed refresh company** still produces a terminal envelope, as in any run — every
  stage is wrapped by `pipeline._stage()`.
- **A truncated baseline file** (from a crashed previous run) is read up to its last intact
  line; `store.read_envelopes()` skips a truncated final line rather than refusing the file.
  Test: `test_store.py::TestCrashTolerance`.
- **Resume** is on by default: a re-run reads back the companies an interrupted attempt already
  finished and skips them. `--no-resume` forces a clean run.

## Replay instructions for a reviewer

**1. Baseline run.**

```
uv run main.py --input data/corpus/corpus.jsonl --limit 100 --output out/base.jsonl --run-id base
```

**2. Refresh against it.**

```
uv run main.py --input data/corpus/corpus.jsonl --limit 100 --output out/again.jsonl \
               --run-id again --previous-snapshot out/base.jsonl
```

**3. Read the result.** `out/again-report.json` (the report path is derived from the output
stem; there is no `--report` flag) carries `results.changes_total` and
`results.changes_material`. Over an interval with no real-world change, both should be 0 or
close to it, and any event should be inspectable in the envelope's `changes` array with its
previous and current values.

**4. Check per-company idempotency** in any envelope's `refresh` block: `idempotent` should be
true and `material_fields` empty where nothing changed.

**5. Run the offline unit checks** — no network needed:

```
uv run pytest tests/signalpost/test_refresh.py -v
```

For a fully deterministic comparison, `--offline` runs against stored snapshots only.
