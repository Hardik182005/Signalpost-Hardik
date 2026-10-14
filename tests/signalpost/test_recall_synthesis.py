"""Discovery must earn identity evidence; summaries must preserve scope and totals."""
from unittest.mock import patch

import pytest

from signalpost.budget import Budget
from signalpost.discovery import DiscoveryResult, Candidate, propose
from signalpost.fetch import Response
from signalpost.identity import CompanyIdentity, IdentityResolver
from signalpost.models import Claim
from signalpost.synthesis import build_brief
from signalpost.website import WebsiteResearcher, WebsiteResult

ORG = "979498047"
PERIOD = "2025-01-01/2025-12-31"


def claim(field, value, *, key=None, **kwargs):
    return Claim(claim_key=key or field, field=field, value=value, family="financials",
                 availability="available", organisation_number=ORG, **kwargs)


def financial(claims):
    return next(s for s in build_brief(ORG, claims).sentences if s.topic == "financials")


def test_registered_aliases_propose_domains_without_becoming_identity_evidence():
    identity = CompanyIdentity(ORG, "NEW HOLDING AS", former_names=["Old Trading AS"],
                               subunit_names=["Local Bakery AS"], discovery_websites=["bakerybrand.no"])
    candidates = propose(identity)
    assert {"https://oldtrading.no", "https://localbakery.no", "https://bakerybrand.no"} <= {c.url for c in candidates}
    assert not IdentityResolver(identity).assess_website("https://bakerybrand.no", page_text="Bakery Brand").publishable
    assert all(not c.from_registry for c in candidates)


def test_legal_suffix_is_kept_as_a_candidate_but_not_ranked_above_existing_guesses():
    urls = [c.url for c in propose(CompanyIdentity(ORG, "EXAMPLE AS"))]
    assert "https://exampleas.no" in urls and "https://example-as.no" in urls
    assert urls.index("https://example.no") < urls.index("https://exampleas.no")


def test_workplace_website_does_not_pollute_parent_contacts_or_authority():
    identity = CompanyIdentity.from_claims(ORG, [claim("subunit", {"organisation_number": "987654321", "name": "Brand", "website": "brand.no"})])
    assert identity.discovery_websites == ["brand.no"]
    assert identity.registry_website is None and identity.listed_websites == []


class Fetcher:
    def __init__(self, pages):
        self.pages = pages
        self.budget = Budget(max_requests=100, max_seconds=100)
        self.asked = []

    def get(self, url, **kwargs):
        self.asked.append(url)
        return self.pages[url]


def page(url, html, final_url=None):
    return Response(ok=True, url=url, final_url=final_url or url, status=200, text=html, body=html.encode())


def test_legal_page_can_supply_exact_identity_without_relaxing_gate():
    home = "https://example.no/"
    legal = home + "personvern"
    fetcher = Fetcher({home: page(home, '<title>Example</title>Example <a href="/personvern">Personvern</a>'),
                       legal: page(legal, f'Personvern. Example AS, org. nr. {ORG}')})
    agent = WebsiteResearcher(fetcher)
    identity = CompanyIdentity(ORG, "EXAMPLE AS")
    result = WebsiteResult()
    with patch("signalpost.website.discover", return_value=DiscoveryResult(candidates=[Candidate(home, "name_guess", "test")])):
        verified = agent._verify_landing(ORG, identity, result)
    assert verified is not None and verified[1].publishable
    assert result.corroboration[0].url == legal


def test_registry_redirect_to_unrelated_domain_does_not_inherit_authority():
    home = "https://example.no/"
    fetcher = Fetcher({home: page(home, "Unrelated business in another country", "https://unrelated.com/")})
    result = WebsiteResult()
    with patch("signalpost.website.discover", return_value=DiscoveryResult(candidates=[Candidate(home, "registry", "test")])):
        verified = WebsiteResearcher(fetcher)._verify_landing(ORG, CompanyIdentity(ORG, "EXAMPLE AS", registry_website=home), result)
    assert verified is None


def test_company_revenue_never_combines_with_group_profit_or_audit_status():
    sentence = financial([
        claim("revenue", 100, reporting_period=PERIOD),
        claim("operating_profit", 50, scope="group", reporting_period=PERIOD),
        claim("accounts_audited", False, scope="group", reporting_period=PERIOD),
    ])
    assert "margin" not in sentence.text and "not audited" not in sentence.text
    assert sentence.claim_keys == ["revenue"]


def test_group_only_accounts_are_labelled_as_group():
    assert "the group reported" in financial([claim("revenue", 100, scope="group", reporting_period=PERIOD)]).text


@pytest.mark.parametrize("difference", [{"currency": "EUR"}, {"unit": "thousands"}, {"reporting_period": "2024-01-01/2024-12-31"}])
def test_incomparable_figures_never_produce_ratios(difference):
    common = {"reporting_period": PERIOD, "currency": "NOK", "unit": "absolute"}
    text = financial([claim("revenue", 100, **common), claim("operating_profit", 20, **(common | difference))]).text
    assert "margin" not in text


def test_nonconsecutive_revenue_comparison_names_actual_period():
    text = financial([claim("revenue", 200, key="new", reporting_period=PERIOD),
                      claim("revenue", 100, key="old", reporting_period="2022-01-01/2022-12-31")]).text
    assert "year before" not in text and "2022-01-01/2022-12-31" in text


def test_latest_profit_does_not_disappear_behind_stale_revenue():
    sentence = financial([claim("revenue", 100, reporting_period="2022-01-01/2022-12-31"),
                          claim("net_profit", 25, reporting_period=PERIOD)])
    assert "2025" in sentence.text and sentence.claim_keys == ["net_profit"]


def test_truncated_workplace_list_uses_official_total_and_labels_subset():
    claims = [claim("subunit", {"city": "Oslo"}, key=f"unit{i}") for i in range(25)]
    claims.append(claim("subunit_count", 120))
    sentence = next(s for s in build_brief(ORG, claims).sentences if s.topic == "scale")
    assert "120 registered workplaces" in sentence.text and "25 detailed" in sentence.text
    assert set(sentence.claim_keys) == {c.claim_key for c in claims}


@pytest.mark.parametrize("field,topic", [("job_posting", "jobs"), ("activity_item", "activity")])
def test_counted_claims_are_all_cited(field, topic):
    claims = [claim(field, {"title": f"Item {i}", "published_at": "2026-10-01"}, key=f"item{i}") for i in range(8)]
    sentence = next(s for s in build_brief(ORG, claims).sentences if s.topic == topic)
    assert set(sentence.claim_keys) == {c.claim_key for c in claims}


def test_legal_notice_contacts_are_not_published_as_company_contacts():
    home = "https://example.no/"
    legal = home + "personvern"
    fetcher = Fetcher({home: page(home, '<title>Example</title>Example <a href="/personvern">Personvern</a>'),
                       legal: page(legal, f'Org. nr. {ORG}. Privacy processor <a href="mailto:officer@vendor.no">Contact DPO</a>')})
    with patch("signalpost.website.discover", return_value=DiscoveryResult(candidates=[Candidate(home, "name_guess", "test")])):
        result = WebsiteResearcher(fetcher).research(ORG, CompanyIdentity(ORG, "EXAMPLE AS"))
    assert result.verified_url == home
    assert not any(c.field == "email" and c.value == "officer@vendor.no" for c in result.claims)


@pytest.mark.parametrize("text", ["Information provided to Greystone as a result of obtaining a Financial Product", "Greystone, as a financial institution, processes data", "As Greystone expands its business"])
def test_english_as_is_not_a_norwegian_legal_entity(text):
    verdict = IdentityResolver(CompanyIdentity(ORG, "GREYSTONE AS")).assess_website(
        "https://greystone.com", page_title="Greystone", page_text=text)
    assert not verdict.publishable
    assert "exact_legal_name" not in {s.name for s in verdict.signals}


@pytest.mark.parametrize("name", ["Greystone AS", "Greystone A/S", "AS Greystone"])
def test_explicit_legal_form_is_still_recognised(name):
    verdict = IdentityResolver(CompanyIdentity(ORG, "GREYSTONE AS")).assess_website(
        "https://greystone.no", page_title="Greystone", page_text=name)
    assert verdict.publishable


def test_foreign_affiliate_in_legal_notice_is_not_sufficient_corroboration():
    home = "https://example.com/"
    legal = home + "privacy"
    fetcher = Fetcher({home: page(home, '<title>Example</title>Example <a href="/privacy">Privacy</a>'),
                       legal: page(legal, 'Our European affiliates include Example AB and Example A/S.')})
    result = WebsiteResult()
    with patch("signalpost.website.discover", return_value=DiscoveryResult(candidates=[Candidate(home, "name_guess", "test")])):
        verified = WebsiteResearcher(fetcher)._verify_landing(ORG, CompanyIdentity(ORG, "EXAMPLE AS"), result)
    assert verified is None
