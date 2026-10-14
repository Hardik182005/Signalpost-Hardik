"""Identity resolution: the component that decides whether a source is this company.

The external precision gate is 95% exact-entity, so a false positive here is far more
expensive than a miss. These tests pin both directions: the gate must publish on real
evidence and must refuse on a near-miss.
"""

from __future__ import annotations

import pytest

from signalpost.identity import (
    CompanyIdentity,
    IdentityResolver,
    distinctive_tokens,
    find_org_numbers,
    name_tokens,
    normalise_name,
    valid_org_number,
)


def identity(**overrides) -> CompanyIdentity:
    base = dict(
        organisation_number="918432639",
        legal_name="JACTUS MEDICAL SERVICES AS",
    )
    base.update(overrides)
    return CompanyIdentity(**base)


class TestNormalisation:
    @pytest.mark.parametrize(
        "upper, mixed",
        [
            ("SCHRØDER EIENDOM AS", "Schrøder Eiendom AS"),
            ("HÅKULL SHIPPING AS", "Håkull Shipping AS"),
            ("MÆLAND & SØNNER AS", "Mæland & Sønner"),
            ("TROMSØ AUTOMOBILCOMPANI", "Tromsø Automobilcompani"),
            ("BLÅ KORS ØST AS", "Blå Kors Øst"),
        ],
    )
    def test_case_does_not_change_the_result(self, upper: str, mixed: str) -> None:
        """The registry publishes upper case; websites use mixed case.

        Regression: "Ø" and "Æ" are indivisible letters that NFKD does not decompose,
        so a transliteration running before casefold mapped only the lowercase forms.
        10.71% of the eligible universe has a name affected by this.
        """
        assert normalise_name(upper) == normalise_name(mixed)
        assert name_tokens(upper) == name_tokens(mixed)

    def test_nordic_letters_transliterate(self) -> None:
        assert normalise_name("SCHRØDER EIENDOM AS") == "schroder eiendom"
        assert normalise_name("MÆLAND & SØNNER AS") == "maeland sonner"
        assert normalise_name("BLÅ KORS ØST AS") == "bla kors ost"

    def test_legal_forms_are_stripped_from_both_ends(self) -> None:
        assert normalise_name("AUTOBJØRN A/S") == "autobjorn"
        assert normalise_name("AS TROMSØ AUTOMOBILCOMPANI") == "tromso automobilcompani"
        assert normalise_name("MAKSIMER AS") == "maksimer"

    def test_real_words_that_look_like_legal_forms_survive(self) -> None:
        """"da", "ba" and "sa" begin real company names and must not be stripped."""
        assert normalise_name("DA VINCI DESIGN AS") == "da vinci design"
        assert normalise_name("SA BYGG AS") == "sa bygg"
        assert normalise_name("ASKO NORGE AS") == "asko norge"

    def test_generic_tokens_are_not_distinctive(self) -> None:
        assert distinctive_tokens("NORGE EIENDOM AS") == set()
        assert "schroder" in distinctive_tokens("SCHRØDER EIENDOM AS")
        assert "eiendom" not in distinctive_tokens("SCHRØDER EIENDOM AS")


class TestOrganisationNumbers:
    @pytest.mark.parametrize("number", ["918432639", "986270027", "979498047", "911876760"])
    def test_real_numbers_pass_mod11(self, number: str) -> None:
        assert valid_org_number(number)

    @pytest.mark.parametrize(
        "number",
        [
            "918432630",  # wrong check digit
            "12345678",  # too short
            "1234567890",  # too long
            "",
            "abcdefghi",
            "000000000",
        ],
    )
    def test_invalid_numbers_are_rejected(self, number: str) -> None:
        assert not valid_org_number(number)

    def test_nine_digit_runs_that_are_not_org_numbers_are_ignored(self) -> None:
        """A phone number or invoice reference must not be read as an entity id."""
        assert find_org_numbers("Ring oss på 123 456 780 i dag") == set()

    def test_formatted_numbers_are_found(self) -> None:
        assert "918432639" in find_org_numbers("Org.nr. 918 432 639")
        assert "918432639" in find_org_numbers("Organisasjonsnummer: 918.432.639")


class TestWebsiteGate:
    def test_registry_declared_site_publishes(self) -> None:
        ident = identity(registry_website="https://jactus.no")
        verdict = IdentityResolver(ident).assess_website(
            "https://jactus.no", page_text="Velkommen", from_registry=True
        )
        assert verdict.publishable
        assert verdict.decision == "published"

    def test_organisation_number_on_page_publishes(self) -> None:
        verdict = IdentityResolver(identity()).assess_website(
            "https://example.no",
            page_text="Kontakt oss. Org.nr. 918 432 639. Alta.",
        )
        assert verdict.publishable

    def test_domain_token_alone_does_not_publish(self) -> None:
        """A guessed domain containing a name token is one weak signal, not proof.

        jactusmedical.no resolves and contains "jactus", but is a parking page owned
        by whoever registered it. Publishing on this alone would breach the precision
        gate.
        """
        verdict = IdentityResolver(identity()).assess_website(
            "https://jactusmedical.no", page_text="This website is under construction"
        )
        assert not verdict.publishable

    def test_a_different_entity_on_the_page_is_a_conflict(self) -> None:
        """Stating another company's number is a reason to refuse, not a missing signal."""
        verdict = IdentityResolver(identity()).assess_website(
            "https://jactusmedical.no",
            page_text="Jactus Medical Services. Org.nr. 986 270 027.",
        )
        assert not verdict.publishable
        assert verdict.conflicts

    def test_supporting_signals_must_pair_up(self) -> None:
        ident = identity(postal_codes={"9515"}, cities={"alta"})
        resolver = IdentityResolver(ident)
        weak = resolver.assess_website("https://example.no", page_text="Vi holder til i Alta.")
        assert not weak.publishable

        strong = resolver.assess_website(
            "https://example.no",
            page_text="JACTUS MEDICAL SERVICES AS, Skipper Wirkolas vei 4, 9515 Alta",
        )
        assert strong.confidence > weak.confidence

    def test_uppercase_registry_name_matches_mixed_case_page(self) -> None:
        """The regression that motivated the normalisation fix, at the gate level."""
        ident = identity(legal_name="SCHRØDER EIENDOM AS", postal_codes={"0150"})
        verdict = IdentityResolver(ident).assess_website(
            "https://example.no",
            page_text="Schrøder Eiendom AS er et eiendomsselskap i Oslo. Postnr 0150.",
        )
        names = {signal.name for signal in verdict.signals}
        assert "exact_legal_name" in names


class TestNameIsOneFact:
    """The measured wrong-company publication, and the rule that now refuses it.

    In a live 100-company run GLØR AS (Mo i Rana) was published with glor.no, the site of
    GLØR IKS, on "3 independent supporting signals": the name in the text, the name in
    the title and the name in the domain. The phone, emails and description of the other
    entity followed it into the profile. The page text and title below are the measured
    shape of that page, reduced to what the gate reads.
    """

    def test_a_page_naming_the_stem_under_another_legal_form_is_refused(self) -> None:
        ident = identity(legal_name="GLØR AS", cities={"mo i rana"})
        verdict = IdentityResolver(ident).assess_website(
            "https://glor.no/",
            page_title="GLØR IKS",
            page_text="GLØR IKS tar hand om avfall og kildesortering for kommunene.",
        )
        assert verdict.decision == "rejected"
        assert any("different legal entity" in item for item in verdict.conflicts)

    def test_the_name_repeated_in_text_title_and_domain_does_not_publish(self) -> None:
        ident = identity(legal_name="ARCTIC MASCOT AS")
        verdict = IdentityResolver(ident).assess_website(
            "https://arcticmascot.no/",
            page_title="Arctic Mascot - start",
            page_text="Arctic Mascot lager maskoter til idrettslag.",
        )
        names = {signal.name for signal in verdict.signals}
        assert {"name_in_text", "name_in_page_title", "distinctive_name_in_domain"} <= names
        assert not verdict.publishable
        assert verdict.decision == "ambiguous"

    def test_the_registered_name_with_its_form_is_a_second_group(self) -> None:
        ident = identity(legal_name="RUBBER INNOVATION AS")
        verdict = IdentityResolver(ident).assess_website(
            "https://rubberinnovation.no/",
            page_title="Rubber Innovation",
            page_text="Rubber Innovation AS utvikler gummiprodukter for industrien.",
        )
        assert verdict.publishable
        assert "registered_name" in verdict.reason

    def test_a_slash_written_form_counts_as_the_registered_form(self) -> None:
        ident = identity(legal_name="FREKHAUG BILSENTER AS", cities={"frekhaug"})
        verdict = IdentityResolver(ident).assess_website(
            "https://essofrekhaug.no/",
            page_title="Frekhaug Bilsenter A/S – Esso Frekhaug",
            page_text="Velkommen til Esso Frekhaug.",
        )
        names = {signal.name for signal in verdict.signals}
        assert {"exact_legal_name", "city"} <= names
        assert verdict.publishable

    def test_the_own_form_on_the_page_outweighs_a_mention_of_another(self) -> None:
        ident = identity(legal_name="GLØR AS")
        verdict = IdentityResolver(ident).assess_website(
            "https://example.no/",
            page_text="GLØR AS leverer tjenester til GLØR IKS.",
        )
        assert not verdict.conflicts

    def test_a_norwegian_word_that_is_also_a_form_is_not_a_conflict(self) -> None:
        """"sa" is "said"; "Glør sa at ..." is not a statement about a legal form."""
        ident = identity(legal_name="GLØR AS")
        verdict = IdentityResolver(ident).assess_website(
            "https://example.no/", page_text="Glør sa at de ville komme tilbake."
        )
        assert not verdict.conflicts

    def test_an_asa_written_as_as_is_not_a_different_entity(self) -> None:
        ident = identity(legal_name="EQUINOR ASA")
        verdict = IdentityResolver(ident).assess_website(
            "https://example.no/", page_text="Equinor AS er et energiselskap."
        )
        assert not verdict.conflicts

    def test_an_address_on_the_site_own_domain_is_not_evidence(self) -> None:
        """Nearly every site lists an address on its own domain; that is not ownership."""
        ident = identity(legal_name="ARCTIC MASCOT AS")
        verdict = IdentityResolver(ident).assess_website(
            "https://arcticmascot.no/",
            page_title="Arctic Mascot",
            page_text="Arctic Mascot. Kontakt post@arcticmascot.no",
            emails=["post@arcticmascot.no"],
        )
        assert "email_domain" not in {signal.name for signal in verdict.signals}
        assert not verdict.publishable

    def test_the_registered_email_listed_on_the_page_is_evidence(self) -> None:
        ident = identity(legal_name="ARCTIC MASCOT AS", emails={"post@arcticmascot.no"})
        verdict = IdentityResolver(ident).assess_website(
            "https://arcticmascot.no/",
            page_title="Arctic Mascot",
            page_text="Arctic Mascot. Kontakt post@arcticmascot.no",
            emails=["post@arcticmascot.no"],
        )
        assert "email_domain" in {signal.name for signal in verdict.signals}
        assert verdict.publishable


class TestProfileGate:
    """Social accounts linked from a verified site, measured cases from a live run."""

    def test_a_link_from_the_site_alone_does_not_publish(self) -> None:
        """perfectwatch.no carried its WordPress theme vendor in the footer."""
        verdict = IdentityResolver(identity(legal_name="PERFECT WATCH AS")).assess_profile(
            "https://www.facebook.com/AxiomThemes",
            linked_from_site=True,
            handle="AxiomThemes",
            website_domain="perfectwatch.no",
        )
        assert not verdict.publishable

    def test_a_linked_handle_that_names_the_company_publishes(self) -> None:
        verdict = IdentityResolver(identity(legal_name="GULLBRING TRENING AS")).assess_profile(
            "https://www.instagram.com/gullbring_trening",
            linked_from_site=True,
            handle="gullbring_trening",
            website_domain="gullbringtrening.no",
        )
        assert verdict.publishable

    def test_a_linked_handle_that_names_the_website_brand_publishes(self) -> None:
        """IVRIG HANDEL AS trades as superbra.no; its accounts are named for the brand."""
        verdict = IdentityResolver(identity(legal_name="IVRIG HANDEL AS")).assess_profile(
            "https://www.facebook.com/Superbravarer",
            linked_from_site=True,
            handle="Superbravarer",
            website_domain="superbra.no",
        )
        assert verdict.publishable

    def test_a_handle_naming_another_legal_form_is_refused(self) -> None:
        verdict = IdentityResolver(identity(legal_name="GLØR AS")).assess_profile(
            "https://www.linkedin.com/company/gl%C3%B8r-iks",
            linked_from_site=True,
            handle="glør-iks",
            website_domain="glor.no",
        )
        assert verdict.decision == "rejected"

    def test_a_matching_handle_without_the_link_does_not_publish(self) -> None:
        verdict = IdentityResolver(identity(legal_name="GULLBRING TRENING AS")).assess_profile(
            "https://www.instagram.com/gullbring_trening", handle="gullbring_trening"
        )
        assert not verdict.publishable
