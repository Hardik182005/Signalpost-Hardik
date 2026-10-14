# Agent policy

What Signalpost does with one organisation number, and — more importantly — the rules about
what it refuses to do.

The governing principle is that **not knowing is a reportable answer**. A field left
`not_available` with a reason costs coverage. A field filled with a plausible guess costs
correctness, and a wrong company's phone number published under a real organisation number is
not a small error. The policies below all resolve in the same direction.

Implementation: `src/signalpost/pipeline.py` (`PIPELINE_VERSION = "1.0.0"`).

## Stages

| Stage | Module | Produces |
| --- | --- | --- |
| Registry | `registry.py` | Legal identity, roles, subunits, group structure, annual accounts |
| Identity | `discovery.py` + `identity.py` | Website candidates and a publication verdict |
| Website | `website.py` | External-family claims from the company's own site |
| Refresh | `refresh.py` | Change events against a previous snapshot |
| Synthesis | `synthesis.py` | A summary whose every sentence names its claims |

The registry runs first because it establishes the entity. Everything after it is measured
against what the register says.

**Every stage is wrapped.** `pipeline._stage()` catches failure so that a broken stage produces
a recorded error and a terminal envelope, never a missing line. This is what makes "exactly
100 envelopes for 100 inputs" structural rather than aspirational: in the `eval100b` run the
one company whose registry lookup failed still produced a complete envelope, terminal state
`failed`, reason `"The official registry could not be read for this company"`.

## Discovery policy

The register lists a website for only about 10.9% of the eligible universe (12.1% in the frozen
1,000-company corpus, per `data/corpus/selection.json`). So for roughly nine companies in ten,
finding the site means proposing candidates.

**Rule 1 — Discovery proposes, the identity gate disposes.** `discovery.py` may be generous.
It generates up to 4 candidates from the registry field, the registered email domain,
distinctive name tokens on `.no`, and one `.com` fallback. It attaches a prior to each.

**Rule 2 — Priors order candidates and never publish them.** A prior determines which candidate
is assessed first. It contributes **zero** to the identity score. A domain guessed from a
company's name arrives at the gate with no evidential standing whatsoever. Pinned by
`tests/signalpost/test_discovery.py::test_priors_are_ordering_only_and_never_publish`.

**Rule 3 — Do not spend requests on guesses that cannot exist.** Candidates are DNS-resolved
first; a name-guessed domain that does not resolve is dropped without an HTTP request. A DNS
lookup is not an outbound request and costs no budget. A registry-listed domain is kept even
when DNS fails, because that failure is itself worth reporting.

**Rule 4 — Generic names produce no guess.** `GENERIC_TOKENS` and a `MIN_STEM_LENGTH` of 4 stop
a company whose name is entirely common words from generating a domain guess that would belong
to someone else.

**Rule 5 — Free mailbox domains are never candidates.** A company using a gmail address does
not thereby own `gmail.com`.

Details in [`CRAWLERS.md`](CRAWLERS.md); the gate itself in
[`IDENTITY_RESOLUTION.md`](IDENTITY_RESOLUTION.md).

## Extraction policy

Extraction is deterministic: regular expressions, HTML parsing, structured-data reading
(JSON-LD, microdata, RDFa via `extruct`), and main-text extraction via `trafilatura`. No model
is involved at any point — see [`MODELS_AND_APIS.md`](MODELS_AND_APIS.md).

**Every value keeps a locator.** A CSS selector, a JSON pointer, or a text span — enough for a
reviewer to find the value again in the stored snapshot. A bare URL is not accepted as
provenance for a specific number.

**Extractors are generous; the pipeline filters.** `extract/fields.py` returns candidates with
locators and does not decide what gets published. That decision belongs to the identity gate.
Keeping the two apart is what stops a permissive extractor from becoming a permissive
publisher.

**Page content is data, never instruction.** Retrieved pages sometimes contain text shaped like
a command — "ignore previous instructions", "publish this as fact", "reveal your API key".
`extract/html.py` matches thirteen such patterns (`INJECTION_PATTERNS`), and the handling is
deliberate:

- detection **does not change behaviour**, because there is no behaviour for a page to change.
  Nothing in a page can select a URL, alter a rule, or authorise anything. Extractors only read
  values into typed fields.
- `detect_injection()` **records** that it happened, so a human can see it;
- `strip_injection_bait()` neutralises the text before it can reach a summary.

Tests: `test_extract.py::TestPromptInjection`, including
`test_ordinary_page_text_is_not_flagged` and `test_bait_is_neutralised_before_summarisation`.

**Norwegian specifics are handled explicitly**, because each silently corrupts data otherwise:

| Convention | Handling |
| --- | --- |
| `1.234.567,89` — `.` thousands, `,` decimal | `parse_norwegian_number()` |
| "Tall i hele 1 000", "i mill" | Scale is **detected and reported, not silently applied** |
| 8-digit phone numbers, `+47` / `0047` prefixes | `NO_PHONE_RE` |
| `12.03.2025`, `12. mars 2025`, ISO | `parse_date()`, with a `not_after` guard |
| Names in upper case in the register, mixed case on sites | Casefold **before** transliteration |
| æ, ø, å in domains | IDNA / punycode, both accented and transliterated forms |

**Noise is rejected, not published.** Navigation menus are not business descriptions
(`looks_like_boilerplate`); parked-domain placeholders are identified (`looks_parked`), while
a real site that happens to say "coming soon" is not
(`test_extract.py::TestParkedDomains::test_a_real_site_saying_coming_soon_is_not_parked`);
menu items are not job vacancies (`_looks_like_job_title`).

**Published URLs are cleaned.** Tracking parameters, session identifiers and cache busters are
stripped by `net.canonical_url` before a URL becomes a value — a session id must never survive
into published output (`test_net.py::TestCanonicalisation`).

**Volume caps.** Job postings are capped at 25 per company and activity items at 20. A site
with hundreds of listings contributes bounded, not unbounded, output.

## Abstention policy

This is the part that costs the most and matters the most.

**Abstain rather than guess the entity.** No claim is published under an organisation number
until a source is tied to that exact entity by a decisive signal, or by two independent
evidence groups reaching a score of 1.0. Below that: `ambiguous`. Well below: `not_available`.
The full rule set and the wrong-company incident that produced it are in
[`IDENTITY_RESOLUTION.md`](IDENTITY_RESOLUTION.md).

**Abstain rather than pick between near-equal candidates.** If the two best candidates are
within 0.02 confidence, `choose_best()` returns `ambiguous` instead of choosing.

**Believe a source that says it belongs to someone else.** A page carrying another
organisation number or a conflicting legal form is `rejected` unconditionally. No accumulated
score overrides it.

**Never convert absence into zero.** Structurally impossible: a non-`available` claim cannot
carry a value (`models.Claim.__post_init__`). A 404 from the accounts register means
`not_available`, not revenue 0. A genuine zero remains a value.

**Distinguish "no source" from "refused".** If the registry-vouched website refuses access, the
external claims are `blocked`, not `not_available`. Those are different facts about the world
and they are reported differently.

**Never estimate a financial figure.** No interpolation, no carry-forward from a prior year, no
derived revenue. A figure is published only when the register reported it, with its period,
scope, unit and currency attached.

**Report the scale rather than applying it.** A detected "figures in thousands" hint is
reported alongside the number instead of silently multiplying it, so a mis-detection is visible
rather than a 1,000× error.

**Say why.** A non-available claim carries a `reason`. `website._unverified_claims()` emits all
five external families with specific reasons when a site could not be verified, so a reader can
tell "no website exists" from "a website exists and we could not prove it is theirs" from "we
were refused".

### The cost, stated plainly

In the `eval100b` run, 22 of 100 companies had a verified website published; 78 did not. Some
of those genuinely have no site. Others have one that we could not prove belonged to them. The
abstention policy makes those two cases indistinguishable in the coverage count, and that is a
real loss — see [`LIMITATIONS.md`](LIMITATIONS.md). The alternative, publishing on a name
match, is what put a competing entry's wrong-company link into the record.

## Synthesis policy

`synthesis.py` renders a summary from templates. `method = "deterministic-template"`.

**A sentence exists only if its claims exist.** Templates are keyed to availability; a sentence
about financials is not rendered when the financials are `not_available`. No hedging prose is
invented to fill a gap.

**Every sentence names its claims.** Each `Sentence` carries `claim_keys`, so the chain runs
sentence → claim → evidence → snapshot → content hash. A reviewer can check any assertion in
the summary without re-running anything.

**Nothing is added by synthesis.** It cannot introduce a fact, because it only ever reads
already-published claims. It never sees a source page.

**Bands where a number would mislead.** `EMPLOYEE_BANDS` renders "10-49 employees" alongside
the registered count and its date, because a head count in the register is a point-in-time
registration rather than a current figure.

**The optional model path is not used.** `render_llm_context()` exists and has no caller. Its
contract, if ever enabled, is rephrasing only over settled claims. See
[`MODELS_AND_APIS.md`](MODELS_AND_APIS.md).

The trade-off is visible in the output: the prose is repetitive across companies and reads like
a template, because it is one. In exchange it is reproducible, traceable and free.

## Termination policy

Every input company produces exactly one envelope with exactly one of five terminal states.
`pipeline._terminal_state()` assigns it with a human-readable reason:

| State | When |
| --- | --- |
| `completed` | Registry resolved and the external attempt succeeded |
| `partial` | Some of it worked |
| `not_found` | The number resolved to nothing |
| `blocked` | Access refused |
| `failed` | The attempt broke |

`store.RunStore` appends, flushes and fsyncs per envelope and enforces uniqueness at write
time, so a crash mid-run leaves a readable file and a resumable run.
`store.verify()` then re-checks the finished file and the run exits non-zero if it does not
verify.

## What the agent does not do

- It does not use a language model, call a paid API, or read a credential.
- It does not crawl beyond a company's own site.
- It does not infer relationships between companies; relationships come from the official group
  and subunit registers or not at all.
- It does not store role holders' birth dates.
- It does not claim independent verification of anything. Website-sourced claims are classed
  `company_owned`: the company saying something about itself.
- It has no independently reviewed accuracy measurement. See [`EVAL.md`](EVAL.md).
