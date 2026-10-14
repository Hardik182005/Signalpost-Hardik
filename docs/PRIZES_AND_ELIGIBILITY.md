# Prizes, eligibility and what "qualified" actually means

Everything here is quoted or derived from the organiser's published material, checksummed
in [`provenance/competition-material.json`](provenance/competition-material.json) and
traced in [`provenance/README.md`](provenance/README.md). Where the documents are silent
or disagree, this file says so rather than filling the gap.

## Round 1

| | |
|---|---|
| Window | 23 August – 21 October 2026 |
| Daily evaluation began | 24 August 2026 |
| Scoring version 2 effective | 26 August 2026, applied retroactively to every Round 1 entrant |
| Revisions close | 18 October 2026 |
| Entries close | 21 October 2026 |
| Submission address | `submit@builderr.ai` |

**No time of day or timezone is published for the 21 October close.** This repository does
not assume one. If a decision depends on the exact cut-off, ask the organiser rather than
inferring it.

## Prize pools

**Main pool — $2,000**

| Place | Award |
|---|---|
| 1st | $1,200 |
| 2nd | $500 |
| 3rd | $300 |

**JBOX bonus pool — $500**, separate from the main pool, for "qualifying agents built with
JBOX": $250 / $150 / $100.

> This entry is **not** built with JBOX and does not claim the bonus pool. It is a Python
> agent with no JBOX dependency, so only the $2,000 main pool and the community awards are
> in scope.

**Community awards — $100 × 4**, on 6 September, 20 September, 4 October and 18 October
2026. Two of the four dates have passed as of 13 September 2026. Public voting "never
changes the technical ranking", and only qualified entries enter the hosted gallery.

**Beyond the prize:** the winning builder gets an opportunity to partner with Håvard
Liltved Dalen (CPO at Fronted, co-founder of JBOX) to launch Signalpost in Norway. This is
described as an opportunity, not a contractual term.

## Qualification is not the same as a good score

This distinction is the single most important thing in the rules, and the published
results page demonstrates it directly:

> "A score of 65 alone does not qualify: coverage, correct company matching and the other
> minimum requirements must also pass."

Qualification is **65/100 plus every hard gate**:

| Gate | Threshold |
|---|---|
| Overall score | ≥ 65/100 |
| Coverage | ≥ 21/35 |
| Weighted external company recall | ≥ 60% |
| External exact-entity precision | ≥ 95% |
| Submitted artifact | ≥ 1,000 completed profiles + exact organisation-number manifest |
| Terminal envelopes | exactly 100 per daily batch |
| Fabricated financial values | none |
| Material wrong-company publication | none |
| Claim provenance | source, retrieval time, reporting period where relevant |
| Missing values | never silently converted to zero |
| Refresh | idempotent on the same snapshot; prior evidence preserved; material changes exposed |
| Setup | reproducible, pinned dependencies, one evaluator command |
| Safety | source rights, server-side secrets and outbound URL policy documented |

## What the published results show

As of the organiser's 13 September 2026 update — five submissions, four assessed, one
under review, scores explicitly provisional and from a single shared 100-company test:

| Entry | Provisional | Organiser's note |
|---|---|---|
| Ajai | 67.46 | Coverage below the minimum needed to qualify |
| Anmol | 69.57 | One website linked to the wrong company; must be corrected to qualify |
| Karthik / AAFA | 49.03 | Coverage and company matching need improvement |
| Meet | unscored | Under review; asked to confirm code version and explain unsupported records |
| Penge | 65.12 | Coverage below the minimum needed to qualify |

**No entry is confirmed qualified and no winner has been declared.**

Two entries scored above 65 and still did not qualify, both on coverage. A third was
blocked by a single wrong-company link. That is the whole competition in one table: the
binding constraints are coverage and precision, not the headline number — and they pull in
opposite directions, because the cheapest way to raise coverage is to publish on weaker
evidence.

This entry resolves that tension by separating the two concerns in code rather than
tuning a single threshold: discovery generates candidates as widely as the budget allows,
and a separate identity gate that never sees discovery's confidence decides what may be
published. Widening discovery cannot, by construction, loosen the publication rule.

## This entry's position

**Official qualification status: `not_determined`.**

Only the organiser's harness can score an entry against the pooled reference collection,
which is versioned, cumulative and not available to entrants. Nothing in this repository
estimates a score, and no local command emits a qualification verdict. What the repository
does report is measured, reproducible facts about its own runs — envelope counts,
availability mix, request counts, wall-clock time, spend — in
[`BUILD_STATUS.md`](BUILD_STATUS.md) and [`EVAL.md`](../EVAL.md).

Two limits are worth stating plainly, because they bound what any local measurement can
mean:

1. **Coverage cannot be self-measured.** Recall is scored against a pooled collection built
   from every entrant's verified discoveries plus Builderr's own crawlers. We can measure
   how many facts we found; we cannot measure what fraction of the pool that is.
2. **Precision can be self-audited but not self-certified.** We can show that the identity
   gate refused every candidate that lacked sufficient evidence, and
   [`LIMITATIONS.md`](../LIMITATIONS.md) records where it is most likely to be wrong.

## Eligibility items an entrant controls

| Requirement | Where it is satisfied |
|---|---|
| ≥ 1,000 completed profiles | `data/profiles/` — count and checksum in `SUBMISSION_README.md` |
| Exact organisation-number manifest | `data/corpus/organisation-numbers.txt` with its SHA-256 |
| Repository URL and exact commit hash | recorded in the submission email draft at freeze time |
| One run command | `uv run main.py -i … -o …` — see [`README.md`](../README.md) |
| Pinned dependencies | `uv.lock`, committed |
| Models, APIs and licences | [`MODELS_AND_APIS.md`](../MODELS_AND_APIS.md) |
| Expected cost per 100-company run | [`COSTS.md`](../COSTS.md), measured not estimated |
| Source rights | [`SOURCE_RIGHTS.md`](../SOURCE_RIGHTS.md) |
| Secrets and URL policy | [`SECURITY.md`](SECURITY.md) |
| Contact for results | in the submission email draft |

## Unresolved by the organiser

Quoted, not interpreted:

> "The documents differ on whether the four-version limit includes the first submission.
> Revisions close 18 October; ask Builderr before submitting a fifth version. Handling of
> sparse information types and failed or missed daily runs also needs clarification. Do not
> infer a new scoring rule from this copy update."

Practical consequence: this entry plans for **one** submission and treats each revision as
scarce. Questions worth asking the organiser before relying on an answer are collected in
[`SUBMISSION_CHECKLIST.md`](SUBMISSION_CHECKLIST.md).
