| Measure | Before (13 Oct 2026, `smoke100-holdout`) | After (this revision) |
|---|---|---|
| Companies | 100 | 100 |
| Terminal states | partial: 86<br>completed: 14 | partial: 86<br>completed: 14 |
| Verified company websites | 14 | 14 |
| Website abstention reasons | no candidate domain resolved in DNS: 40<br>candidates failed the identity gate: 31<br>match suggested but evidence insufficient: 15 | no candidate domain resolved in DNS: 39<br>candidates failed the identity gate: 32<br>match suggested but evidence insufficient: 15 |
| Companies with a company-owned social profile | 10 | 10 |
| Companies with an independent news article | 0 | 0 |
| News outcome per company | not searched (no news family in this version): 100 | search failed: HTTP 429 (rate limited): 76<br>searched, no independent article found: 11<br>search failed: timeout: 8<br>hits found, none confirmed to name the company: 3<br>skipped: name too generic for exact search: 2 |
| Revenue comparison | not produced (no comparison in this version): 100 | compared: 67<br>unknown: 33 |
| Available claims | 4761 | 4760 |
| …whose evidence references all resolve | 4761 | 4760 |
| Requests | 766 | 918 |
| Third-party cost (USD) | 0.0 | 0.0 |
| Seconds per company, median | 5.7 | 75.81 |
| Seconds per company, p90 | 13.12 | 115.97 |
| Time spent in news lookups (sum over companies, s) | 0.0 | 6492.8 |

Website changes on matched companies: gained 0, lost 0, changed 0.

## How to read this

- **Before:** submission smoke test of 13 Oct 2026 (`docs/evidence/smoke100-holdout/`).
  **After:** run `nov2026-final` on 6 Nov 2026 (10:08–10:25 UTC) at commit `772f48d`. Both runs used
  the same 100 held-out organisation numbers (`data/holdout/organisation-numbers.txt`) and
  the default budgets.
- These are operational counts, not recall or precision. Live websites, filings and GDELT's
  availability changed between the two dates.
- **Requests** are summed from each envelope's `operations.requests_used`. Shared requests
  (the NAV feed refresh and token) are not attributed to a company. The run totals from the
  reports are 770 (before) and **967 of 2,000** (after).
- **Time per company rose from about 6 s to about 76 s because of the news step.** The news step is paced
  at 1 request per 5 s across the run, and lookups wait for a slot within the run budget.
  The whole run still finished in 1,047 s of the 2,700 s cap. `SIGNALPOST_NEWS=0` restores the
  earlier timing.
- **News:** GDELT returned HTTP 429 to 76 of 100 lookups, and 8 more timed out. Those are recorded
  as `failed`, not as "no coverage". Of the lookups that completed, none produced an article
  confirmed to name the exact company. Zero news articles is therefore a statement about
  this run, not about the companies.
- **Websites and socials did not change.** This revision added sameAs reading and news. It did not
  loosen the identity gate, so no new website was published. The one-company shift between
  abstention reasons is a live DNS/page difference.
- **Revenue comparison is new:** 67 companies have a latest-vs-previous comparison (absolute
  and %, at the same scope and currency). 33 are `unknown`, each with its reason, never 0.
- Available claims went from 4,761 to 4,760, and every available claim resolves to its evidence in
  both runs (`integrity-report.json`).
