# Signalpost improvement audit — 14 October 2026

This work continues an **unsubmitted** agent. The leaderboard entry named Hardik is not the owner of this checkout. No score or rank is attributed to the owner. No commit, push, access change, or submission was made.

## Baselines verified

- The starting working tree passes **483 tests** with `TLDEXTRACT_CACHE` directed to a writable local directory. The first attempt had two legacy tests fail solely because the default cache directory was outside the filesystem permission boundary. 104 tests exercise the legacy package rather than the submitted agent.
- `out/dev/v3-1000.jsonl` contains exactly **1,000 companies**, **182 verified websites**, and **47,597 available claims**. Its saved run report records **7,442 / 20,000 requests** and **532.3 seconds**. These historical runtime values are corroborated by the artifact, not remeasured retrospectively.
- Re-auditing that saved output finds **14 duplicate claim keys**. The prior agent's scope-aware key fix is present in the starting code; the historical output predates the fix.
- The NAV build log records **10,390 active ads**, **10,214 with employer numbers**. The later saved refreshed index contains **10,394 ads**, **10,215 with employer numbers**. The historical corpus output publishes **12 NAV ads across eight companies**. The implementation re-fetches a matched ad and checks ACTIVE status and the exact employer or workplace number before publication.
- There is a request cap. The CLI now scales its default at **20 requests/input**, with a 2,000-request minimum, and respects explicit flags/environment overrides. A 1,000-input run gets 20,000, not a fixed 2,000. Direct construction of `RunConfig` still has its dataclass default; the evaluator CLI uses `resolve_limits`.
- A fresh starting-code holdout run produced **14/100 verified websites**, **769 requests**, **159.0 seconds**, **0 integrity defects**, **434 stored bodies rehashed**, and **0 replay changes**.

## Implemented

1. Website discovery also proposes registered former names, workplace names and workplace homepages, plus domains retaining the AS suffix. These proposals do not confer identity authority or change publication thresholds. Candidate and request budgets still apply.
2. Verification can read one linked same-site legal/privacy page in addition to the existing two contact/about pages. Legal notices require an organisation number or non-name corroboration; a foreign affiliate with the same name is insufficient. Contacts of privacy processors are not extracted from legal notices.
3. A redirect to a different registered domain no longer inherits the original registry URL's identity authority. The destination must independently pass verification.
4. Ordinary English “Greystone as …” and “Lion as …” no longer qualify as exact Norwegian AS legal names. Explicit uppercase AS and A/S remain supported. Lowercase legal forms may need other evidence; this is a deliberate precision tradeoff.
5. Synthesis keeps all financial figures in one reporting scope and period, labels group accounts, compares only compatible currencies/units, and names the actual earlier revenue period. Counts cite all their contributing claims. Workplace summaries use the official total and distinguish a truncated detail list.
6. `scripts/signalpost/compare_runs.py` compares aligned company envelopes and records source hashes, field coverage, and gained/lost/changed websites. It does not manufacture a score.

The registry workplace schema was checked against [Brønnøysund's official API documentation](https://data.brreg.no/enhetsregisteret/api/dokumentasjon/no/index.html).

## Final measured website and integrity results

| Matched dataset | Before verified sites | After verified sites | New / withheld |
| --- | ---: | ---: | ---: |
| Development corpus, 1,000 companies | 182 | 181 | 4 / 5 |
| Held-out operational slice, 100 companies | 14 | 14 | 0 / 0 |

This is **not an overall recall increase**. Candidate discovery recovered four sites while precision safeguards withheld five historical assignments. Company coverage with job postings increased from 13 to 14 on the development corpus; business descriptions changed from 166 to 165 and owned social profiles from 97 to 96. These are observed field counts, not correctness scores.

Reviewed recoveries:

| Company | Website | Evidence / mechanism |
| --- | --- | --- |
| MNB AS — 991577092 | https://www.xponella.no/ | Workplace name XPONELLA proposes the domain; the page states 991 577 092. |
| GARANTUM WEALTH MANAGEMENT NORGE AS — 898321592 | https://www.garantum.no/ | Former name GARANTUM NORGE AS proposes the domain; the page states 898321592. |
| ARCTIC GOLF CHALLENGE AS — 934802934 | https://www.arcticgolfchallenge.com/ | Linked privacy page states 934 802 934 and the Oslo address. |
| ATIC AS — 920934927 | https://aticas.no/ | New AS-suffix candidate; page names ATIC A/S and describes building work in Oslo/Akershus. Accepted by the existing exact-legal-name/title/domain rule; no organisation number was found in the inspected pages. This match has weaker evidence than the other three. |

Five historical assignments are now withheld:

- AMALIE AS → amalie.com: the phrase **“Follow AMALIE as we travel the world”** had been treated as the legal name AMALIE AS. It is now only a name mention, insufficient for publication.
- DARRES GATE 2 KS and BJØRNEMYRVEIEN 29 AS → heimstaden.com, HAGMANS NORDIC AB NUF → hagmans.se, and HOTELLDRIFT OSLO SENTRUM AS → strawberry.no: cross-domain redirects no longer inherit registry identity authority. These destinations lacked sufficient current exact-entity corroboration. This does **not** prove the links are wrong; it makes the unresolved relationship explicit.

Provisional legal-page gains for Greystone, Lion, and X-Team were rejected after review found ordinary English “as” phrases. A notice listing SPM Instrument A/S could name a foreign affiliate; the final legal-page guard does not publish from that name alone. The same conservative guard withholds Tavler and Sykt Bra until a non-name discriminator is found. Those provisional results are not counted in the final table.

The final full live run produced 1,100 envelopes in **624.9 seconds**, used **8,537 / 22,000 requests**, and recorded **$0 third-party spend**. The last legal-notice safeguard was then tested by rerunning all five potentially affected accepted sites: **96 requests**, **65.7 seconds**, two retained and three withheld.

The final evaluated dataset is explicitly composed of the full run plus these five replacements; it is **not misrepresented as one execution of the final revision**. Each envelope retains its own run identifier. `composition.json` records exactly what was replaced. The final code change affects only this identified acceptance path.

Final audit of the composed dataset:

- **1,100 unique companies**, exactly one envelope per expected input.
- **0 integrity problems**, including no duplicate claim keys or unresolved evidence references.
- **4,985 distinct stored source bodies rehashed**, no missing bodies or mismatches.
- **62,925 claims replayed; 0 false changes**. Replay is an idempotency check, not a second live refresh measurement.
- **52,373 available claims**. Availability and citation integrity do not establish truth.

The development baseline was collected on the prior run; the holdout baseline was freshly rerun during this session. Live-source changes and timing can affect comparisons. The site review above was performed by the coding agent on saved source pages; it is not independent human-labelled precision testing.

## Controlled synthesis comparison

Both versions rendered the **same 1,000 saved claim sets**, isolating the code change from live-source variability:

| Mechanical check | Before | After |
| --- | ---: | ---: |
| Financial sentences citing mixed company/group scopes | 6 | 0 |
| Activity counts with incomplete claim citations | 21 | 0 |
| Workplace counts with incomplete claim citations | 5 | 0 |
| Hiring counts with incomplete claim citations | 2 | 0 |
| Workplace total understated because the detail list was capped | 3 | 0 |

Checks overlap and should not be summed into a count of companies. This is a mechanical quality check, not independent grading of prose or accuracy. Examples are in `synthesis-comparison.json`.

## Validation and reproducibility

The final test suite passes **506 tests**, including 23 new regression cases. Two pre-existing dependency deprecation warnings remain.

```sh
TLDEXTRACT_CACHE=work/codex-improvement/tldcache .venv/bin/python -m pytest tests/ -q -p no:cacheprovider
.venv/bin/python scripts/signalpost/compare_runs.py BEFORE.jsonl AFTER.jsonl --report comparison.json
.venv/bin/python scripts/signalpost/check_integrity.py AFTER.jsonl --snapshot-root work/codex-improvement --expected work/codex-improvement/all1100.txt --report integrity.json
.venv/bin/python scripts/signalpost/check_idempotency.py AFTER.jsonl --report idempotency.json
```

`implementation.patch` isolates this session's source changes from the starting, already-uncommitted source. `source-hashes.json` records before/after hashes. Detailed local runs and raw bodies are preserved under `work/codex-improvement/`, which is ignored by Git. The starting diff is preserved there as `baseline.patch`.

## Remaining score bottlenecks

The historical corpus has **435/1,000 companies with no resolving candidate** and another 90 whose best candidate is ambiguous. Name guessing cannot establish whether an entity has a separate website, trades under an unrelated brand, or only appears on a group site. Expanding guesses helps some companies; accepting a group homepage without exact-entity support would inflate coverage unsafely.

There is no independent gold-labelled benchmark or organiser evaluator available here. The legacy `evaluate_research_agent.py` and `evaluate_external_footprint.py` scripts do not score the current submission envelopes. Operational website coverage is not recall. The highest-impact next experiment is budgeted search-backed candidate discovery plus a labelled exact-entity audit; no search API credential was configured in the checked local environment files, and no paid service was added.

Official score and qualification remain **not determined**.
