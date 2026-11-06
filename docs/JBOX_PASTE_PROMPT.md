# JBox paste prompt — Signalpost website

This is the prompt pasted into the JBox project chat to build the public website. It
references only a dataset that exists and was fetched before pasting (see
`docs/JBOX_BUILD_PROVENANCE.md` for what JBox actually built and where it is hosted).

Data base URL (public, read-only, CORS-enabled static files on GitHub):

```
https://raw.githubusercontent.com/Hardik182005/Signalpost-Hardik/feat/signalpost-jbox-feedback-nov2026/showcase/data/v1/
```

---

## Prompt (pasted verbatim)

Build the **Signalpost website now** — a real, responsive Next.js site, not a document.
Do not create docs files. Do not use Supabase or any database: the data is a static,
read-only JSON export that already exists at the URLs below. Fetch it from the browser
(or at build time) — these URLs return JSON with `Access-Control-Allow-Origin: *`.

**Data (verified, live):**
- `DATA = https://raw.githubusercontent.com/Hardik182005/Signalpost-Hardik/feat/signalpost-jbox-feedback-nov2026/showcase/data/v1`
- `${DATA}/manifest.json` — `{schema, label, generated_at, run_ids, repository, commit, counts{companies, terminal_status, verified_websites, with_social_profiles, with_news, with_revenue, with_revenue_comparison}, caveats[]}`
- `${DATA}/index.json` — `{companies: [{org, name, former_names[], legal_form, city, industry, status, website, socials[], news_count, employees, revenue{value, period, currency, scope}|null, revenue_change{status, absolute, percent}|null, facts, sources}]}`
- `${DATA}/companies/{org}.json` — `{organisation_number, name, terminal_status, terminal_reason, researched_at, brief{headline, summary, sentences[{text, claim_keys[], topic}], highlights[{label, text, claim_keys[]}], gaps[], confidence, revenue_comparison{status, latest{reporting_period, value, currency, scope, claim_key}, previous{…}|null, absolute_change, percent_change, reason}}, website{verdict, verified_url, rejected[{url, reason}]}, news_search{…}, families[{family, label, facts[{key, field, availability, value, evidence[], reporting_period?, scope?, currency?, reason?}]}], sources[{id, url, source_class, retrieved_at, sha256, locator{kind, value}, excerpt, method}]}`

Use only these values. Never invent a company, figure, date, URL or source. A missing
value is shown as "Not established" with its `reason`, never as 0. Label the data as a
snapshot: "Research snapshot · {manifest.label} · generated {manifest.generated_at}".

**Brand.** Name "Signalpost". Tagline: **"Find the company. Follow the evidence."**
Supporting line: "Research Norwegian companies, compare their latest filed accounts, and
trace each published fact to its source." Colours: deep ink `#081A20`, panel `#102B33`,
warm background `#F5F7F4`, text `#10252C`, muted `#53676D`, teal action `#007F74`,
mint accent `#76E3C3` (mint only on dark backgrounds). Precise sans typography
(Inter or similar), tabular numbers for money, generous spacing, excellent tables.
No chatbot layout, no stock photos, no repeated glassmorphism cards.

**Pages.**
1. **Home `/`** — dark hero: tagline, supporting line, a large search box (company name
   or 9-digit organisation number, instant results from `index.json` with keyboard
   navigation), and beside it an **interactive 3D signal beacon** (three.js / react-three-fiber):
   a slim mast emitting slow concentric pulse rings over an abstract topographic fjord
   grid; it gently follows the pointer / can be dragged to rotate. Respect
   `prefers-reduced-motion` (render a static frame), lazy-load it, and show a static SVG
   beacon if WebGL is unavailable. Below the hero: the snapshot stats from
   `manifest.counts` (companies, verified websites, with social profiles, with news,
   with revenue comparison) and a "How it works" strip: Registry → Website identity check
   → Facts with sources → Summary.
2. **Browse `/companies`** — sortable, filterable table of all companies: name, org
   number, city, industry, status badge, latest revenue (formatted NOK, period year),
   revenue change (▲/▼ % or "unknown"), website, socials icons, news count. Filters:
   status, has website, has revenue. On phones it becomes stacked rows.
3. **Company `/company/[org]`** — header with name, org number, legal form, city, status
   badge, verified website link. Then:
   - **Summary**: each `brief.sentences[].text` as a paragraph; each sentence has small
     citation chips for its `claim_keys` that scroll to / highlight that fact.
   - **Revenue card**: from `brief.revenue_comparison` — latest vs previous year, absolute
     and % change, or the stated `reason` it is unknown.
   - **Facts by family** (accordion/tabs in `families` order): each fact shows value,
     period/scope/currency where present, availability badge, and a "Source" button
     opening a side drawer with the source URL, class, retrieval time, locator and
     excerpt from `sources` (matched by `evidence` ids). Unavailable facts show the reason.
   - **Online presence**: website + social profiles; **Company-published activity** and
     **Independent news** shown as two clearly separate lists.
   - **What we could not establish**: `brief.gaps`.
4. **Compare `/compare?a=ORG&b=ORG`** — pick two companies (search pickers), side-by-side
   columns: status, city, industry, employees, latest revenue + period, revenue change,
   website, socials, news count, facts and sources counts, and their summaries. Shareable URL.
5. **Method `/method`** — short page: data sources (Brønnøysund registers, the company's
   own verified website, NAV job ads, GDELT news candidates verified by reading the
   article), the identity rule (a website is published only with a decisive signal or two
   independent evidence groups), "missing ≠ zero", and the caveats from `manifest.caveats`,
   with links to the GitHub repository and the exact `manifest.commit`.

**Quality bar.** Works at 360 px and 1440 px with no horizontal scroll; keyboard
accessible; visible focus; WCAG AA contrast; loading skeletons; graceful error state if a
JSON file fails to load; fast (code-split the 3D scene). Footer: "Signalpost · research
snapshot · data from public Norwegian registers and company websites · source on GitHub".

When done, publish the site and tell me the public URL.
