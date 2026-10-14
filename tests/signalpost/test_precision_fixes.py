"""Precision fixes measured on the 1,000-company run of 2026-09-14.

Every negative case below was published as a fact in that run: navigation buttons and staff
listings as vacancies, footer text as a city. Every positive case is a real value from a
real careers page or contact block that must survive the stricter rules.
"""

from __future__ import annotations

import pytest

from signalpost.extract.fields import _looks_like_job_title, extract_addresses, postal_town
from signalpost.models import Claim
from signalpost.synthesis import build_brief

ORG = "979498047"


@pytest.mark.parametrize(
    "label",
    [
        "Se ledige stillinger",
        "Abonner på ledige stillinger",
        "Ingen ledige stillinger",
        "- Ledige stillinger",
        "Ledige stillinger ->",
        "Se ledige stillinger ved vår skole",
        "Merete Kaspersen HR-rådgiver +47 77 62 90 17",
        "Kevin Strandenes Chief Software Architect",
        "Saber Derouiche Senior Developer",
        "Fast månedslønn i 100% stilling + provisjon på salg",
        "6. The 2nd Interview - Department Specialist",
        "Landets ledende rådgiver innen næringseiendom",
        "Selger du mye, men tjener lite? Dette tallet kan være grunnen",
    ],
)
def test_published_non_vacancies_are_rejected(label: str) -> None:
    assert not _looks_like_job_title(label)


@pytest.mark.parametrize(
    "label",
    [
        "Ledig stilling for Tømrer i Bergen",
        "Sivilingeniør/ingeniør elektro",
        "Helsefagarbeidar Fjaler sjukeheim, 100 % fast stilling",
        "Senior Software Engineer",
        "Prosjektleder elektro",
        "Lead Developer Oslo",
    ],
)
def test_real_vacancies_survive(label: str) -> None:
    assert _looks_like_job_title(label)


def test_an_address_is_published_only_with_its_own_post_town() -> None:
    text = (
        "Besøk oss: Edvard Griegs vei 3C, 5059 Bergen Norge. Kontor 0181 Oslo Norge Maksimer AS. "
        "6016 Powered by Salgsbetingelser."
    )
    found = {item.value["postal_code"]: item.value["city"] for item in extract_addresses(text)}
    assert found == {"5059": "BERGEN", "0181": "OSLO"}


def test_the_postcode_table_is_loaded() -> None:
    assert postal_town("5059") == "BERGEN"
    assert postal_town("0000") is None


def claim(field: str, value: object, **extra: object) -> Claim:
    key = f"{ORG}:{field}:{extra.pop('discriminator', '')}".rstrip(":")
    return Claim(
        claim_key=key,
        field=field,
        family=extra.pop("family", "legal_identity"),
        value=value,
        availability="available",
        organisation_number=ORG,
        evidence_ids=["ev-1"],
        **extra,  # type: ignore[arg-type]
    )


class TestSummary:
    def claims(self) -> list[Claim]:
        period = "2025-01-01/2025-12-31"
        return [
            claim("legal_name", "MAKSIMER AS"),
            claim("legal_form_code", "AS"),
            claim("incorporated_at", "1997-12-11"),
            claim("role", {"role_code": "DAGL", "holder": "Ola Nordmann"}, family="people", discriminator="dagl"),
            claim("role", {"role_code": "LEDE", "holder": "Ola Nordmann"}, family="people", discriminator="lede"),
            claim("role", {"role_code": "MEDL", "holder": "Ola Nordmann"}, family="people", discriminator="medl1"),
            claim("role", {"role_code": "MEDL", "holder": "Kari Nordmann"}, family="people", discriminator="medl2"),
            claim("revenue", 100.0, family="financials", reporting_period=period, discriminator="2025"),
            claim("operating_profit", 10.0, family="financials", reporting_period=period, discriminator="2025"),
            claim("total_equity", 40.0, family="financials", reporting_period=period, discriminator="2025"),
            claim("total_assets", 80.0, family="financials", reporting_period=period, discriminator="2025"),
        ]

    def test_leadership_names_one_person_once_and_counts_other_board_members(self) -> None:
        text = build_brief(ORG, self.claims()).summary
        assert "Ola Nordmann (chief executive and chair of the board)" in text
        assert "1 further board member" in text

    def test_derived_ratios_say_they_are_calculated_and_cite_their_inputs(self) -> None:
        brief = build_brief(ORG, self.claims())
        sentence = next(item for item in brief.sentences if item.topic == "financials")
        assert "operating margin of 10.0%" in sentence.text and "equity ratio of 50%" in sentence.text
        assert "calculated from those figures" in sentence.text
        assert f"{ORG}:total_equity:2025" in sentence.claim_keys

    def test_unknowns_are_stated_and_no_hiring_answer_is_invented_without_a_website(self) -> None:
        brief = build_brief(ORG, self.claims())
        text = brief.summary
        assert "Not established from permitted sources" in text
        assert "whether it is hiring" in text
        assert "No open roles" not in text

    def test_the_changes_sentence_reports_the_baseline_state(self) -> None:
        first = build_brief(ORG, self.claims(), refresh={"compared_against": None}, changes=[])
        assert "first observation" in first.summary
        later = build_brief(
            ORG,
            self.claims(),
            refresh={"compared_against": "run-1"},
            changes=[{"claim_key": f"{ORG}:revenue:2025", "field": "revenue", "change_type": "value_changed", "material": True}],
        )
        assert "1 material change (changed revenue)" in later.summary
