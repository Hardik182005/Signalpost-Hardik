# Models and APIs

## The short answer

**The submitted run path uses no language model, no model provider, and no API key that
belongs to anyone.**

Synthesis is deterministic template rendering over already-published claims
(`src/signalpost/synthesis.py`, `method = "deterministic-template"`). Extraction is regular
expressions, HTML parsing and structured-data reading. Identity resolution is a weighted
rule set with explicit thresholds. Nothing in a run sends text to a model provider.

Verify it:

```
grep -rn "openai\|anthropic" src/signalpost/
```

This returns nothing. The run reads a small set of optional environment variables (below),
none of which is required and none of which is a personal credential.

## Network APIs used in the submitted run path

| Provider | Endpoints | Auth | Cost | Status |
| --- | --- | --- | --- | --- |
| Brønnøysundregistrene (`data.brreg.no`) | Entity, subentity, roles, subunits, group structure, annual accounts | None — open data | Free, NLOD 2.0 | **Enabled** |
| NAV vacancy feed (`pam-stilling-feed.nav.no`) | `/api/publicToken`, `/api/v1/feed/{page}`, `/api/v1/feedentry/{ad}` | NAV's published public bearer token, fetched at run time | Free | **Enabled** |
| Company websites | The company's own pages, `robots.txt` first | None | Free | **Enabled** |

The NAV feed is NAV's open job-vacancy API ([documentation](https://navikt.github.io/pam-stilling-feed/),
[terms](https://arbeidsplassen.nav.no/vilkar-api)). Its token is the public one NAV publishes
for open use at `/api/publicToken`; it is fetched by the run, not configured, so the run is
reproducible by anyone. `SIGNALPOST_NAV_FEED_TOKEN` may override it with a privately
registered token. How the source is used is in [`CRAWLERS.md`](CRAWLERS.md#nav-vacancies).

## Model providers

| Provider | Model ID | Configured | Env var | Status |
| --- | --- | --- | --- | --- |
| OpenAI | — | No | — | **Not integrated.** No client, no dependency, no code path. |
| Anthropic | — | No | — | **Not integrated.** Same. |
| Any hosted LLM | — | No | — | **Not integrated.** |
| Hugging Face — `NOSIBLE/financial-sentiment-v1.2-base` | Apache-2.0 | No | — | **Disabled.** Reached only by `scripts/run_sentiment_model.py`, behind the optional `sentiment` extra. |
| Hugging Face — `ltg/norbert3-base_sentence-sentiment` | CC BY 4.0 | No | — | **Disabled.** Same. Its remote model code would need inspection before any use. |

`pyproject.toml` has no model-provider dependency in its required set. `transformers`,
`torch` and `accelerate` appear only under the optional `sentiment` extra, which the submitted
run does not install or import.

## The one optional LLM hook, and what it does not do

`src/signalpost/synthesis.py` defines `render_llm_context()`. It is **not called anywhere in
the run path**. It exists so that an operator who later wants a paid rephrasing pass can hand
a model the already-decided claim set. Its contract, if it were ever wired up:

- it receives claims that have **already passed the identity gate and already carry evidence**;
- it may only rephrase; every sentence keeps its `claim_keys`;
- it cannot introduce a fact, because it is never given a source page, only resolved claims.

The reason for that shape is the scoring and honesty requirement that every published sentence
trace back to evidence. A model that could read pages could invent a fact; a model that can
only see settled claims cannot. Today the distinction is theoretical — the function has no
caller.

Confirm with:

```
grep -rn "render_llm_context" src/ main.py
```

The only hit is the definition itself.

## Synthesis without a model

`synthesis.py` builds each profile summary from templates keyed to what is actually available:

- a `Sentence` is emitted only when the claims it names are `available`;
- every `Sentence` carries the `claim_keys` it was rendered from, so a reviewer can click
  from a sentence to the claim to the evidence to the snapshot;
- `LEGAL_FORMS` maps a registry form code to readable English;
- derived figures (operating margin, equity ratio, revenue change) are computed only from
  filed figures of the same period, say that they are calculated, and cite every input claim;
- the method string on the synthesis block is `"deterministic-template"`, so the output
  states its own provenance.

The trade-off is honest to record: the prose is repetitive across companies, and it will not
read as well as model-written prose. In exchange it is reproducible byte-for-byte, it cannot
hallucinate, and it costs nothing.

## Environment variables

Every variable is optional. None is a secret; the run works with none of them set.

| Variable | Used by | Effect |
| --- | --- | --- |
| `SIGNALPOST_MAX_REQUESTS` | `runner.py` | Outbound request cap, when no `--max-requests` flag is given (default: 20 per input) |
| `SIGNALPOST_MAX_SECONDS` | `runner.py` | Wall-clock cap (default: 2,700) |
| `SIGNALPOST_MAX_SPEND_USD` | `runner.py` | Third-party spend cap (default: 10; actual spend is 0) |
| `SIGNALPOST_CONCURRENCY` | `runner.py` | Companies in flight (default: 8, or 24 above 200 inputs) |
| `SIGNALPOST_NAV_FEED_TOKEN` | `nav_jobs.py` | Use a registered NAV feed token instead of NAV's public one |
| `BRAVE_SEARCH_API_KEY` | `scripts/run_brave_discovery.py` | **Not in the run path.** The script is not invoked by `main.py` or `runner.py`. |

A command-line flag always wins over the environment, and the environment over the default.

If a credential were ever introduced, the handling rules are in
[`docs/SECURITY.md`](docs/SECURITY.md): secrets never enter a URL, never enter a snapshot,
and are redacted from stored request metadata (`net.SENSITIVE_QUERY_KEYS`,
`net.SENSITIVE_HEADERS`, tested in `tests/signalpost/test_net.py::TestRedaction`).

## Licences

| Component | Licence | Note |
| --- | --- | --- |
| BRREG open data | NLOD 2.0 | Attribution required; see [`SOURCE_RIGHTS.md`](SOURCE_RIGHTS.md) |
| NAV vacancy feed | NAV API terms of use | Free; inactive ads must be dropped promptly; see [`SOURCE_RIGHTS.md`](SOURCE_RIGHTS.md) |
| beautifulsoup4, lxml, trafilatura, extruct, pypdf, pydantic, tldextract | Their own OSS licences | Required dependencies, declared in `pyproject.toml` |
| scrapy, scrapy-playwright | Optional `crawler` extra | Not installed for the submitted run |
| transformers, torch, accelerate | Optional `sentiment` extra | Not installed for the submitted run |
| `NOSIBLE/financial-sentiment-v1.2-base` | Apache-2.0 | Disabled |
| `ltg/norbert3-base_sentence-sentiment` | CC BY 4.0 | Disabled |
| NoReC / `ltg/norec_sentence` | CC BY-NC 4.0 | **Not used.** Non-commercial terms make it a research reference only. |

## What this costs a reviewer

Nothing to configure. `uv sync` installs the required dependency set from `uv.lock`; the run
command works with no credentials. See [`README.md`](README.md) for the exact commands and
[`COSTS.md`](COSTS.md) for the measured spend.
