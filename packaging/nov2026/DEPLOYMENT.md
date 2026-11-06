# Website deployment

| Item | Value |
|---|---|
| Public URL | https://9-copy-ready-jbox-prompt-create-docs-jbo.sites.jboxai.com/ |
| Built and hosted by | JBox (jboxai.com), project `usbe3zu3gl4bzjp4y7irwcpx` |
| Frontend source | Lives in the JBox project and is not exported to this repository. The prompt that produced it is `docs/JBOX_PASTE_PROMPT.md` |
| Data | Static JSON, `showcase/data/v1/` in this repository at commit `07255e2`, served read-only from `raw.githubusercontent.com` on branch `feat/signalpost-jbox-feedback-nov2026` |
| Dataset run | `nov2026-prelim`, 100 held-out companies, researched 2026-11-06 09:47–09:57 UTC (`dataset-manifest.json`) |
| Mode | Snapshot, labelled on the page. There is no live research, and visitors cannot start jobs or spend budget |
| Secrets | None. No keys, environment variables or database |
| Cost | About 1 JBox credit per day from the existing balance. It can be taken offline from the JBox publishing panel |

The dataset was exported by
`uv run python scripts/signalpost/export_showcase.py` from that run's envelopes. The export
drops tracking or credential-like query strings, local paths, traces and birth dates, and
publishes facts, source URLs, locators, hashes and short excerpts, not page bodies.

The research code at the final commit differs from the dataset's commit only in the revenue
group-year fallback. The final run's measured results are in `verification/research-comparison.md`.

Note: the site depends on the branch `feat/signalpost-jbox-feedback-nov2026` staying on
GitHub. If the branch is merged and deleted, the data URL must be repointed, for example to `main`.
