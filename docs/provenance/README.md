# Requirement provenance

Every requirement this entry is built against is traced here to the exact organiser
document it came from, with a quotation. The purpose is narrow: if a reviewer disputes a
design decision, the disagreement should be about the decision, not about whether we read
the rules correctly.

Three things this file deliberately does **not** do:

- It does not redistribute the organiser's material. `competition-material.json` records a
  SHA-256 for each document instead, so a reviewer can confirm we read the same bytes they
  published. See [`../SOURCE_RIGHTS.md`](../SOURCE_RIGHTS.md).
- It does not resolve the organiser's own open questions. Where the published documents
  disagree, that is recorded as a disagreement.
- It does not restate the scoring rubric as if it were our score. Nothing here is a claim
  about how this entry will be scored.

## Documents

| File | Source | Retrieved |
|---|---|---|
| `brief.md` | `https://builderr.ai/starter-briefs/signalpost.md` | 2026-09-13 |
| `eval-harness.md` | `https://builderr.ai/docs/signalpost-evaluation-harness.md` | 2026-09-13 |
| `sources.md` | `https://builderr.ai/starter-briefs/signalpost-sources.md` | 2026-09-13 |
| `agent-playbook.md` | `https://builderr.ai/starter-briefs/signalpost-agent-playbook.md` | 2026-09-13 |
| `learning-harness.md` | `https://builderr.ai/starter-briefs/signalpost-learning-harness.md` | 2026-09-13 |
| `challenge-page.html` | `https://builderr.ai/challenges/signalpost` | 2026-09-13 |
| `product.html` | `https://builderr.ai/signalpost` | 2026-09-13 |
| `guidelines.html` | `https://builderr.ai/guidelines` | 2026-09-13 |
| `start.html` | `https://builderr.ai/start` | 2026-09-13 |

Exact byte counts, checksums and retrieval timestamps: `competition-material.json`.

## The envelope contract

> "Exactly one terminal envelope per input organisation number. Envelopes must contain
> legal identity, claims, evidence references, availability states, source snapshots,
> refresh metadata and errors. Valid states are `available`, `not_available`, `blocked`,
> `not_applicable`, `ambiguous` and `failed`."
> — `eval-harness.md`, *Output contract*

> "Every input must end in one terminal envelope, even when sources are missing or
> blocked." — `brief.md`, *Required company envelope*

Implemented in `src/signalpost/models.py`. The six states are a frozenset validated at
claim construction, so a seventh cannot be introduced by a typo; the five terminal states
are checked by `store.verify()` over the finished run. Pinned by
`tests/signalpost/test_availability.py`.

> "Never turn absence into zero." — `brief.md`
>
> "Missing values are never silently converted to zero." — `eval-harness.md`, *Hard gates*

`parse_norwegian_number` returns `None` for an unparseable value and `0.0` only for a
literal zero, and a claim that is not `available` is forbidden from carrying a value at
all. Pinned by `test_extract.py::TestNorwegianNumbers` and
`test_availability.py::TestValueAndStateAgreement`.

## Identity before publication

> "A profile or domain must resolve to the exact legal entity before its facts are
> published." — `sources.md`, *Publication rules*

> "Search results generate candidates; they are not claim evidence." — `sources.md`

This is the sharpest constraint in the competition and it shapes the whole design.
`src/signalpost/discovery.py` generates candidates and assigns them ordering priors;
`src/signalpost/identity.py` decides publication and never sees those priors. The module
docstring states the split directly: discovery proposes, the identity gate disposes.

> "It is better to miss some information than publish it under the wrong company; return
> `ambiguous` or `not_available` when the company match is uncertain." — `brief.md`

> "A material wrong-company match can contaminate an entire profile, so the score remains
> visible but the entry does not qualify until the match is corrected." — `brief.md`

`PUBLISH_THRESHOLD` is set so that a single weak signal — a guessed domain containing a
name token, a city that matches — cannot publish on its own. Pinned by
`test_identity.py::TestWebsiteGate`, which asserts refusal on a domain token alone and on a
page naming a different entity.

## Evidence

> "Every claim records source URL or source identifier, retrieval time, effective/reporting
> date where relevant, content hash and extraction method." — `sources.md`

> "Published material claims have source, retrieval time and reporting period where
> relevant." — `eval-harness.md`, *Hard gates*

A claim carries evidence ids; evidence carries a snapshot id, a content hash and a
`Locator` naming the exact span, table cell, JSON pointer or PDF page. `validate_result`
refuses to publish a material claim whose evidence does not resolve through to a snapshot.
Pinned by `test_availability.py::TestEvidenceResolution`.

## Refresh

> "Re-running the same snapshot is idempotent." / "Refresh preserves prior evidence and
> exposes material changes." — `eval-harness.md`, *Hard gates*

> "Refresh must be idempotent: the same source snapshot must not create duplicate records
> or false changes." — `challenge-page.html`

`refresh.replay_check` diffs a run against its own output and must report zero changes.
Comparison is by stable claim key after normalisation, with retrieval metadata excluded.
Pinned by `test_refresh.py::TestIdempotency` and `TestFalseChangeImmunity`.

## The locked run budget

> "100 inputs / 45 minutes wall clock / 8 vCPU, 16 GB RAM and 10 GB temporary disk / 2,000
> total outbound requests, including redirects and retries / $10 maximum declared
> third-party API spend" — `eval-harness.md`, *Locked evaluator budget*

> "Redirects and retries count toward requests; cache hits are free. Builderr-supplied
> official snapshots do not count toward third-party spend." — `challenge-page.html`

These are the defaults in `src/signalpost/budget.py`, reserved before work rather than
counted after it. The measured figures for a real 100-company run are in
[`../BUILD_STATUS.md`](../BUILD_STATUS.md) and [`../COSTS.md`](../COSTS.md); nothing there
is an estimate presented as a measurement.

One consequence worth stating, because it drove a design decision: a DNS lookup is not an
outbound HTTP request. Discovery resolves candidate domains before fetching them, so a
guessed domain that does not exist costs no request budget at all.

## Reproducibility and safety

> "Setup is reproducible with pinned dependencies and one evaluator command." / "Source
> rights, secrets and outbound URL policy are documented and safe."
> — `eval-harness.md`, *Hard gates*

One command (`uv run main.py …`), a committed `uv.lock`, and `.gitattributes` pinning LF so
the manifest checksum reproduces on a Windows checkout. URL safety is in
`src/signalpost/net.py` and documented in [`../SECURITY.md`](../SECURITY.md).

## Source restrictions

> "Do not scrape a platform when its terms, robots policy or applicable law prohibit the
> submitted method. This includes treating unofficial LinkedIn, Meta, Glassdoor, Indeed or
> Google clients as automatically acceptable simply because code exists on GitHub."
> — `sources.md`, *Restricted platforms and unofficial crawlers*

> "Company-owned promotional copy may describe the business but cannot provide an
> independent sentiment claim." — `sources.md`

Recorded in [`../SOURCE_RIGHTS.md`](../SOURCE_RIGHTS.md), with the list of connectors that
exist in the repository but are **not** part of the submitted run path.

## What the organiser has not settled

These are quoted rather than resolved, because guessing at them would be inventing a rule:

> "The documents differ on whether the four-version limit includes the first submission.
> Revisions close 18 October; ask Builderr before submitting a fifth version. Handling of
> sparse information types and failed or missed daily runs also needs clarification. Do not
> infer a new scoring rule from this copy update."
> — `eval-harness.md` and `brief.md`, identical closing sections

The entry therefore assumes it has **one** submission and treats revisions as scarce.

## Dates and deadline

`brief.md` and the challenge page agree that Round 1 runs 23 August – 21 October 2026, that
revisions close 18 October, and that entries close 21 October 2026. **No time of day or
timezone is published for the 21 October close**, so none is assumed anywhere in this
repository. Anything scheduled against that date should treat it as the start of 21 October
in the earliest plausible timezone, or ask the organiser.
