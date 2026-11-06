# Manual sample review — verified websites and social profiles

**Sample:** every published website (14 of 100) and every published social-profile set
(10 companies, 21 profiles) in the final run `nov2026-final` (commit `772f48d`). This is a
census of those outputs, not a random sample. Companies where the agent abstained were not reviewed.

**Method:** Claude Code read each envelope's identity-gate verdict (signals, groups, score)
and the profile URLs, and compared them with the registry name, address and declared website.
The pages were not re-opened by a person. This review checks that each published value is
supported by the stored evidence and plausible as company-owned. It is not an
independent human accuracy audit, and it gives no precision figure.

## Websites (14)

| Org | Company | Published site | Why the gate accepted it | Review |
|---|---|---|---|---|
| 957855482 | HTH AS | www.hth.no | Registry lists hth.no (decisive) | Consistent |
| 950553952 | LARVIK BOLIGBYGGELAG | www.labo.no | Registered address, phone and email domain on the page (4 groups) | Consistent |
| 917457778 | DEN LILLE PRIKKEN OVER I'EN AS | denlilleprikkenoverien.no | Registry website; organisation number on page | Consistent |
| 983142338 | RENOMMÉ SKILT OG DEKOR AS | reskilt.no | Organisation number on page; address, phone, email | Consistent |
| 976250257 | LANGUAGE INFUSION AS | www.languageinfusion.no | Exact legal name, title, domain, postal code, city (3 groups) | **Flag:** the registry declares `www.flavus.no`. The published site matches the exact legal name, but the registry-declared domain was not the one published. Check which is current |
| 916574347 | MPENERGI AS | mp-energi.no | Registry website; organisation number on page | Consistent |
| 978630960 | PROSESSTEKNIKK AS | prosessteknikk.no | Exact legal name, domain, postal code, city (3 groups) | Consistent. There is no decisive signal, but the evidence groups are independent |
| 930520535 | ACCELERATE CAPITAL AS | acceleratecapital.no | Registered street address, name in title and domain (2 groups) | Consistent |
| 919529563 | VISIT JÆREN AS | xn--visitjren-l3a.com (visitjæren.com) | Organisation number on page (decisive) | **Flag:** the registry declares `visitjaeren.com` (ASCII). The IDN variant was published. Both are the same organisation according to the page, but the canonical domain should be checked |
| 997874544 | MALERMESTER F.A.G AS | www.mfag.no | Organisation number on page | Consistent |
| 911873621 | HOVELSRUDHAGEN AS | hovelsrud.no/hagen | Registered address and email domain (3 groups) | **Flag:** this is a section of a wider family site (hovelsrud.no), not a dedicated domain. The registry declares `www.hovelsrudhagen.no` |
| 933545113 | MAGISKE INDIAREISER AS | magiskeindiareiser.no | Registered address, name, domain (3 groups) | Consistent |
| 912238504 | TERMINALEN BYSCENE AS | www.terminalenbyscene.no | Name in title and domain, city (2 groups, score 1.05) | Consistent but weakest. No address, phone or organisation number was on the page |
| 985073546 | LISTER REKLAME AS | listerreklame.no | Organisation number, address, phone | Consistent |

Websites: 10 consistent with no caveat, 1 consistent but weakly evidenced, and 3 flagged for
domain choice. None was judged to belong to a different company.

## Social profiles (10 companies, 21 profiles)

| Company | Profiles | Review |
|---|---|---|
| DEN LILLE PRIKKEN OVER I'EN | Facebook, Pinterest, Instagram `denlilleprikkenoverien`; TikTok `@denlilleprikkenoveri1` | Consistent. The TikTok handle is truncated but linked from the company site |
| RENOMMÉ SKILT OG DEKOR | Facebook, Instagram, LinkedIn `reskilt` | Consistent with the domain |
| LANGUAGE INFUSION | Facebook `languageinfusion.no` | Consistent |
| MPENERGI | Twitter, Facebook `mpenergi` | Consistent |
| ACCELERATE CAPITAL | LinkedIn `accelerate-capital-no` | Consistent |
| VISIT JÆREN | Instagram `visit_jaeren`, Facebook and LinkedIn `visitjaeren` | Consistent |
| HOVELSRUDHAGEN | Facebook `hovelsrudhagen`; Instagram `hovelsrudkylling` | **Flag:** the Instagram handle names a poultry brand ("kylling") linked from the shared family site. It may belong to a sibling business rather than this entity |
| MAGISKE INDIAREISER | Instagram `magiskeindiareiser` | Consistent |
| TERMINALEN BYSCENE | Facebook `terminalen`; Instagram `terminalen_byscene` | **Flag:** `facebook.com/terminalen` is a generic handle. It is linked from the company site, but the handle alone does not identify the company |
| LISTER REKLAME | Facebook, Instagram `listerreklame` | Consistent |

Profiles: 19 consistent and 2 flagged. Every profile was linked from the verified company site.
None is a share link, a personal profile or an agency credit. Those are refused by design (see the tests).

## What this does not show

- It does not show recall. Companies where the agent abstained may still have a website. The abstention
  reasons are counted in `BEFORE_AFTER.md`.
- It does not include a person re-opening the live pages, so a site that changed after the run is not caught.
