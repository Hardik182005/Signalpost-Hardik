"""Company website research: discovery, crawl planning and external-family claims.

This is where the externally-scored families come from — verified web presence,
business description, contact details, hiring and dated activity. It is also the
riskiest part of the agent, because a wrong website means every claim derived from
it is attached to the wrong company. So the order is always:

1. Find candidate sites (registry first, then conservative inference).
2. Fetch the landing page and run the identity gate on it.
3. Only if the gate publishes, spend further budget crawling that site.

A company whose site cannot be verified still produces a complete profile: its
external claims are recorded as `not_available` or `ambiguous` with reasons, which is
an honest result rather than a gap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable

from .budget import BudgetExceeded
from .discovery import DiscoveryResult, discover
from .extract import fields as F
from .extract import html as H
from .fetch import Fetcher, Response
from .identity import CompanyIdentity, IdentityResolver, IdentityVerdict
from .models import Claim, Evidence, Locator, Snapshot, claim_key, stable_id
from .net import canonical_url, registrable_domain

# Crawl order: the pages most likely to carry scored facts come first, so a company
# that runs out of budget still gets the highest-value pages.
CRAWL_PRIORITY = ("contact", "about", "careers", "news", "team", "services")

# Transport failures that say something about the port rather than about the site, and
# so justify asking the same host once on :80. A timeout or a reset is excluded: those
# are answers about the site, and retrying them on another port spends request budget
# on a question already answered.
_DOWNGRADE_ERROR_KINDS = frozenset({"refused", "tls"})


DEFAULT_MAX_PAGES = 5
DEFAULT_MAX_CANDIDATES = 4

# Pages read to corroborate a landing page the identity gate found ambiguous, and the
# kinds of page worth reading for it: the places a Norwegian company puts its
# organisation number, address and phone.
CORROBORATION_PAGES = 2
CORROBORATION_KINDS = ("contact", "about")

# Statuses that mean "we were refused", not "there is nothing here". A registry-listed
# site answering with one of these is evidence the site exists and that we could not
# read it, which is the `blocked` availability state rather than `not_available`.
ACCESS_REFUSED_STATUSES = frozenset({401, 403, 405, 429, 451, 503})


def _is_access_refusal(status: int | None) -> bool:
    return status in ACCESS_REFUSED_STATUSES


@dataclass(slots=True)
class PageVisit:
    """One fetched page of a company website."""

    url: str
    kind: str
    response: Response
    soup: Any = None
    text: str = ""
    blocks: list[dict[str, Any]] = field(default_factory=list)
    injection_hits: list[str] = field(default_factory=list)


@dataclass(slots=True)
class WebsiteResult:
    """Everything the website stage produced for one company."""

    claims: list[Claim] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    snapshots: list[Snapshot] = field(default_factory=list)
    verdict: IdentityVerdict | None = None
    verified_url: str | None = None
    pages: list[str] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    injection_reports: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    discovery: DiscoveryResult | None = None
    access_refused: list[dict[str, Any]] = field(default_factory=list)
    corroboration: list[PageVisit] = field(default_factory=list)


class WebsiteResearcher:
    """Verifies a company website and extracts the external-family claims."""

    def __init__(
        self,
        fetcher: Fetcher,
        *,
        max_pages: int = DEFAULT_MAX_PAGES,
        max_candidates: int = DEFAULT_MAX_CANDIDATES,
        today: date | None = None,
    ) -> None:
        self.fetcher = fetcher
        self.max_pages = max_pages
        self.max_candidates = max_candidates
        self.today = today

    # -- entry point ----------------------------------------------------------

    def research(self, org: str, identity: CompanyIdentity, *, page_budget: int | None = None) -> WebsiteResult:
        result = WebsiteResult()
        allowance = page_budget if page_budget is not None else self.max_pages

        landing = self._verify_landing(org, identity, result)
        if landing is None:
            result.claims.extend(self._unverified_claims(org, result))
            return result

        visit, verdict = landing
        result.verdict = verdict
        result.verified_url = canonical_url(visit.response.final_url)
        result.pages.append(result.verified_url)

        visits = [visit]
        for page in self._crawl(visit, allowance - 1, result):
            visits.append(page)
            result.pages.append(canonical_url(page.response.final_url))

        cited = {canonical_url(page.response.final_url) for page in visits}
        for page in result.corroboration:
            if canonical_url(page.response.final_url) not in cited:
                visits.append(page)
                result.pages.append(canonical_url(page.response.final_url))

        result.snapshots.extend(self._cited_snapshots(visits, result.corroboration))
        for page in visits:
            if page.injection_hits:
                result.injection_reports.append(
                    {
                        "url": canonical_url(page.response.final_url),
                        "patterns": page.injection_hits,
                        "action": "recorded only; page content is treated as data and never as instruction",
                    }
                )

        self._emit_claims(org, identity, visits, result)
        return result

    @staticmethod
    def _cited_snapshots(visits: list[PageVisit], corroboration: list[PageVisit]) -> list[Snapshot]:
        """Every snapshot a website claim can cite, once each.

        Deduplicated by snapshot id, not by URL. The website claim cites each
        corroborating page, and the crawl can fetch the same URL again; on a page that
        renders differently per request the two fetches are different snapshots, and
        dropping the corroboration copy left its evidence pointing at nothing. Measured
        on 2 of 186 verified websites in the 1,000-company run before this fix.
        """
        seen: set[str] = set()
        snapshots: list[Snapshot] = []
        for page in [*visits, *corroboration]:
            snapshot = page.response.snapshot
            if snapshot is not None and snapshot.id not in seen:
                seen.add(snapshot.id)
                snapshots.append(snapshot)
        return snapshots

    # -- verification ---------------------------------------------------------

    def _verify_landing(
        self, org: str, identity: CompanyIdentity, result: WebsiteResult
    ) -> tuple[PageVisit, IdentityVerdict] | None:
        resolver = IdentityResolver(identity)
        best: tuple[PageVisit, IdentityVerdict] | None = None

        # Candidates that cannot resolve are dropped here without spending a request.
        result.discovery = discover(identity, max_candidates=self.max_candidates)
        for candidate in result.discovery.candidates:
            url, from_registry = candidate.url, candidate.from_registry
            try:
                response, url = self._fetch_landing(url, result)
            except BudgetExceeded as exc:
                result.notes.append(f"website verification stopped: {exc}")
                break

            if not response.ok or not response.text:
                record = {
                    "url": canonical_url(url),
                    "source": candidate.source,
                    "decision": "unreachable",
                    "reason": response.error or f"HTTP {response.status}",
                    "status": response.status,
                }
                result.rejected.append(record)
                if from_registry and _is_access_refusal(response.status):
                    result.access_refused.append(record)
                continue

            visit = self._page(response, "landing")
            title = H.page_title(visit.soup).value if H.page_title(visit.soup) else ""

            # A resolving guessed domain is very often a registrar parking page. Saying
            # so is a better research result than "insufficient identity evidence",
            # and there is nothing on such a page worth putting to the identity gate.
            parked = F.looks_parked(str(title), visit.text)
            if parked:
                result.rejected.append(
                    {
                        "url": canonical_url(response.final_url),
                        "source": candidate.source,
                        "decision": "rejected",
                        "reason": f"domain resolves to a parking or placeholder page ({parked!r})",
                    }
                )
                continue

            verdict = resolver.assess_website(
                response.final_url,
                page_text=visit.text,
                page_title=str(title),
                emails=[str(item.value) for item in F.extract_emails(visit.soup, visit.text)],
                phones=[str(item.value) for item in F.extract_phones(visit.soup, visit.text)],
                from_registry=from_registry,
            )
            if verdict.decision == "ambiguous" and not verdict.conflicts:
                verdict = self._corroborate(visit, str(title), verdict, resolver, from_registry, result)
            if verdict.publishable:
                return visit, verdict
            result.rejected.append({**verdict.to_dict(), "source": candidate.source})
            if best is None and verdict.decision == "ambiguous":
                best = (visit, verdict)

        if best is not None:
            result.verdict = best[1]
            result.notes.append("no candidate site met the publication threshold")
        return None

    def _corroborate(
        self,
        landing: PageVisit,
        title: str,
        verdict: IdentityVerdict,
        resolver: IdentityResolver,
        from_registry: bool,
        result: WebsiteResult,
    ) -> IdentityVerdict:
        """Read the site's contact and about pages before giving up on an ambiguous landing.

        A landing page is often just a name and a picture; the organisation number,
        address and phone are one click away. When the identity gate finds a landing
        page ambiguous, reading at most two same-site contact or about pages and putting
        the combined evidence to the *same* gate recovers sites without lowering what
        publication requires. A conflict (another organisation number, another legal
        form) is never corroborated away: those verdicts do not reach this method.

        The pages are cached by the fetcher, so the crawl that follows a publication
        does not pay for them twice.
        """
        base = landing.response.final_url
        home = registrable_domain(base)
        planned: dict[str, str] = {}
        for url, anchor in H.links(landing.soup, base):
            kind = F.classify_link(url, anchor)
            if registrable_domain(url) != home or kind not in CORROBORATION_KINDS:
                continue
            canonical = canonical_url(url)
            if canonical != canonical_url(base) and canonical not in planned:
                planned[canonical] = kind
        if not planned:
            return verdict

        pages: list[PageVisit] = []
        for url, kind in list(planned.items())[:CORROBORATION_PAGES]:
            if self.fetcher.budget.should_stop_discretionary():
                break
            try:
                response = self.fetcher.get(url, purpose="website_corroboration", source_class="company_owned")
            except BudgetExceeded:
                break
            if response.ok and response.text and registrable_domain(response.final_url) == home:
                pages.append(self._page(response, kind))
        if not pages:
            return verdict

        visits = [landing, *pages]
        combined = resolver.assess_website(
            base,
            page_text="\n".join(page.text for page in visits),
            page_title=title,
            emails=[str(i.value) for page in visits for i in F.extract_emails(page.soup, page.text)],
            phones=[str(i.value) for page in visits for i in F.extract_phones(page.soup, page.text)],
            from_registry=from_registry,
        )
        if not combined.publishable:
            return combined if combined.conflicts else verdict
        combined.reason = (
            f"{combined.reason}; corroborated on "
            f"{', '.join(canonical_url(page.response.final_url) for page in pages)}"
        )
        result.corroboration = pages
        return combined

    def _fetch_landing(self, url: str, result: WebsiteResult) -> tuple[Response, str]:
        """Fetch a candidate landing page, downgrading to `http://` once if needed.

        Discovery proposes every candidate as `https://`, which is right as a default and
        wrong as an assumption. A live check of the hosts that failed in a measured
        100-company run found three Norwegian company sites — `bergenproperty.no`,
        `oceancleaning.no`, `sofiesgullogur.no` — that refuse :443 outright and answer
        HTTP 200 on :80. Three real websites in eight failures is not a rounding error in
        a competition scored on recall.

        So a *settled* transport failure on :443 is treated as a question about the port,
        not about the company, and the same host is asked once on :80. A timeout, a 404
        or a reset is not retried this way: those are answers about the site itself.

        Two things this deliberately does not do. It does not widen what may be
        published: the returned page still goes to the identity gate unchanged, and the
        gate has no idea which scheme was used. And it does not hide the downgrade — the
        published website value will read `http://…`, which is the truth about how that
        company publishes itself.
        """
        response = self.fetcher.get(url, purpose="website_landing", source_class="company_owned")
        if response.ok or not url.lower().startswith("https://"):
            return response, url
        if response.error_kind not in _DOWNGRADE_ERROR_KINDS:
            return response, url

        fallback = "http://" + url[len("https://") :]
        retry = self.fetcher.get(fallback, purpose="website_landing", source_class="company_owned")
        if not retry.ok:
            return response, url
        result.notes.append(
            f"{canonical_url(url)} refused TLS ({response.error_kind}); "
            f"the site answers over plain HTTP and is recorded as such"
        )
        return retry, fallback

    # -- crawling -------------------------------------------------------------

    def _crawl(self, landing: PageVisit, allowance: int, result: WebsiteResult) -> list[PageVisit]:
        if allowance <= 0:
            return []
        base = landing.response.final_url
        home = registrable_domain(base)
        planned: dict[str, str] = {}

        for url, anchor in H.links(landing.soup, base):
            if registrable_domain(url) != home:
                continue
            kind = F.classify_link(url, anchor)
            if not kind:
                continue
            canonical = canonical_url(url)
            if canonical == canonical_url(base) or canonical in planned:
                continue
            if kind not in {key for key in planned.values()}:
                planned[canonical] = kind

        ordered = sorted(
            planned.items(),
            key=lambda item: CRAWL_PRIORITY.index(item[1]) if item[1] in CRAWL_PRIORITY else 99,
        )

        visits: list[PageVisit] = []
        for url, kind in ordered[:allowance]:
            if self.fetcher.budget.should_stop_discretionary():
                result.notes.append("crawl stopped early to protect the run budget")
                break
            try:
                response = self.fetcher.get(url, purpose=f"website_{kind}", source_class="company_owned")
            except BudgetExceeded as exc:
                result.notes.append(f"crawl stopped: {exc}")
                break
            if not response.ok or not response.text:
                continue
            visits.append(self._page(response, kind))
        return visits

    def _page(self, response: Response, kind: str) -> PageVisit:
        soup = H.parse(response.text)
        text = H.visible_text(soup)
        blocks = H.structured_data(response.text, response.final_url)
        return PageVisit(
            url=response.final_url,
            kind=kind,
            response=response,
            soup=soup,
            text=text,
            blocks=blocks,
            injection_hits=H.detect_injection(text),
        )

    # -- claim emission -------------------------------------------------------

    def _evidence_for(self, page: PageVisit, candidate: F.Candidate) -> Evidence | None:
        snapshot = page.response.snapshot
        if snapshot is None:
            return None
        return Evidence(
            id=stable_id("ev", snapshot.id, candidate.locator_value, str(candidate.value)[:120]),
            snapshot_id=snapshot.id,
            source_url=snapshot.final_url,
            source_class=snapshot.source_class,
            retrieved_at=snapshot.retrieved_at,
            content_sha256=page.response.content_sha256,
            locator=Locator(
                kind=candidate.locator_kind,
                value=candidate.locator_value,
                excerpt=candidate.excerpt,
            ),
            claim_span=candidate.excerpt,
            extraction_method=candidate.method,
        )

    def _emit_claims(
        self,
        org: str,
        identity: CompanyIdentity,
        visits: list[PageVisit],
        result: WebsiteResult,
    ) -> None:
        verdict = result.verdict
        assert verdict is not None

        landing = visits[0]
        site_evidence = self._evidence_for(
            landing,
            F.Candidate(
                "website",
                result.verified_url,
                "css_selector",
                "html",
                excerpt=result.verified_url,
                method="identity_gate",
            ),
        )
        site_evidence_ids: list[str] = []
        if site_evidence:
            result.evidence.append(site_evidence)
            site_evidence_ids.append(site_evidence.id)
        # A site published on corroborating pages cites them too: the verdict rests on
        # what those pages say, and a reviewer must be able to reach it.
        for page in result.corroboration:
            item = self._evidence_for(
                page,
                F.Candidate(
                    "website",
                    result.verified_url,
                    "css_selector",
                    "html",
                    excerpt=canonical_url(page.response.final_url),
                    method="identity_gate",
                ),
            )
            if item and item.id not in site_evidence_ids:
                result.evidence.append(item)
                site_evidence_ids.append(item.id)
        result.claims.append(
            Claim(
                claim_key=claim_key(org, "website"),
                field="website",
                family="web_presence",
                value=result.verified_url,
                availability="available",
                organisation_number=org,
                evidence_ids=site_evidence_ids,
                confidence=verdict.confidence,
                identity_ref=verdict.reason,
                extraction_method="identity_gate",
            )
        )

        def publish(
            page: PageVisit,
            candidate: F.Candidate,
            *,
            field_name: str,
            family: str,
            discriminators: tuple[Any, ...] = (),
            confidence: float | None = None,
            **extra: Any,
        ) -> None:
            item = self._evidence_for(page, candidate)
            if item:
                result.evidence.append(item)
            result.claims.append(
                Claim(
                    claim_key=claim_key(org, field_name, *discriminators),
                    field=field_name,
                    family=family,
                    value=candidate.value,
                    availability="available",
                    organisation_number=org,
                    evidence_ids=[item.id] if item else [],
                    confidence=confidence if confidence is not None else candidate.confidence,
                    extraction_method=candidate.method,
                    **extra,
                )
            )

        seen: set[str] = set()

        # Business description: take the single best-supported statement.
        description_pages = [page for page in visits if page.kind in ("about", "landing")]
        best_description: tuple[PageVisit, F.Candidate] | None = None
        for page in description_pages:
            for candidate in F.extract_description(page.soup, page.blocks, page.response.text, page.response.final_url):
                if best_description is None or candidate.confidence > best_description[1].confidence:
                    best_description = (page, candidate)
        if best_description:
            publish(
                best_description[0],
                best_description[1],
                field_name="business_description",
                family="business_description",
            )
        else:
            result.claims.append(
                Claim(
                    claim_key=claim_key(org, "business_description"),
                    field="business_description",
                    family="business_description",
                    value=None,
                    availability="not_available",
                    organisation_number=org,
                    reason="No self-description found on the verified website",
                )
            )

        # Contact details.
        emails: list[tuple[PageVisit, F.Candidate]] = []
        phones: list[tuple[PageVisit, F.Candidate]] = []
        addresses: list[tuple[PageVisit, F.Candidate]] = []
        for page in visits:
            emails += [(page, item) for item in F.extract_emails(page.soup, page.text)]
            phones += [(page, item) for item in F.extract_phones(page.soup, page.text)]
            addresses += [(page, item) for item in F.extract_addresses(page.text)]

        for page, candidate in emails[:5]:
            key = f"email::{candidate.value}"
            if key in seen:
                continue
            seen.add(key)
            publish(page, candidate, field_name="email", family="contact", discriminators=(candidate.value,))

        for page, candidate in phones[:4]:
            key = f"phone::{candidate.value}"
            if key in seen:
                continue
            seen.add(key)
            publish(page, candidate, field_name="phone", family="contact", discriminators=(candidate.value,))

        for page, candidate in addresses[:4]:
            code = candidate.value.get("postal_code")
            key = f"addr::{code}"
            if key in seen:
                continue
            seen.add(key)
            publish(
                page,
                candidate,
                field_name="site_address",
                family="contact",
                discriminators=(code,),
            )

        if not emails and not phones:
            result.claims.append(
                Claim(
                    claim_key=claim_key(org, "site_contact"),
                    field="site_contact",
                    family="contact",
                    value=None,
                    availability="not_available",
                    organisation_number=org,
                    reason="No email or phone published on the verified website",
                )
            )

        # Social profiles linked from the verified site. The link alone is not ownership:
        # sites link their theme vendor, their parent group and their partners too, so
        # each account also goes through the identity gate. See `assess_profile`.
        resolver = IdentityResolver(identity)
        site_domain = registrable_domain(result.verified_url or "")
        for page in visits:
            for candidate in F.extract_social_profiles(page.soup, page.response.final_url):
                platform = candidate.value["platform"]
                key = f"social::{platform}"
                handle = str(candidate.value.pop("handle", "") or "")
                if key in seen:
                    continue
                profile = resolver.assess_profile(
                    candidate.value["url"],
                    linked_from_site=True,
                    handle=handle,
                    website_domain=site_domain,
                )
                if not profile.publishable:
                    result.notes.append(
                        f"{platform} link {candidate.value['url']} not published: {profile.reason}"
                    )
                    continue
                seen.add(key)
                publish(
                    page,
                    candidate,
                    field_name="social_profile",
                    family="web_presence",
                    discriminators=(platform,),
                    confidence=profile.confidence,
                    identity_ref=profile.reason,
                )

        # Hiring.
        job_pages = [page for page in visits if page.kind in ("careers", "landing")]
        jobs_found = 0
        for page in job_pages:
            for candidate in F.extract_jobs(page.soup, page.blocks, page.response.final_url, page.text):
                if candidate.field == "hiring_signal":
                    if "hiring::signal" in seen:
                        continue
                    seen.add("hiring::signal")
                    publish(page, candidate, field_name="is_hiring", family="jobs")
                    jobs_found += 1
                    continue
                title = str(candidate.value.get("title") or "")
                key = f"job::{title.casefold()}"
                if key in seen or not title:
                    continue
                seen.add(key)
                publish(
                    page,
                    candidate,
                    field_name="job_posting",
                    family="jobs",
                    discriminators=(title,),
                    effective_date=candidate.value.get("posted_at"),
                )
                jobs_found += 1
                if jobs_found >= 25:
                    break
        if not jobs_found:
            visited_careers = any(page.kind == "careers" for page in visits)
            result.claims.append(
                Claim(
                    claim_key=claim_key(org, "job_postings"),
                    field="job_postings",
                    family="jobs",
                    value=None,
                    availability="not_available" if visited_careers else "ambiguous",
                    organisation_number=org,
                    reason=(
                        "Careers page reachable but lists no current openings"
                        if visited_careers
                        else "No careers page found within the crawl budget"
                    ),
                )
            )

        # Dated activity.
        activity_pages = [page for page in visits if page.kind in ("news", "landing")]
        activity_found = 0
        for page in activity_pages:
            for candidate in F.extract_activity(page.soup, page.blocks, page.response.final_url, today=self.today):
                title = str(candidate.value.get("title") or "")
                key = f"act::{title.casefold()[:80]}"
                if key in seen or not title:
                    continue
                seen.add(key)
                publish(
                    page,
                    candidate,
                    field_name="activity_item",
                    family="activity",
                    discriminators=(title[:80],),
                    effective_date=candidate.value.get("published_at"),
                )
                activity_found += 1
                if activity_found >= 20:
                    break
        if not activity_found:
            visited_news = any(page.kind == "news" for page in visits)
            result.claims.append(
                Claim(
                    claim_key=claim_key(org, "activity"),
                    field="activity",
                    family="activity",
                    value=None,
                    availability="not_available" if visited_news else "ambiguous",
                    organisation_number=org,
                    reason=(
                        "News section reachable but contains no dated items"
                        if visited_news
                        else "No dated news or blog section found within the crawl budget"
                    ),
                )
            )

        # People named on the site, kept separate from the registry role claims.
        for page in visits:
            if page.kind not in ("team", "about"):
                continue
            for candidate in F.extract_people(page.soup, page.blocks):
                name = str(candidate.value.get("name") or "")
                key = f"person::{name.casefold()}"
                if key in seen or not name:
                    continue
                seen.add(key)
                publish(
                    page,
                    candidate,
                    field_name="site_person",
                    family="people",
                    discriminators=(name,),
                )

    # -- fallback -------------------------------------------------------------

    def _unverified_claims(self, org: str, result: WebsiteResult) -> list[Claim]:
        """Explicit, reasoned states for a company with no verified website.

        Every external family still gets a claim, because a stated `not_available` with
        a reason is a real research result and an omission is not.
        """
        if result.access_refused:
            # The registry vouches for a site we were refused access to. Reporting
            # `not_available` would assert we looked and found nothing published;
            # `blocked` says what actually happened and keeps the distinction the
            # evaluator scores.
            refusal = result.access_refused[0]
            state = "blocked"
            reason = (
                f"Registry-listed website {refusal['url']} refused automated access "
                f"({refusal['reason']})"
            )
        elif result.verdict is not None and result.verdict.decision == "ambiguous":
            state, reason = "ambiguous", result.verdict.reason
        elif result.rejected:
            state, reason = "not_available", "Candidate websites failed the identity gate"
        elif result.discovery is not None and not result.discovery.candidates:
            state, reason = (
                "not_available",
                "No candidate domain for this company resolved in DNS"
                f" ({result.discovery.dns_lookups} lookups)",
            )
        else:
            state, reason = "not_available", "No website candidate available for this company"

        families = (
            ("website", "web_presence"),
            ("business_description", "business_description"),
            ("site_contact", "contact"),
            ("job_postings", "jobs"),
            ("activity", "activity"),
        )
        return [
            Claim(
                claim_key=claim_key(org, name),
                field=name,
                family=family,
                value=None,
                availability=state,
                organisation_number=org,
                reason=reason,
                identity_ref=result.verdict.reason if result.verdict else None,
            )
            for name, family in families
        ]
