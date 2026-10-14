"""URL safety: SSRF defence, redaction and canonicalisation.

The agent fetches URLs derived from untrusted pages and from guessed domains, so every
one of them is a potential request at an internal address. These tests pin the refusals
rather than the acceptances, because a gap here is a security defect and not merely a
missed claim.
"""

from __future__ import annotations

import pytest

from signalpost.net import (
    address_is_public,
    canonical_url,
    redact_headers,
    redact_url,
    registrable_domain,
    same_site,
    validate_url,
)


class TestSchemeAndCredentials:
    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "ftp://example.no/pub",
            "gopher://example.no",
            "javascript:alert(1)",
            "data:text/html,<h1>x</h1>",
            "jar:http://example.no!/",
            "",
            "   ",
        ],
    )
    def test_non_http_schemes_are_refused(self, url: str) -> None:
        assert not validate_url(url, resolve=False).allowed

    @pytest.mark.parametrize(
        "url",
        [
            "https://user:pass@example.no/",
            "https://admin@169.254.169.254/",
            "https://user@example.no/",
        ],
    )
    def test_embedded_credentials_are_refused(self, url: str) -> None:
        assert not validate_url(url, resolve=False).allowed

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.no:22/",
            "https://example.no:8080/",
            "https://example.no:0/",
        ],
    )
    def test_non_web_ports_are_refused(self, url: str) -> None:
        assert not validate_url(url, resolve=False).allowed

    @pytest.mark.parametrize("url", ["https://exa mple.no/", "https://example.no/\nHost: x"])
    def test_whitespace_and_control_characters_are_refused(self, url: str) -> None:
        assert not validate_url(url, resolve=False).allowed


class TestPrivateAddresses:
    @pytest.mark.parametrize(
        "address",
        [
            "127.0.0.1",
            "127.9.9.9",
            "0.0.0.0",
            "10.0.0.5",
            "172.16.4.4",
            "192.168.1.1",
            "169.254.169.254",  # cloud instance metadata
            "100.64.0.1",  # carrier-grade NAT
            "::1",
            "fe80::1",  # link-local
            "fc00::1",  # unique local
            "::ffff:127.0.0.1",  # IPv4-mapped loopback
            "::ffff:169.254.169.254",  # IPv4-mapped metadata
            "2002:7f00:0001::",  # 6to4-wrapped loopback
        ],
    )
    def test_internal_addresses_are_refused(self, address: str) -> None:
        allowed, reason = address_is_public(address)
        assert not allowed, f"{address} was allowed"
        assert reason

    @pytest.mark.parametrize("address", ["8.8.8.8", "1.1.1.1", "2001:4860:4860::8888"])
    def test_public_addresses_are_allowed(self, address: str) -> None:
        allowed, _ = address_is_public(address)
        assert allowed

    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost/",
            "http://metadata.google.internal/",
            "http://127.0.0.1/",
            "http://[::1]/",
            "http://169.254.169.254/latest/meta-data/",
        ],
    )
    def test_internal_hosts_are_refused_by_name_or_literal(self, url: str) -> None:
        assert not validate_url(url, resolve=False).allowed


class TestRedaction:
    def test_sensitive_query_parameters_are_removed(self) -> None:
        redacted = redact_url("https://example.no/x?api_key=SECRET123&token=abc&page=2")
        assert "SECRET123" not in redacted
        assert "abc" not in redacted
        assert "page=2" in redacted

    def test_sensitive_headers_are_removed(self) -> None:
        redacted = redact_headers(
            {"Authorization": "Bearer sk-live-123", "Cookie": "sid=xyz", "Accept": "text/html"}
        )
        assert "sk-live-123" not in str(redacted)
        assert "xyz" not in str(redacted)
        assert redacted.get("Accept") == "text/html"


class TestCanonicalisation:
    def test_tracking_parameters_do_not_change_the_canonical_form(self) -> None:
        """Refresh compares canonical URLs; a campaign tag is not a change."""
        plain = canonical_url("https://example.no/om-oss")
        tagged = canonical_url("https://example.no/om-oss?utm_source=newsletter&utm_medium=email")
        assert plain == tagged

    def test_a_wordpress_cron_buster_is_not_part_of_a_company_website(self) -> None:
        """Regression from a measured run.

        `mastermat.no` was published as
        `https://mastermat.no/?doing_wp_cron=1789309421.0442390441894531250000`, and the
        next day's fetch reported a material `value_changed` because WordPress had put a
        fresh float in the parameter. Two defects in one artifact: a wrong answer to
        "what is this company's website", and exactly the false change the refresh hard
        gate forbids.
        """
        volatile = canonical_url(
            "https://mastermat.no/?doing_wp_cron=1789309421.0442390441894531250000"
        )
        later = canonical_url("https://mastermat.no/?doing_wp_cron=1789309999.1234567890")
        assert volatile == later == "https://mastermat.no/"

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.no/?PHPSESSID=deadbeef",
            "https://example.no/?jsessionid=deadbeef",
            "https://example.no/kontakt;jsessionid=deadbeef",
            "https://example.no/kontakt;CFID=99;CFTOKEN=deadbeef",
        ],
    )
    def test_a_session_identifier_never_survives_into_a_published_url(self, url: str) -> None:
        """A session token picked up from a redirect must not be written into a claim,
        a snapshot filename or an evidence record. Java and ColdFusion put it in a path
        parameter rather than the query string, so the query filter alone is not enough."""
        assert "deadbeef" not in canonical_url(url)

    def test_meaningful_query_parameters_are_kept(self) -> None:
        """The stripping is a named list, not a policy of discarding query strings —
        `?side=2` is how a lot of Norwegian sites paginate their job listings."""
        assert canonical_url("https://example.no/ledige-stillinger?side=2") == (
            "https://example.no/ledige-stillinger?side=2"
        )

    def test_fragment_and_case_are_normalised(self) -> None:
        assert canonical_url("HTTPS://Example.NO/Om-Oss#kontakt") == canonical_url(
            "https://example.no/Om-Oss"
        )

    def test_registrable_domain_ignores_subdomains(self) -> None:
        assert registrable_domain("https://www.example.no/x") == registrable_domain(
            "https://shop.example.no/y"
        )

    def test_same_site_distinguishes_different_companies(self) -> None:
        assert same_site("https://example.no/a", "https://www.example.no/b")
        assert not same_site("https://example.no", "https://example-annet.no")
