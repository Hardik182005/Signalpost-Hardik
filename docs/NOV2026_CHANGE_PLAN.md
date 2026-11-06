# November 2026 change plan — feedback revision and JBox website

Prepared 2026-11-06 on branch `feat/signalpost-jbox-feedback-nov2026` of
`Hardik182005/Signalpost-Hardik`. The evaluator path (`main.py` → `runner.py` →
`pipeline.py`), the envelope schema, the financial extraction, the identity gate, the CLI
flags and the local FastAPI dashboard are preserved. Changes are additive.

## Feedback → change → evidence

| # | Feedback | Change | Where | Evidence |
|---|---|---|---|---|
| 1 | Find websites beyond the registry URL and verify the exact company | Discovery already proposes registry, NAV employer homepage, email domain, subunit/former-name and name-guess candidates (DNS pre-filtered); every candidate still passes the identity gate (one decisive signal or two independent evidence groups). No search engine is scraped; no undocumented API is used. | `discovery.py`, `website.py`, `identity.py` | 100-company before/after comparison (`docs/evidence/nov2026/`) |
| 2 | Company-owned social links from the confirmed site | In addition to `<a>` links, schema.org `sameAs` on organisation-typed JSON-LD blocks is read. Share links, personal LinkedIn `/in/` profiles, platform feature paths and agency/theme credits are refused; the handle must name the company or its domain. | `extract/fields.py::extract_social_same_as`, `website.py` | `tests/signalpost/test_nov2026_feedback.py` |
| 3 | Company-attributed news, separate from company activity | New family `news` (field `news_mention`). GDELT DOC 2.0 (documented, keyless) returns *candidates* only; each article is fetched (robots.txt respected) and must name the exact entity (registered name with legal form, or name + organisation number / registered city / verified domain). Different legal forms reject. The company's own domain is excluded (that is `activity`). Rate limit (1 request / 5 s) enforced process-wide; waits are bounded by the run budget; skipped lookups are recorded with a reason. Disable with `SIGNALPOST_NEWS=0`. | `news.py`, `pipeline.py`, `runner.py`, `models.py` | tests + smoke run news counts |
| 4 | Short evidence-backed summary with revenue change and unknowns | `revenue_comparison` compares the latest filed revenue with the *exactly preceding* period at the same scope, currency and unit: absolute and percentage change (no percentage when the base is ≤ 0). Otherwise status `unknown` with the reason — never zero, never a group figure for a company gap. Added to `synthesis` as a sentence (topic `revenue_change`) and a structured field. Unknowns now name missing company activity and missing independent news separately. | `synthesis.py` | tests; envelopes |
| 5 | Public website built with JBox by 7 Nov 2026 23:59 IST | Static versioned dataset exported from a real run (`scripts/signalpost/export_showcase.py` → `showcase/data/v1/`), served from GitHub raw; the JBox project builds the site from `docs/JBOX_PASTE_PROMPT.md`. | `showcase/`, `docs/JBOX_*` | `docs/JBOX_BUILD_PROVENANCE.md` |
| 6 | UI: beacon hero, tokens, tagline, accessible fallbacks | Specified in the JBox prompt (reduced motion, no-WebGL SVG fallback, AA contrast, 360 px layout). | `docs/JBOX_PASTE_PROMPT.md` | browser checks in provenance |

## Guarantees kept

- Search results generate candidates; they are never claim evidence.
- No financial number changed; no gap filled with zero; identity thresholds unchanged;
  candidates are not counted as verified.
- The core agent runs without UI extras, Node, WebGL, JBox or Supabase.
- No secrets in URLs, logs, exports or the ZIP; the export drops query strings that look
  like tracking or credentials, local paths, traces and birth dates.

## Not done / limits

- Wikidata (organisation number P2333 → official website P856) was evaluated and not
  used: `query.wikidata.org/robots.txt` disallows `/sparql` and `www.wikidata.org/robots.txt`
  disallows `/w/` (including `api.php`) for generic agents (checked 2026-11-06).
- GDELT answered HTTP 429 to this machine during the 2026-11-06 runs (after a few
  manual probes); the agent records such lookups as `failed` with the reason and backs
  off for 60 s rather than retrying.
- No paid or keyed search API is used (no key provided). Optional Brave discovery remains
  only in the legacy script.
- GDELT coverage of small Norwegian companies is thin; most companies will truthfully show
  "no independent article found".
- The website shows a snapshot, not live research.
