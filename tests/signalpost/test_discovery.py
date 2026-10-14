"""Website candidate discovery.

Discovery decides what the agent spends its request budget on, and a careless
candidate is how a company gets attached to someone else's website. These tests run
without network access: `discover(resolve=False)` skips DNS so the ranking and the
safety filters are pinned independently of what happens to be registered today.
"""

from __future__ import annotations

import pytest

from signalpost.discovery import (
    domain_stems,
    name_tokens_for_domain,
    propose,
    to_ascii_host,
)
from signalpost.identity import CompanyIdentity


def identity(name: str, *, website: str | None = None, emails: set[str] | None = None):
    return CompanyIdentity(
        organisation_number="918432639",
        legal_name=name,
        registry_website=website,
        emails=emails or set(),
    )


class TestIdnaEncoding:
    @pytest.mark.parametrize(
        "unicode_host, punycode",
        [
            ("schrødereiendom.no", "xn--schrdereiendom-tqb.no"),
            ("håkull.no", "xn--hkull-mra.no"),
            ("blåkors.no", "xn--blkors-jua.no"),
        ],
    )
    def test_accented_hosts_become_punycode(self, unicode_host: str, punycode: str) -> None:
        """A raw Unicode host reaching getaddrinfo fails to resolve; punycode does not."""
        assert to_ascii_host(unicode_host) == punycode

    def test_ascii_hosts_pass_through_unchanged(self) -> None:
        assert to_ascii_host("example.no") == "example.no"
        assert to_ascii_host("WWW.Example.NO") == "www.example.no"

    def test_unencodable_hosts_are_dropped(self) -> None:
        assert to_ascii_host("") is None
        assert to_ascii_host("..") is None


class TestNameVariants:
    def test_both_transliterated_and_accented_forms_are_generated(self) -> None:
        """A company may have registered either spelling; guessing one is a coin flip."""
        stems = {stem for stem, _, _ in domain_stems(identity("SCHRØDER EIENDOM AS"))}
        assert "schrodereiendom" in stems
        assert "schrødereiendom" in stems

    def test_accented_variant_keeps_the_ring_on_a(self) -> None:
        """NFKD would strip it and collapse the accented variant into the ASCII one."""
        assert name_tokens_for_domain("HÅKULL AS", ascii_only=False) == ["håkull"]
        assert name_tokens_for_domain("HÅKULL AS", ascii_only=True) == ["hakull"]

    def test_hyphenated_and_generic_stripped_forms_are_offered(self) -> None:
        stems = {stem for stem, _, _ in domain_stems(identity("MELVA VARME OG MULTISERVICE AS"))}
        assert "melvavarmeogmultiservice" in stems
        assert "melva-varme-og-multiservice" in stems


class TestPrecisionGuards:
    def test_a_wholly_generic_name_yields_no_single_token_guess(self) -> None:
        """"eiendom.no" and "norge.no" belong to whoever registered them."""
        stems = {stem for stem, _, _ in domain_stems(identity("NORGE EIENDOM AS"))}
        assert "norge" not in stems
        assert "eiendom" not in stems

    @pytest.mark.parametrize(
        "mailbox",
        ["post@gmail.com", "firma@hotmail.no", "kontakt@online.no", "a@outlook.com"],
    )
    def test_free_mailboxes_never_become_candidates(self, mailbox: str) -> None:
        """Otherwise every company on Gmail gets attached to google.com."""
        urls = [item.url for item in propose(identity("ACME AS", emails={mailbox}))]
        assert not any("gmail" in url or "hotmail" in url or "online.no" in url for url in urls)
        assert not any("outlook" in url for url in urls)

    def test_a_company_mailbox_domain_is_a_strong_candidate(self) -> None:
        candidates = propose(identity("JACTUS MEDICAL SERVICES AS", emails={"epost@jactus.no"}))
        assert candidates[0].url == "https://jactus.no"
        assert candidates[0].source == "email_domain"

    def test_registry_site_outranks_every_guess(self) -> None:
        candidates = propose(
            identity("ALPHA ØKONOMI AS", website="alpha-as.no", emails={"post@alpha.no"})
        )
        assert candidates[0].source == "registry"

    @pytest.mark.parametrize(
        "website, forbidden",
        [
            ("http://localhost", "localhost"),
            ("https://127.0.0.1", "127.0.0.1"),
            ("https://169.254.169.254", "169.254.169.254"),
            ("ftp://example.no", "example.no"),
            ("https://user:pass@example.no", "example.no"),
        ],
    )
    def test_an_unsafe_registry_url_is_dropped_not_proposed(
        self, website: str, forbidden: str
    ) -> None:
        """The registry field is attacker-influenced free text, so it goes through
        the same SSRF validation as any other URL. Name guesses may still be offered;
        what must never survive is the unsafe host itself."""
        candidates = propose(identity("ACME AS", website=website))
        assert not any(item.source == "registry" for item in candidates)
        assert not any(forbidden in item.url for item in candidates)

    def test_candidates_are_unique_per_registrable_domain(self) -> None:
        candidates = propose(identity("MAKSIMER AS", website="https://www.maksimer.no"))
        domains = [item.host for item in candidates]
        assert len(domains) == len(set(domains))

    def test_priors_are_ordering_only_and_never_publish(self) -> None:
        """Nothing in discovery may assert a site belongs to the company."""
        for candidate in propose(identity("MAKSIMER AS")):
            assert 0.0 <= candidate.prior <= 1.0
            assert candidate.rationale
