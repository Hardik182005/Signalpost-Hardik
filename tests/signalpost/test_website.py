"""Transport-level recall: reaching a company website that does not serve HTTPS.

Discovery proposes every candidate as `https://`. That is the right default and a wrong
assumption, and the difference is measurable. A live check of the hosts that failed
during a 100-company run found three Norwegian company sites that refuse :443 and answer
HTTP 200 on :80. This module pins the narrow fallback that recovers them, and — more
importantly — pins the limits on it, because the cheapest way to raise coverage in this
competition is to loosen what may be published, and that is what disqualifies entries.
"""

from __future__ import annotations

import pytest

from signalpost.fetch import TERMINAL_ERROR_KINDS
from signalpost.website import WebsiteResearcher, WebsiteResult

PAGE = "<html><head><title>Bergen Property AS</title></head><body>Bergen Property AS</body></html>"


def ok(url: str) -> object:
    from signalpost.fetch import Response

    return Response(
        ok=True,
        url=url,
        final_url=url,
        status=200,
        text=PAGE,
        body=PAGE.encode(),
        content_type="text/html",
        retrieved_at="2026-09-13T04:00:00Z",
    )


def failed(url: str, kind: str, status: int = 0) -> object:
    from signalpost.fetch import Response

    return Response(
        ok=False,
        url=url,
        final_url=url,
        status=status,
        retrieved_at="2026-09-13T04:00:00Z",
        error=f"simulated {kind}",
        error_kind=kind,
    )


class ScriptedFetcher:
    """Answers by exact URL and records every URL asked for, in order."""

    def __init__(self, script: dict[str, object]) -> None:
        self.script = script
        self.asked: list[str] = []

    def get(self, url: str, **_: object) -> object:
        self.asked.append(url)
        try:
            return self.script[url]
        except KeyError:  # pragma: no cover - a miss means the test is wrong
            raise AssertionError(f"unscripted fetch: {url}") from None


def researcher(script: dict[str, object]) -> tuple[WebsiteResearcher, ScriptedFetcher]:
    fetcher = ScriptedFetcher(script)
    return WebsiteResearcher(fetcher), fetcher  # type: ignore[arg-type]


class TestPlainHttpFallback:
    def test_a_host_that_refuses_tls_is_asked_once_on_port_80(self) -> None:
        """`bergenproperty.no`, `oceancleaning.no` and `sofiesgullogur.no` all behave
        exactly like this: connection refused on :443, HTTP 200 on :80."""
        agent, fetcher = researcher(
            {
                "https://bergenproperty.no": failed("https://bergenproperty.no", "refused"),
                "http://bergenproperty.no": ok("http://bergenproperty.no"),
            }
        )
        response, url = agent._fetch_landing("https://bergenproperty.no", WebsiteResult())
        assert response.ok
        assert url == "http://bergenproperty.no"
        assert fetcher.asked == ["https://bergenproperty.no", "http://bergenproperty.no"]

    def test_an_invalid_certificate_also_earns_one_plain_http_attempt(self) -> None:
        """`foodstock.no` serves a certificate that does not validate and a working site
        on :80. Refusing to look is a recall loss, not a safety win: the bytes are
        equally unauthenticated either way, and the identity gate still decides."""
        agent, fetcher = researcher(
            {
                "https://foodstock.no": failed("https://foodstock.no", "tls"),
                "http://foodstock.no": ok("http://foodstock.no"),
            }
        )
        response, url = agent._fetch_landing("https://foodstock.no", WebsiteResult())
        assert response.ok and url.startswith("http://")

    def test_the_downgrade_is_recorded_rather_than_hidden(self) -> None:
        """A reviewer must be able to see that a fact came over an unauthenticated
        transport. The note says so, and the returned URL keeps the `http://` scheme
        instead of being cosmetically rewritten to `https://`."""
        result = WebsiteResult()
        agent, _ = researcher(
            {
                "https://bergenproperty.no": failed("https://bergenproperty.no", "refused"),
                "http://bergenproperty.no": ok("http://bergenproperty.no"),
            }
        )
        _, url = agent._fetch_landing("https://bergenproperty.no", result)
        assert url.startswith("http://")
        assert any("plain HTTP" in note for note in result.notes)

    @pytest.mark.parametrize("kind, status", [("timeout", 0), ("http_status", 404), ("network", 0)])
    def test_an_answer_about_the_site_is_not_retried_on_another_port(
        self, kind: str, status: int
    ) -> None:
        """A timeout, a 404 and a reset are answers about the site. Re-asking on :80
        spends request budget from a locked 2,000 to learn what we already know."""
        agent, fetcher = researcher(
            {"https://hellemyr.no": failed("https://hellemyr.no", kind, status)}
        )
        response, url = agent._fetch_landing("https://hellemyr.no", WebsiteResult())
        assert not response.ok
        assert url == "https://hellemyr.no"
        assert fetcher.asked == ["https://hellemyr.no"]

    def test_a_failing_fallback_reports_the_original_failure(self) -> None:
        """`hellemyr.no` refuses both ports. The recorded reason must be the real one,
        not the artifact of our own retry."""
        agent, _ = researcher(
            {
                "https://hellemyr.no": failed("https://hellemyr.no", "refused"),
                "http://hellemyr.no": failed("http://hellemyr.no", "refused"),
            }
        )
        response, url = agent._fetch_landing("https://hellemyr.no", WebsiteResult())
        assert not response.ok
        assert url == "https://hellemyr.no"
        assert response.url == "https://hellemyr.no"

    def test_a_successful_https_fetch_never_costs_a_second_request(self) -> None:
        agent, fetcher = researcher({"https://lyse.no": ok("https://lyse.no")})
        response, url = agent._fetch_landing("https://lyse.no", WebsiteResult())
        assert response.ok and url == "https://lyse.no"
        assert fetcher.asked == ["https://lyse.no"]

    def test_a_candidate_already_on_http_is_not_downgraded_again(self) -> None:
        agent, fetcher = researcher({"http://x.no": failed("http://x.no", "refused")})
        agent._fetch_landing("http://x.no", WebsiteResult())
        assert fetcher.asked == ["http://x.no"]


class TestTerminalFailures:
    def test_a_closed_port_and_an_invalid_certificate_are_not_retried(self) -> None:
        """Both are settled answers. Retrying them is a request spent for no
        information, and the run has exactly 2,000."""
        assert "refused" in TERMINAL_ERROR_KINDS
        assert "tls" in TERMINAL_ERROR_KINDS

    def test_a_transient_network_error_stays_retryable(self) -> None:
        """`network` covers resets, DNS hiccups and — observed once in a measured run —
        a Windows ephemeral source port landing in an administered exclusion range,
        which produced `PermissionError: [WinError 10013]` on a host that was reachable
        a minute later. That is worth one more attempt."""
        assert "network" not in TERMINAL_ERROR_KINDS
        assert "timeout" not in TERMINAL_ERROR_KINDS


class TestSocialProfileEmission:
    """End to end through claim emission: only accounts that pass the gate are published."""

    FOOTER = (
        "<html><head><title>Perfect Watch AS</title></head><body>"
        "<p>Perfect Watch AS</p>"
        '<a href="https://www.instagram.com/perfectwatch.no">Instagram</a>'
        '<a href="https://www.facebook.com/AxiomThemes">Theme by AxiomThemes</a>'
        '<a href="https://www.youtube.com/channel/UCBjqhuwKj3MfE3B6Hg2oA8Q">YouTube</a>'
        "</body></html>"
    )

    def test_theme_vendor_and_opaque_channel_are_not_published(self) -> None:
        from signalpost.fetch import Response
        from signalpost.identity import CompanyIdentity, IdentityResolver

        url = "https://perfectwatch.no/"
        response = Response(
            ok=True, url=url, final_url=url, status=200, text=self.FOOTER,
            body=self.FOOTER.encode(), content_type="text/html",
            retrieved_at="2026-09-13T04:00:00Z",
        )
        ident = CompanyIdentity(organisation_number="928654796", legal_name="PERFECT WATCH AS")
        site, _ = researcher({})
        visit = site._page(response, "landing")
        result = WebsiteResult()
        result.verdict = IdentityResolver(ident).assess_website(url, page_text="Org.nr. 928 654 796")
        result.verified_url = url
        site._emit_claims("928654796", ident, [visit], result)

        published = [c.value["url"] for c in result.claims if c.field == "social_profile"]
        assert published == ["https://www.instagram.com/perfectwatch.no"]
        assert all("handle" not in c.value for c in result.claims if c.field == "social_profile")
        assert any("AxiomThemes" in note for note in result.notes)


class TestCorroboration:
    """An ambiguous landing page may be settled by the site's own contact page, and only so."""

    LANDING = (
        "<html><head><title>Arctic Mascot - start</title></head><body>"
        "<p>Arctic Mascot lager maskoter.</p>"
        '<a href="https://arcticmascot.no/kontakt">Kontakt oss</a>'
        '<a href="https://arcticmascot.no/produkter">Produkter</a>'
        "</body></html>"
    )

    @staticmethod
    def page(url: str, html: str) -> object:
        from signalpost.fetch import Response

        return Response(
            ok=True, url=url, final_url=url, status=200, text=html, body=html.encode(),
            content_type="text/html", retrieved_at="2026-09-13T04:00:00Z",
        )

    def run(self, contact_html: str) -> tuple[object, object, ScriptedFetcher]:
        from signalpost.budget import Budget
        from signalpost.identity import CompanyIdentity, IdentityResolver

        ident = CompanyIdentity(organisation_number="911876760", legal_name="ARCTIC MASCOT AS")
        resolver = IdentityResolver(ident)
        site, fetcher = researcher(
            {"https://arcticmascot.no/kontakt": self.page("https://arcticmascot.no/kontakt", contact_html)}
        )
        fetcher.budget = Budget()  # type: ignore[attr-defined]
        landing = site._page(self.page("https://arcticmascot.no/", self.LANDING), "landing")
        first = resolver.assess_website(
            "https://arcticmascot.no/", page_text=landing.text, page_title="Arctic Mascot - start"
        )
        assert first.decision == "ambiguous"
        result = WebsiteResult()
        verdict = site._corroborate(landing, "Arctic Mascot - start", first, resolver, False, result)
        return verdict, result, fetcher

    def test_the_organisation_number_on_the_contact_page_publishes(self) -> None:
        verdict, result, fetcher = self.run(
            "<html><body>Arctic Mascot AS, org.nr. 911 876 760</body></html>"
        )
        assert verdict.publishable
        assert "corroborated on https://arcticmascot.no/kontakt" in verdict.reason
        assert [page.kind for page in result.corroboration] == ["contact"]
        assert fetcher.asked == ["https://arcticmascot.no/kontakt"]

    def test_a_contact_page_without_new_evidence_leaves_it_ambiguous(self) -> None:
        verdict, result, _ = self.run("<html><body>Skriv til oss.</body></html>")
        assert verdict.decision == "ambiguous"
        assert not result.corroboration

    def test_a_contact_page_naming_another_company_is_a_refusal(self) -> None:
        verdict, result, _ = self.run("<html><body>Levert av Nalanadi Group AS, org.nr. 937 181 981</body></html>")
        assert verdict.decision == "rejected"
        assert not result.corroboration

    def test_only_contact_and_about_pages_are_read(self) -> None:
        _, _, fetcher = self.run("<html><body>Skriv til oss.</body></html>")
        assert "https://arcticmascot.no/produkter" not in fetcher.asked


class TestCitedSnapshots:
    """A website claim cites its corroborating pages, so their snapshots must be stored.

    The 1,000-company run found 2 of 186 verified websites whose identity evidence
    pointed at a snapshot the envelope did not contain: the crawl had fetched the
    corroborating URL a second time, the page rendered differently, and URL-keyed
    deduplication kept only the crawl's copy.
    """

    @staticmethod
    def visit(url: str, digest: str) -> object:
        from signalpost.fetch import Response
        from signalpost.models import Snapshot
        from signalpost.website import PageVisit

        snapshot = Snapshot(
            id=f"snap-{digest}", requested_url=url, final_url=url, status=200,
            content_type="text/html", bytes_received=10, content_sha256=digest,
            retrieved_at="2026-09-14T09:23:05Z", source_class="company_owned",
        )
        response = Response(
            ok=True, url=url, final_url=url, status=200, text="<html></html>",
            body=b"<html></html>", content_type="text/html",
            retrieved_at="2026-09-14T09:23:05Z", snapshot=snapshot,
        )
        return PageVisit(url=url, kind="about", response=response)

    def test_a_refetched_corroboration_page_keeps_both_snapshots(self) -> None:
        crawled = self.visit("https://www.axessgroup.com/who-we-are", "aaa")
        corroborating = self.visit("https://www.axessgroup.com/who-we-are/", "bbb")
        kept = WebsiteResearcher._cited_snapshots([crawled], [corroborating])
        assert [s.id for s in kept] == ["snap-aaa", "snap-bbb"]

    def test_the_same_snapshot_is_stored_once(self) -> None:
        page = self.visit("https://arcticmascot.no/kontakt", "ccc")
        kept = WebsiteResearcher._cited_snapshots([page], [page])
        assert [s.id for s in kept] == ["snap-ccc"]
