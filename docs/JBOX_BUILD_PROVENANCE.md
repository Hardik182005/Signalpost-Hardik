# JBox build provenance — Signalpost website

**Public URL:** https://9-copy-ready-jbox-prompt-create-docs-jbo.sites.jboxai.com/
(JBox hosted site, HTTPS. The subdomain comes from the JBox project name.)

## Who built what

| Part | Built by | Where |
|---|---|---|
| Website (Next.js pages, layout, 3D beacon hero, fallbacks, search, profile, compare, method) | **JBox** (jboxai.com), from the prompt in `docs/JBOX_PASTE_PROMPT.md` plus two follow-up fix messages | JBox project `usbe3zu3gl4bzjp4y7irwcpx` (https://jboxai.com/project/usbe3zu3gl4bzjp4y7irwcpx/), preview `https://usbe3zu3gl4bzjp4y7irwcpx.preview.jboxai.com/` |
| Dataset (100 company profiles, evidence, sources) | The Signalpost agent in this repository; exported by `scripts/signalpost/export_showcase.py` | `showcase/data/v1/` at commit `07255e2`, served read-only from `raw.githubusercontent.com` |
| Prompt, data export, browser checks, this record | Claude Code (operating the user's JBox account in the user's Chrome, at the user's request) | this repository |

JBox hosts the website. The research agent does not depend on JBox, Node, WebGL or any
database, and the site cannot start research jobs. It only reads the static snapshot.

## Timeline (6 Nov 2026, IST)

| Time | Event |
|---|---|
| ~15:30 | Prompt from `docs/JBOX_PASTE_PROMPT.md` pasted into the JBox project chat. JBox built the site in 7 min 37 s |
| 15:41 | Fix request: phone navigation overflow and WebGL fallback check |
| 15:51 | Retry of the navigation fix as a new message. JBox applied CSS-only rules (revision `ea6fc9ad2c7934cc`) |
| ~16:00 | Published with the JBox "Publish hosted site" button, with the user's approval |

## Dataset

- URL: `https://raw.githubusercontent.com/Hardik182005/Signalpost-Hardik/feat/signalpost-jbox-feedback-nov2026/showcase/data/v1/`
- Manifest commit `07255e2`, run id `nov2026-prelim` (100 holdout companies, run on 6 Nov 2026).
- The site is a **snapshot**: it shows that run's results and does not research live.
  It is labelled as a research snapshot on the page.
- The final research run for this revision (`nov2026-final`, commit `772f48d`) is in the
  submission package. Its research code is identical to the prelim export except for the
  revenue group-year fallback, so revenue-change wording can differ for a few group-reporting companies.

## Checks

See `docs/evidence/nov2026/ui-checks.md`. In summary, the following work:

- HTTPS and signed-out deep links
- search → profile → evidence dialog
- compare
- back navigation
- no-results, missing-data and failed-source states
- phone width without overflow
- the phone menu
- the no-WebGL SVG fallback
- reduced motion with no animation loop

There is one minor gap: focus does not return to the trigger after the evidence dialog closes.

## Cost and control

- Hosting uses JBox credits at about **1 credit per day** from the account's existing balance.
  No purchase or subscription was made.
- The site can be taken offline or deleted from the project's publishing panel ("Open publishing
  options" → Take offline / Delete).
- No secrets are used by the site: the dataset is public and static, and there are no API
  keys or environment variables.
