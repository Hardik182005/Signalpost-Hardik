# Build status

Updated: 2026-10-14. Branch `improve-recall-1000` (from `main` at `30e1c4b`).
Remote `https://github.com/Hardik182005/signalpost.git` (private).

This file exists so the build can resume after a context limit. It is not a statement about
competition qualification, which remains `not_determined`.

## Competition facts that changed since September

Read from builderr.ai on 2026-10-13:

- Scoring is now **Recall and coverage 50, Precision/identity/evidence 30, Synthesis 12,
  UX 8**. Qualification is an official run scoring 65/100; the old 21/35, 60% recall and 95%
  precision bars are scored dimensions, not separate gates.
- Builderr supplies the **official batch: 1,000 companies now, possibly 1,100**, with a budget
  it sets. Precomputed profiles and a 1,000-company manifest are **no longer submitted**.
- Submission: email `submit@builderr.ai`, subject "Signalpost Round 1 submission", with agent
  name, repository URL, exact commit hash, 100-company smoke-test result or report, one run
  command, models/APIs/licences, expected cost per official batch, contact. First submission
  is version 1; up to four revised hashes before **18 October**; round closes 21 October.
- Leaderboard of 5 October: 37 submissions, 28 scored, none qualified; best 60.80. This entry
  has **not been submitted**.

## Done on 2026-10-13

| Change | Where | Why |
| --- | --- | --- |
| Budgets scale with batch size (20 requests/input; concurrency 24 above 200 inputs; `SIGNALPOST_*` env overrides) | `runner.resolve_limits` | A fixed 2,000-request default would have failed ~750 of a 1,000-company batch |
| Registry API paced at 0.1 s instead of 0.6 s | `fetch.API_HOST_DELAYS` | 2,410 s of a 2,539 s 1,000-company run was that throttle; now 532 s |
| NAV vacancy register as a jobs source, exact on employer organisation number | `nav_jobs.py`, `data/nav/`, `scripts/signalpost/build_nav_job_index.py` | Only exact-entity jobs source available without a website |
| NAV employer homepage as a website candidate and an `official_listing` evidence group | `discovery.py`, `identity.py` | Recovered e.g. SUVI AS → suvitromso.no |
| Job listing extraction only on careers pages, stricter titles; hiring signal only on explicit statements | `extract/fields.py` | Most September "postings" were buttons, staff lists and blog titles |
| Site addresses validated against the postcode's own post town | `extract/fields.py`, `data_postal_codes.json` | "6016 Powered by Salgsbetingelser" was published as an address |
| Registered activity (`aktivitet`) claim from the registry | `registry.py` | A "what it does" statement for companies with no website |
| Summary rewritten: identity, activity, leadership, scale, finances with calculated ratios and trend, presence, contact, hiring, activity, changes, unknowns | `synthesis.py` | People sentence never fired (wrong keys); no hiring, change or unknowns sentences |
| Output resumed only for the same `--run-id`; any other file set aside | `store.prepare_output` | A daily run to a fixed path would have returned the previous day's envelopes |
| `accounts_audited` key carries scope for group accounts | `registry.py` | 14 duplicate claim keys in the 1,000 run |

Tests: 483 passed (`uv run pytest tests/ -q`).

## Measured

- Submission smoke test, 100 held-out companies: `docs/evidence/smoke100-holdout/`.
- 1,000 corpus companies: `out/dev/v3-1000-report.json` (gitignored), 532 s, 7,442 requests.
- Figures and caveats in `EVAL.md` ("Runs of 2026-10-13").

## Not done — needs the owner

1. **Commit and push** the branch, and decide which commit to submit.
2. **Reviewer access.** The repository is private; Builderr must be able to clone it. Either
   make it public or ask Builderr how to grant access. Not changed.
3. **Send the submission email.** Draft kept outside the repository (`../submission-email-draft.txt`). Not sent.
4. Optional: ask Builderr for a model API key for a search-assisted website discovery path
   (not built; website coverage is the main remaining recall gap).

## Refreshing the NAV cache before a revision

```
uv run python scripts/signalpost/build_nav_job_index.py --refresh --output data/nav/nav-job-index.json.gz
```

Reads the feed forward from the cached cursor and details only new ads (minutes, not hours).

## Codex follow-up — 2026-10-14

Uncommitted improvements and measured evidence are documented in
[`docs/evidence/codex-20261014/README.md`](evidence/codex-20261014/README.md).
The owner has not submitted; the leaderboard row named Hardik is not the owner.

- Expanded candidate discovery from registered names/workplaces and AS suffixes.
- Added bounded legal-page corroboration with exact-entity safeguards; prevented
  cross-domain redirects from inheriting registry authority and English “as” from
  impersonating the Norwegian legal form AS.
- Fixed financial scope mixing, incomplete count citations, and truncated workplace totals.
- Final suite: 506 passed (23 new regression cases); `git diff --check` clean.
- Fresh baseline holdout: 14 websites / 100 companies, 769 requests, 159 seconds.
- Full revised run + five targeted precision rechecks: 1,100 valid envelopes, 0 integrity defects, 4,985 source bodies rehashed, 0 replay changes.
- Development websites: 182 → 181 (four recovered, five withheld); held-out websites: 14 → 14. This is not a measured overall recall increase. See the evidence report for the acceptance tradeoffs.
- No commit, push, access change or submission was performed.
