# Security

The agent fetches URLs derived from untrusted pages and from domains it guessed. That
makes it, structurally, a server-side request forgery engine pointed at whatever a remote
page suggests. This document states what stops that, where the code is, and what is not
defended.

Hard gate this satisfies: *"Source rights, secrets and outbound URL policy are documented
and safe."*

## One egress point

Every byte the agent retrieves passes through `Fetcher.get()` in
[`src/signalpost/fetch.py`](../src/signalpost/fetch.py). Nothing else opens a socket. That
is the point: there is exactly one function to audit, and a reviewer can confirm it by
grepping for `http.client`, `urlopen` or `requests` and finding only that module.

## URL policy

Implemented in [`src/signalpost/net.py`](../src/signalpost/net.py), enforced before every
hop, and pinned by `tests/signalpost/test_net.py` (50 tests).

**Refused before any DNS lookup:**

| Rule | Rejects |
|---|---|
| Scheme allowlist | `file:`, `ftp:`, `gopher:`, `javascript:`, `data:`, `jar:`, everything but `http`/`https` |
| No embedded credentials | `https://user:pass@host/`, `https://admin@169.254.169.254/` |
| Port allowlist (80, 443) | `:22`, `:8080`, and `:0` — which an `or`-based default would have silently rewritten to 443 |
| Hostname blocklist | `localhost`, `metadata`, `metadata.google.internal`, `.local`, `.internal` |
| No whitespace or control characters | header-injection shapes like `https://example.no/\nHost: x` |

**Refused after resolution:** every address returned for the host must be publicly
routable. Loopback, private, link-local, unique-local, CGNAT (`100.64.0.0/10`),
unspecified and reserved ranges are all rejected, for IPv4 and IPv6.

Three wrappings are unwrapped before the check, because each is a way to write an internal
address that looks external to a naive parser:

- IPv4-mapped IPv6 (`::ffff:127.0.0.1`, `::ffff:169.254.169.254`)
- 6to4 (`2002:7f00:0001::` encodes `127.0.0.1`)
- IPv4-compatible IPv6

**Any private answer rejects the whole host**, rather than filtering to the public ones. A
split-horizon DNS record that returns one public and one internal address is an attempt to
steer the agent, not a host with a mixed deployment.

## DNS rebinding

Validating a hostname and then connecting by hostname leaves a window in which the record
can change between the two. `Fetcher.get()` closes it: the connection is made to an IP
address that was validated moments earlier and is pinned for that connection, while TLS
SNI and certificate verification still use the real hostname. Certificate validation is
not weakened to achieve this.

## Redirects

Followed manually, one hop at a time, to a maximum of four. Each hop is a new URL chosen
by a remote server, so each is fully revalidated through the rules above — a public page
cannot redirect the agent to `169.254.169.254`. `urllib`'s automatic redirect handling
would bypass this, which is why it is not used.

Every hop also reserves request budget before it is issued, because the evaluator counts
redirects and retries.

## Retrieved pages are data, never instructions

A company website is an untrusted document. Text inside it cannot change system rules,
invoke tools, select URLs to fetch, expose secrets or authorise anything.

`detect_injection()` in [`src/signalpost/extract/html.py`](../src/signalpost/extract/html.py)
records instruction-shaped text when it appears — "ignore all previous instructions",
`SYSTEM:` line prefixes, `<system>` tags, "reveal your API key", developer-mode framings.
Detection changes **no behaviour**: it exists so a human can see that a page tried, and it
appears in the envelope's `errors` section. `strip_injection_bait()` neutralises such text
before it can reach any summarisation step.

The guarantee does not rest on the detector catching everything. It rests on the agent
having no mechanism by which page text could act: URLs to fetch come from the registry and
from the agent's own candidate generation, never from instructions embedded in a document.

## Resource limits

| Limit | Value | Why |
|---|---|---|
| Response ceiling | 4 MB | A hostile or broken server must not exhaust the 10 GB temp disk |
| Request timeout | 12 s | One slow host must not consume the 45-minute wall clock |
| Redirect limit | 4 hops | Redirect loops |
| Retry limit | 2 attempts; retried only on statuses 408, 425, 429, 500, 502, 503, 504 and on network failures such as timeouts and resets | Retries are charged to the budget. A refused connection, a TLS failure or an unsupported media type is terminal on the first attempt, because asking again cannot change the answer |
| Per-host delay | 0.6 s | Politeness; the competition does not reward request volume |

Non-textual content types are recorded and skipped rather than downloaded in full.

## Plain HTTP

Candidates are proposed as `https://`. When a company's host *refuses* port 443 or fails
the TLS handshake, the same host is asked once over `http://` on port 80. That recovers
real company sites that do not serve HTTPS. The limits:

- Only a refused connection or a TLS failure triggers it. A timeout, a reset or an HTTP
  error status does not.
- Certificate verification is never relaxed. The HTTPS attempt either validates or fails;
  there is no "accept invalid certificate" path.
- The plain-HTTP request passes through the same URL policy, the same address pinning and
  the same budget as every other request.
- It is disclosed, not hidden: the published website value keeps the `http://` scheme and
  a note records that the site refused TLS.
- The page still goes to the identity gate unchanged; the gate does not know which scheme
  was used, so the fallback cannot widen what is published.

Traffic on that one request is unencrypted and could be read or altered in transit. The
agent sends no credentials, cookies or personal data, so what is exposed is which public
page was read. A tampered response would be a poisoned page, covered under
"Not defended" below.

## Canonical URLs

Tracking parameters (`utm_*`, `fbclid`, `gclid`, ...), cache-busters (`doing_wp_cron`,
`nocache`) and session identifiers (`PHPSESSID`, `JSESSIONID`, `;jsessionid=` path
parameters, `CFID`/`CFTOKEN`) are removed before a URL is published or compared. Two
reasons: a session identifier in a published URL is someone's session, and a
cache-buster made one measured refresh report a false material change on a company
website. Meaningful parameters such as `?side=2` are kept.

## robots.txt

Honoured for company websites, with the fetch itself charged to the request budget. An
unreachable `robots.txt` allows the fetch, matching the usual convention that a missing
file is not a directive; a retrieved file that disallows the path blocks it, and the claim
becomes `blocked` — not `not_available`, because we were refused rather than finding
nothing.

The agent identifies itself honestly:

```
SignalpostResearchBot/1.0 (+https://github.com/Hardik182005/signalpost; company research for the Signalpost challenge)
```

No user-agent spoofing, no header games, no evasion of access controls. Where a site
refuses us, the refusal is recorded as a finding.

## Secrets

- Credentials are read from environment variables only. No key is ever written to a file
  in this repository, printed to the console, or committed.
- `.env`, `.env.*`, `*.pem`, `*.key`, `*.p12`, `credentials.json`, `token.json`,
  `.cookies/` and `.browser-profile/` are excluded by `.gitignore`. `.env.example` is the
  one exception and contains placeholders only.
- Sensitive query parameters (`api_key`, `token`, `access_token`, `signature`, `password`,
  …) are stripped from every logged URL by `redact_url()`.
- Sensitive headers (`Authorization`, `Cookie`, `Set-Cookie`, `Proxy-Authorization`, …)
  are stripped by `redact_headers()`.
- Snapshots store response bodies, not request headers, so a credential cannot reach the
  snapshot directory by that path.

The submitted run path uses **no API keys at all** — see
[`MODELS_AND_APIS.md`](../MODELS_AND_APIS.md). There is nothing to leak because there is
nothing configured.

## What is excluded from Git and from the submission ZIP

Raw snapshots, local run output, browser state, downloaded bulk archives and the local
`.fetch/` working directory are excluded from Git. The submission ZIP is built by
`scripts/signalpost/package_submission.py`: its `source/` is a `git archive` of the tested
commit (so `.git`, `.env`, virtual environments, caches and untracked files cannot enter it),
and its `evidence/snapshots/` holds only official-register response bodies; company-website
bodies are referenced by SHA-256 and not redistributed. After building, the packager extracts
the ZIP into a fresh directory (refusing traversal and symlink members) and scans every text
payload file for credential patterns (Anthropic, OpenAI, GitHub, AWS, Google and Slack keys,
private-key blocks), for the builder's own checkout and home-directory paths, and for forbidden
names (`.env`, `.git`, `.venv`, `node_modules`, `__pycache__`); `--verify-only` repeats the same
checks on an existing ZIP. These are pattern scans: they catch the listed key formats, not every
possible secret. See [`SUBMISSION_CHECKLIST.md`](SUBMISSION_CHECKLIST.md) for the recorded result.
No curated evidence set exists under `docs/`.

## Not defended

Stated so nobody assumes otherwise:

- **A compromised upstream official registry.** Data from `data.brreg.no` is the identity
  anchor and is trusted. If BRREG served wrong data, the agent would publish it with
  correct provenance pointing at BRREG.
- **Traffic analysis.** The agent's request pattern reveals which companies are being
  researched to any network observer.
- **Deliberate poisoning of a company's own website.** A company that publishes false
  facts about itself will have those facts published with correct provenance. Provenance
  is the defence: a reviewer can see exactly which page said it.
- **Denial of service by a hostile host.** Timeouts and the per-host delay bound the
  damage to one company's profile, not the run — but a company whose site is engineered to
  be slow will get a thinner profile.

## Reporting

Security issues in this repository can be raised as a GitHub issue at
`https://github.com/Hardik182005/signalpost`, or through the contact address in the
submission email.
