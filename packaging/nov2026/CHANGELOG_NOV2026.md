# Changelog — November 2026 revision

This revision responds to the 1 November 2026 feedback. Commits on
`feat/signalpost-jbox-feedback-nov2026` after the 13 Oct submission:

| Commit | Change |
|---|---|
| `b228870` | Smoke-test and before/after evaluation evidence (baseline) |
| `59ae8bd` | Revenue comparison, independent `news` family, schema.org `sameAs` social profiles, showcase export |
| `07255e2` | News: back off the shared GDELT gate after HTTP 429; change plan and JBox prompt |
| `e22c7cd` | 100-company website snapshot (`showcase/data/v1`, run `nov2026-prelim`) |
| `772f48d` | Revenue change falls back to comparing group years when company years cannot be compared; refresh replay script |
| later | Final evidence, before/after comparison, website provenance and checks, packaging (documentation and scripts only, no research-code change) |

## Shipped

- **Revenue change in the summary.** The latest filed revenue is compared with the exactly preceding
  period at the same scope, currency and unit, giving the absolute and percentage change. When
  that is not possible the status is `unknown` with a reason. It is never 0, and a group figure
  never fills a company gap. Result: 67 of 100 compared and 33 unknown.
- **Independent news, kept separate from company activity.** GDELT DOC 2.0 candidates are
  confirmed on the article page and must name the exact company. The company's own domain is excluded.
  Failures are recorded with their reason. Result in this run: 0 published, because GDELT
  rate-limited most lookups (76 HTTP 429, 8 timeouts).
- **Social profiles from schema.org `sameAs`** on organisation-typed JSON-LD, in addition to
  links. Share links, personal profiles and agency credits are refused. Result: 10 companies and
  21 profiles, the same as before on this sample.
- **Public website built with JBox:**
  https://9-copy-ready-jbox-prompt-create-docs-jbo.sites.jboxai.com/. It covers search, profile,
  evidence inspection, comparison and method, with a 3D beacon hero, reduced-motion and no-WebGL
  fallbacks, and the requested theme. It shows a labelled snapshot.

## Not changed

- Website discovery and the identity gate are unchanged. Discovery already used registry,
  NAV, email-domain, subunit and name-guess candidates. No search engine or undocumented API
  was added, and thresholds were not lowered. Verified websites stayed at 14 of 100.
- Registry research, multi-year accounts, evidence model, refresh behaviour, CLI and
  envelope schema are preserved. The schema gained a `news` family and the
  `synthesis.revenue_comparison` field.
