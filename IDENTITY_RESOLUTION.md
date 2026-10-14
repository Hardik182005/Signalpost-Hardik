# Identity resolution

The rule this whole component exists to enforce: **a fact is published under an organisation
number only when the source has been tied to that exact legal entity.** Finding a plausible
website is not the same as proving it belongs to the company, and the difference is where
wrong-company publication comes from.

Implementation: `src/signalpost/identity.py`. Tests: `tests/signalpost/test_identity.py` and
`tests/signalpost/test_website.py`.

## The key

The 9-digit Norwegian organisation number is the only stable identifier used. Names change,
sites move, addresses change; the number does not.

`valid_org_number()` checks it properly rather than by length:

- MOD-11 with weights `(3, 2, 7, 6, 5, 4, 3, 2)`;
- a check digit of 10 invalidates the number;
- the first digit must be 8 or 9, which is what the register actually issues.

That last rule is what stops a random nine-digit run — a phone number, an invoice reference, a
product code — from being read as an organisation number on a page.
`find_org_numbers()` tolerates the formatting companies actually use (`123 456 785`,
`123.456.785`, `123456785`).

Tests: `test_identity.py::TestOrganisationNumbers`, including
`test_nine_digit_runs_that_are_not_org_numbers_are_ignored`.

## Discovery proposes, the identity gate disposes

These are two separate stages, deliberately.

`discovery.py` generates candidate websites and attaches a **prior** to each — 1.0 for a
registry-listed site, 0.80 for a domain taken from the registered email, 0.60 down to 0.35 for
name-derived `.no` guesses, 0.30 for a single `.com` fallback.

**Those priors are ordering hints only. They never enter the identity score.** A candidate
invented from the company's name arrives at the gate with exactly the same evidential standing
as any other: zero. It gets published only if the page itself proves the tie.

Tested directly: `test_discovery.py::test_priors_are_ordering_only_and_never_publish`.

This matters because the registry's `hjemmeside` field is populated for only about 10.9% of
the eligible universe (the figure is recorded in the `discovery.py` module docstring; the
frozen 1,000-company corpus shows 12.1%, in `data/corpus/selection.json`). Most companies
need a guess. Guessing is cheap; publishing a guess is the failure mode.

## Signals

Each assessed source produces signals. A signal is either **decisive** or **supporting**.

### Decisive signals

| Signal | Meaning | Weight |
| --- | --- | --- |
| `registry_website` | The register itself lists this domain for this organisation number | 1.0 |
| `organisation_number_on_page` | The company's own organisation number appears on the page | 1.0 |
| `organisation_number_on_profile` | The number appears on the social profile | 1.0 |
| `organisation_number_in_text` | The number appears in the mention text | 1.0 |

Any one decisive signal publishes. Both kinds are a statement by an authority or by the entity
itself, tied to the number rather than to a name.

### Supporting signals

| Signal | Group | Weight |
| --- | --- | --- |
| `exact_legal_name` | `registered_name` | 0.45 |
| `former_name` | `registered_name` | 0.35 |
| `distinctive_name_in_domain` | `name` | 0.40 |
| `name_in_text` | `name` | 0.25 |
| `name_in_page_title` | `name` | 0.25 |
| `phone` | `contact` | 0.40 |
| `email_domain` | `contact` | 0.35 |
| `street_address` | `location` | 0.35 |
| `postal_code` | `location` | 0.30 |
| `city` | `location` | 0.15 |
| `profile_links_website`, `linked_from_verified_site`, `links_company_domain` | `website_link` | (profile assessment) |

## The publication rule

From `IdentityResolver._decide()`, in order:

1. **Conflict first.** If the source self-identifies as a *different* legal entity — another
   organisation number on the page, a conflicting legal form — the verdict is `rejected`
   unconditionally. No score can override this. A page that says it belongs to someone else is
   believed.
2. **One decisive signal publishes.** Confidence `min(0.99, 0.90 + 0.03 × signal count)`.
3. **Otherwise, two independent groups and a score of at least 1.0 publish.**
   `PUBLISH_THRESHOLD = 1.0`, and the count is over **groups**, not signal names. Confidence
   `min(0.95, 0.55 + score/4)`.
4. **Score at least 0.45 without two groups is `ambiguous`.** `AMBIGUOUS_THRESHOLD = 0.45`.
   Not published, not discarded — recorded as ambiguous with its reason.
5. **Anything less is `rejected`.**

A verdict maps to an availability state:

| Decision | Availability in the envelope |
| --- | --- |
| `published` | `available` |
| `ambiguous` | `ambiguous` |
| `rejected` | `not_available` |

## Why groups, not signal count

This is the most important line in the module, and it comes from a measured mistake.

An earlier version published **GLØR AS** — a 26-employee company in Mo i Rana — with the
website `glor.no`, justified by "three independent supporting signals": the name in the page
text, the name in the page title, the name in the domain.

Those are not three facts. They are one fact counted three times. The site belongs to
**GLØR IKS**, an inter-municipal company. Along with the wrong website, the wrong company's
phone number, two email addresses and business description were published under GLØR AS's
organisation number.

So the rule became: however many ways a page repeats a company's name, **the name is one
group**, and publication needs a second group that is not the name. The comment recording this
is in `identity.py` above `SIGNAL_GROUPS`.

Tested as a class of failure, not a single case:

- `test_identity.py::TestNameIsOneFact::test_the_name_repeated_in_text_title_and_domain_does_not_publish`
- `test_identity.py::TestWebsiteGate::test_domain_token_alone_does_not_publish`
- `test_identity.py::TestWebsiteGate::test_supporting_signals_must_pair_up`

## Failure examples that shaped the rules

Each of these was observed, then turned into a rule and a test.

**GLØR AS / GLØR IKS.** Name repeated three ways published the wrong company's site.
→ Signal grouping. Test: `TestNameIsOneFact`.

**A name under a different legal form.** A page naming the same stem as `X IKS` when the
subject is `X AS` is a conflict, not a match. `CONFLICTING_FORMS` encodes which forms can and
cannot be the same entity — while `ASA` written as `AS` is *not* treated as a different entity,
because that abbreviation is common and harmless.
→ Tests: `test_a_page_naming_the_stem_under_another_legal_form_is_refused`,
`test_an_asa_written_as_as_is_not_a_different_entity`.

**`facebook.com/AxiomThemes`.** A company site linked to the Facebook page of its *WordPress
theme vendor*. A link from a verified site is not proof that the linked profile is the
company. The rule: a link alone does not publish; the handle must also name the company or the
site's brand.
→ Tests: `test_identity.py::TestProfileGate::test_a_link_from_the_site_alone_does_not_publish`,
`test_website.py::TestSocialProfileEmission::test_theme_vendor_and_opaque_channel_are_not_published`.

**`x.com/VTSvest`.** An opaque handle linked from a site, matching nothing.
→ Same gate. Recorded in the `assess_profile()` docstring.

**A matching handle with no link.** The mirror image: a handle that names the company but is
not linked from the verified site is also not enough.
→ Test: `test_a_matching_handle_without_the_link_does_not_publish`.

**An address on the site's own domain.** A contact address that is really the web agency's or
the hosting platform's is not location evidence for the company.
→ Test: `test_an_address_on_the_site_own_domain_is_not_evidence`.

**Case mismatch between register and site.** The register publishes names in upper case
(`SCHRØDER`), websites write them mixed case (`Schrøder`). Transliteration has to run *after*
casefolding, or `Ø` survives one path and not the other and the company's real website is
rejected. The comment above `TRANSLITERATE` records this.
→ Tests: `TestNormalisation::test_case_does_not_change_the_result`,
`TestWebsiteGate::test_uppercase_registry_name_matches_mixed_case_page`.

**Generic names.** A company called something like "Norsk Service AS" has no distinctive token
to guess a domain from. `GENERIC_TOKENS` strips those, and a wholly generic name yields no
single-token guess at all.
→ Test: `test_discovery.py::TestPrecisionGuards::test_a_wholly_generic_name_yields_no_single_token_guess`.

## Ambiguity between candidates

`choose_best()` picks the strongest verdict among candidates, but if the top two are within
**0.02 confidence** of each other it returns `ambiguous` rather than picking a winner. Two
nearly equally plausible sites means the evidence does not distinguish them, and a coin flip
between them is exactly the wrong-company risk the gate exists to prevent.

## Corroboration: a second page can settle it

An `ambiguous` landing page is not the end. `website._corroborate()` fetches up to
**2 more pages** (`CORROBORATION_PAGES`), restricted to `contact` and `about`
(`CORROBORATION_KINDS`) — the pages where a Norwegian company states its organisation number.

Three outcomes, all tested in `test_website.py::TestCorroboration`:

- the organisation number appears on the contact page → publishes
  (`test_the_organisation_number_on_the_contact_page_publishes`);
- the page adds nothing → stays ambiguous
  (`test_a_contact_page_without_new_evidence_leaves_it_ambiguous`);
- the page names another company → refusal
  (`test_a_contact_page_naming_another_company_is_a_refusal`).

Only those two page kinds are read; a news or services page is not worth the request
(`test_only_contact_and_about_pages_are_read`).

## Relationships between entities

Relationships come from the register, not from inference. Signalpost does not guess that two
companies are related because they share a name, an address or a website.

| Relationship | Source | Endpoint |
| --- | --- | --- |
| Group structure (parent, subsidiaries) | Official group register | `konsernstruktur/{org}` |
| Registered establishments (subunits) | Official subunit register | `underenheter?overordnetEnhet={org}` |
| A number that is itself a subunit | Subentity lookup | `underenheter/{org}` |

Subunits carry their own addresses, industry codes and reported employee counts, and are
recorded as such — a subunit's head count is not silently added to the parent's. When the
group endpoint returns nothing, the relationship is `not_available`; absence of a returned
group record is not evidence of independence.

Consolidated accounts are marked: when the register reports `regnskapstype == "KONSERN"`, the
financial claim is scoped `group` rather than to the single entity. Mixing a group revenue into
a company-level field would be a wrong-entity error of the same family as publishing the wrong
website.

## What the gate does not do

- It does not verify that a company is *trading*, only that a source belongs to it.
- It has **no independent human-reviewed accuracy measurement**. Every figure in
  [`EVAL.md`](EVAL.md) about identity is either a fixture check or a local proxy over our own
  reference set. No external reviewer has adjudicated the published links.
- It cannot detect a company that deliberately publishes another entity's organisation number.
- Abstention is the designed behaviour for uncertainty, and it is costly: in the `eval100b`
  run, 22 of 100 companies had a website published. The other 78 include genuine no-site cases
  and cases where the evidence existed but did not meet the bar. That trade is discussed in
  [`LIMITATIONS.md`](LIMITATIONS.md).
