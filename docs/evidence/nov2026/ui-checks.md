# Website browser checks — 6 Nov 2026

Site: **https://9-copy-ready-jbox-prompt-create-docs-jbo.sites.jboxai.com/** (JBox hosted,
published after the fix that produced preview revision `ea6fc9ad2c7934cc`). Dataset: `showcase/data/v1` at commit `07255e2`
(run `nov2026-prelim`).

Method: Chrome on Windows 11, driven by Claude Code through the Claude-in-Chrome extension.
Pages were loaded in same-origin iframes at 360 px (phone) and 1280/1440 px (desktop), and
the DOM was inspected with scripts. Screenshots timed out in this environment, so the results
below come from DOM state, not images. Signed-out HTTP checks used `curl` with no cookies.

| Check | Width | Result |
|---|---|---|
| HTTPS and every route, signed out: `/`, `/companies`, `/compare`, `/compare?a=917457778&b=911873621`, `/method`, `/company/917457778`, `/company/911873621` | — | 200. The company pages are server-rendered, so a deep-link reload shows the name, organisation number and status |
| Unknown company `/company/000000000` | — | 200 with a "not found" view. Unknown route `/no-such-page` returns 404 |
| Published HTML scanned for `supabase`, `localhost`, Windows paths or secret patterns | — | none found |
| Search → profile: type "prikken", ArrowDown, submit | 1280 | 1 option (`role=option`, `aria-activedescendant` set). Submitting goes to `/company/917457778` |
| No results ("zzzz…") | 1280 | "No companies found in this snapshot." with `aria-expanded=true` |
| Profile → evidence: "View source evidence for revenue" | 1280 | Modal `<dialog>` opens with focus inside it. It shows the value 1,265,324 NOK, period 2025, scope company, source URL `data.brreg.no/regnskapsregisteret/regnskap/917457778`, retrieval time, JSON-pointer locator and the method `brreg_json`. The Close button closes it |
| Source link opened signed out | — | `data.brreg.no/…/917457778` returns 200 |
| Compare `/compare?a=917457778&b=911873621` | 1280 | Side-by-side rows (status, city, industry, employees, …), each linking to its evidence. The note "Different reporting periods are not like-for-like" is shown |
| Missing data `/company/989186043` (no website, no revenue) | 1280 | "No company website could be verified". The website is shown as "Not established" with the reason (no candidate domain resolved in DNS). No `0` revenue appears |
| Source-error state (news search failed with HTTP 429) | 1280 | Shown as "Independent news · Not established · news search failed: HTTP 429 · Failed", and listed under "What we could not establish". It is not presented as a fact |
| Back navigation: profile → /companies (client navigation), then `history.back()` | top-level | Returns to `/company/917457778` with the profile rendered |
| No horizontal overflow on phone | 360 | scrollWidth equals clientWidth (345/345) on `/`, `/companies`, `/compare`, `/method`, `/company/917457778`, `/company/989186043` |
| Phone menu | 360 | `.mobile-nav-toggle` ("Open navigation menu") opens a full-width stacked panel. A link navigates and closes it, and Escape closes it |
| Desktop | 1440 | No overflow, and the menu toggle is hidden |
| WebGL failure: `getContext` forced to return null, then the hero remounted | 1280 | No canvas. A static SVG beacon with alt text is shown and no error is thrown |
| Reduced motion: `prefers-reduced-motion: reduce` forced | 1280 | No canvas and 0 `requestAnimationFrame` calls in 3 s (vs. 1 canvas and 187 calls normally) |

Known gaps (recorded, not fixed):

- After the evidence dialog closes, focus does not go back to the button that opened it.
  Keyboard users land at the top of the document. This is a minor accessibility issue.
- Escape on the native `<dialog>` could not be tested, because synthetic key events are not trusted.
  The browser's built-in `cancel` behaviour applies.
- The site shows the preliminary-run snapshot (`nov2026-prelim`), not the final run. The
  live research numbers are in `docs/evidence/nov2026/BEFORE_AFTER.md`.
