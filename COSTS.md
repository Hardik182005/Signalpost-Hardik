# Costs

## Measured live cost

**$0.00.** That is a measurement, not an estimate or a rounding.

| Field | Value |
| --- | --- |
| Run | `eval100b` |
| Companies | 100 |
| Third-party spend | **$0.00** |
| Command | `uv run main.py --input data/corpus/corpus.jsonl --limit 100 --output out/eval100b.jsonl --run-id eval100b` |
| Started / completed | 2026-09-13T15:12:36Z / 2026-09-13T15:19:05Z |
| Wall clock | 389.0 s of the 2700 s cap |
| Outbound requests | 797 of the 2000 cap (7.97 per company) |
| Evidence file | `out/eval100b-report.json`, key `budget.spend_usd` |

The refresh run over the same 100 companies measured the same figure:

| Field | Value |
| --- | --- |
| Run | `eval100refresh` |
| Third-party spend | **$0.00** |
| Command | `uv run main.py --input data/corpus/corpus.jsonl --limit 100 --output out/eval100-refresh.jsonl --run-id eval100refresh --previous-snapshot out/eval100.jsonl` |
| Completed | 2026-09-13T14:32:55Z |
| Wall clock / requests | 312.7 s / 743 |
| Evidence file | `out/eval100-refresh-report.json`, key `budget.spend_usd` |

## Why it is zero

Not because the accounting is missing. `src/signalpost/budget.py` implements a real spend
ledger with a hard cap (`DEFAULT_MAX_SPEND_USD = 10.0`); `charge()` refuses a charge that
would exceed the cap rather than overrunning it, and the behaviour is tested in
`tests/signalpost/test_budget.py::TestSpend`, including
`test_the_free_path_reports_zero`.

The figure is zero because the two sources a run contacts are both free:

| Source | Charging model | Cost |
| --- | --- | --- |
| Brønnøysundregistrene open-data APIs | Public open data under NLOD 2.0, no account, no metered tier | $0.00 |
| Company-owned websites | Direct HTTP to the origin, no intermediary | $0.00 |

And there is no model provider in the path at all. No tokens are sent anywhere, so there are
no token assumptions to state. See [`MODELS_AND_APIS.md`](MODELS_AND_APIS.md) and the
verification grep it documents.

**Pricing date and source.** Checked 2026-09-14. BRREG's open data terms and price (free,
NLOD 2.0) are recorded at <https://data.norge.no/nlod/en/2.0> and in the endpoint
documentation linked from `docs/norway-sources.md`. There is no vendor price list to attach,
because there is no vendor.

## Cost per 100 companies

**$0.00 measured**, from the `eval100b` run above. This is the figure that matters for the
evaluator envelope, which is 100 inputs with a $10 declared third-party spend cap. The run
used 0% of that cap.

A per-100 projection is therefore trivial rather than modelled: the marginal cost of the
101st company is also $0.00, because every request goes to a free endpoint.

## Cost for the full 1,000-company corpus

**Final figure pending.** The first 1,000-company run completed and measured $0.00, but it has
been **superseded**: an integrity audit found an evidence defect, fixed in commit `1e06568`, and
the corpus is being regenerated on that commit (see
[`LIMITATIONS.md`](LIMITATIONS.md#10-the-1000-company-run-was-superseded-by-a-defect-fix)). The
values below are from the superseded run and are labelled as such; the regenerated run's values
are pending.

```
.venv/Scripts/python.exe main.py --input data/corpus/corpus.jsonl \
    --output out/submission-profiles.jsonl --run-id submission-1000 \
    --concurrency 8 --max-requests 40000 --max-seconds 14400 \
    --max-spend-usd 0 --snapshots out/snapshots-submission
```

| Field | Superseded run (`23a7c21`) | Regenerated run (`1e06568`) |
| --- | --- | --- |
| Run id | `submission-1000` | `submission-1000` |
| Companies | 1,000 (the frozen corpus in `data/corpus/selection.json`) | 1,000 |
| Started / completed | 2026-09-14T09:19:56Z / 2026-09-14T10:02:22Z | pending |
| Third-party spend | **$0.00**, under a cap set to $0.00 | pending |
| Outbound requests | 7,413 of a 40,000 ceiling (7.41 per company) | pending |
| Wall clock | 2,545.3 s of a 14,400 s ceiling | pending |
| Budget denials | none | pending |
| Report file | `out/superseded/submission-profiles-23a7c21-report.json`, key `budget.spend_usd` | **`out/submission-profiles-report.json`**, key `budget.spend_usd` |
| Output | `out/superseded/submission-profiles-23a7c21.jsonl` (39 MB) | `out/submission-profiles.jsonl` |
| Snapshot bodies | `out/superseded/snapshots-submission-23a7c21/` (756 MB on disk) | `out/snapshots-submission/` |
| Progress log | `out/superseded/submission-1000-23a7c21.log` | `out/submission-1000.log` |

**$0.00 in that run is enforced, not only observed.** The run was launched with
`--max-spend-usd 0`, and `budget.charge()` refuses any charge that would take spend above the
cap, so a positive charge would have been refused rather than recorded.

**The ceilings are not the evaluator's.** 40,000 requests and 14,400 s were sized for
generating a 1,000-company corpus; the evaluator's 2,000-request / 2,700 s envelope is scoped to
a 100-company run. This run is not evidence of compliance with that envelope. The per-100
evidence remains `out/eval100b-report.json`, which comes from an earlier code state (see
[`EVAL.md`](EVAL.md#which-code-produced-which-run)).

Requests by purpose in the superseded run (`budget.by_purpose`):

| Purpose | Requests |
| --- | --- |
| `robots` | 1,487 |
| `registry_entity` | 1,000 |
| `registry_roles` | 1,000 |
| `registry_accounts` | 1,000 |
| `registry_subunits` | 1,000 |
| `website_landing` | 1,254 |
| `website_contact` | 171 |
| `website_about` | 134 |
| `website_corroboration` | 124 |
| `website_news` | 92 |
| `website_services` | 84 |
| `website_careers` | 40 |
| `website_team` | 27 |

`data.brreg.no` received 4,016 of the 7,413 requests.

The report filename is not chosen by hand. `runner.py` derives it from the output path:

```python
store_mod.write_json(config.output.with_name(config.output.stem + "-report.json"), report)
```

So an output of `out/submission-profiles.jsonl` produces `out/submission-profiles-report.json`.
There is no `--report` flag. A reviewer looking for the final 1,000-company total should open
that file and read `budget.spend_usd`; until the regenerated run finishes, that file does not
exist and the final total is **unavailable**, not zero-by-assumption. The superseded run's
$0.00 is a measurement for different code, and the structural reason it is zero (two free
sources, no provider) applies unchanged to the regenerated run — but that is an expectation,
not the measurement.

## What a run does consume

Zero dollars is not zero resources. The real budget is time, requests and the reviewer's
machine.

| Resource | Cap in the evaluator envelope | Measured in `eval100b` | Headroom used |
| --- | --- | --- | --- |
| Outbound requests (redirects and retries included) | 2,000 | 797 | 40% |
| Wall clock | 2,700 s (45 min) | 389.0 s | 14% |
| Declared third-party spend | $10.00 | $0.00 | 0% |

Requests by purpose in that run (`budget.by_purpose`):

| Purpose | Requests |
| --- | --- |
| `robots` | 169 |
| `registry_roles` | 110 |
| `registry_accounts` | 110 |
| `registry_entity` | 106 |
| `registry_subunits` | 105 |
| `website_landing` | 128 |
| `website_contact` | 21 |
| `website_news` | 17 |
| `website_about` | 16 |
| `website_services` | 6 |
| `website_corroboration` | 4 |
| `website_team` | 3 |
| `website_careers` | 2 |

Roughly four registry calls plus a `robots.txt` fetch are spent before any crawling; the
remainder is the site crawl. `runner._page_allowance` recomputes the crawl allowance per
company from what is left, so a slow head of the batch cannot starve its tail
(`tests/signalpost/test_budget.py::TestFairShare`).

**Compute.** The run is single-process with a default concurrency of 8 workers. It was
measured on an ordinary laptop, well inside the declared 8 vCPU / 16 GB / 10 GB temp-disk
envelope. The snapshot store is the main disk consumer: `out/eval100b.jsonl` is about 4 MB of
envelopes, and the snapshot directory holds the retrieved bodies behind them. No GPU is used
and none is installed, because no model runs.

## Assumptions, stated plainly

1. BRREG remains free and unmetered. If it ever introduced a paid tier the spend ledger is
   already in place to account for it; the figure would stop being zero.
2. No connector under `scripts/` is enabled. Enabling the Brave Search path would introduce
   the first real per-query cost, and would need its plan and price recorded here before use.
3. The optional `render_llm_context` rephrasing path stays uncalled. Wiring it up would
   introduce token costs, which would then need a provider, a model ID, a price and a date in
   this file.

## Summary

| Question | Answer | Basis |
| --- | --- | --- |
| Measured live cost, 100 companies | $0.00 | `out/eval100b-report.json` |
| Measured live cost, 100-company refresh | $0.00 | `out/eval100-refresh-report.json` |
| Estimated cost per 100 | $0.00 | Measured, not modelled — all sources free |
| Token/call assumptions | None applicable | No model provider in the run path |
| Pricing date and source | 2026-09-14, NLOD 2.0 open-data terms | <https://data.norge.no/nlod/en/2.0> |
| Total corpus cost (1,000), superseded run | $0.00, under a $0.00 cap | `out/superseded/submission-profiles-23a7c21-report.json` |
| Total corpus cost (1,000), regenerated run | **Pending** | Will appear in `out/submission-profiles-report.json` |
