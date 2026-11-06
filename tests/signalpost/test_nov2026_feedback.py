"""Nov 2026 feedback: revenue comparison, and independent news kept apart from activity."""
from signalpost.budget import Budget
from signalpost.fetch import Response
from signalpost.identity import CompanyIdentity
from signalpost.models import Claim, Snapshot
from signalpost.news import NewsResearcher, RateGate, mentions_company, search_phrase
from signalpost.synthesis import build_brief, revenue_comparison

ORG = "979498047"


def revenue(value, year, *, scope="company", currency="NOK", key=None):
    return Claim(claim_key=key or f"{ORG}:revenue:{year}:{scope}", field="revenue", family="financials",
                 value=value, availability="available", organisation_number=ORG,
                 reporting_period=f"{year}-01-01/{year}-12-31", scope=scope, currency=currency, unit="absolute")


def test_consecutive_years_give_absolute_and_percent_change():
    result = revenue_comparison([revenue(1_100_000, 2025), revenue(1_000_000, 2024)])
    assert result["status"] == "compared"
    assert result["absolute_change"] == 100_000 and result["percent_change"] == 10.0
    sentence = next(s for s in build_brief(ORG, [revenue(1_100_000, 2025), revenue(1_000_000, 2024)]).sentences
                    if s.topic == "revenue_change")
    assert "up 100,000 NOK (10.0%)" in sentence.text and "2024" in sentence.text
    assert len(sentence.claim_keys) == 2


def test_group_figure_never_stands_in_for_missing_company_year():
    result = revenue_comparison([revenue(500, 2025), revenue(9_000, 2024, scope="group")])
    assert result["status"] == "unknown" and result["previous"] is None


def test_currency_mismatch_is_not_compared():
    assert revenue_comparison([revenue(500, 2025), revenue(400, 2024, currency="EUR")])["status"] == "unknown"


def test_zero_previous_revenue_gives_no_percentage():
    result = revenue_comparison([revenue(500, 2025), revenue(0, 2024)])
    assert result["status"] == "compared" and result["percent_change"] is None
    assert result["absolute_change"] == 500


def test_no_revenue_is_unknown_not_zero_and_serialised():
    brief = build_brief(ORG, [])
    assert brief.to_dict()["revenue_comparison"] == {"status": "unknown", "reason": "no filed revenue figure was found"}
    assert not any(s.topic == "revenue_change" for s in brief.sentences)


def test_search_phrase_drops_legal_form_and_refuses_generic_names():
    assert search_phrase("KONGSBERG GRUPPEN ASA") == "KONGSBERG GRUPPEN"
    assert search_phrase("AS NORGE") is None
    assert search_phrase("ABC AS") is None


def test_article_must_name_exact_entity_or_add_context():
    identity = CompanyIdentity(ORG, "FJORD ROBOTICS AS", cities={"bergen"})
    assert mentions_company("Fjord Robotics AS opened a lab.", identity, None)[0]
    assert mentions_company("Fjord Robotics in Bergen raised money.", identity, None)[0]
    assert not mentions_company("Fjord Robotics raised money.", identity, None)[0]
    assert not mentions_company("Fjord Robotics IKS raised money in Bergen.", identity, None)[0]


def test_one_word_name_needs_context_even_with_legal_form():
    identity = CompanyIdentity(ORG, "GLOR AS", cities={"oslo"})
    assert not mentions_company("Glor AS won a contract.", identity, None)[0]
    assert mentions_company(f"Glor AS (org.nr {ORG}) won a contract.", identity, None)[0]


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.budget = Budget(max_requests=100, max_seconds=100)
        self.offline = False
        self.asked = []

    def get(self, url, **kwargs):
        self.asked.append(url)
        return self.pages.get(url) or Response(ok=False, url=url, final_url=url, status=404, error="missing")


def html_page(url, text):
    snapshot = Snapshot(id="snap-" + url[-6:], requested_url=url, final_url=url, status=200,
                        content_type="text/html", bytes_received=len(text), content_sha256="x",
                        retrieved_at="2026-11-06T00:00:00Z", source_class="permitted_public")
    return Response(ok=True, url=url, final_url=url, status=200, text=text, body=text.encode(),
                    content_type="text/html", snapshot=snapshot)


def test_news_publishes_only_confirmed_independent_articles():
    identity = CompanyIdentity(ORG, "FJORD ROBOTICS AS", cities={"bergen"})
    articles = [
        {"url": "https://fjordrobotics.no/news/1", "title": "Fjord Robotics news", "seendate": "20261001T100000Z"},
        {"url": "https://paper.no/a", "title": "Fjord Robotics expands", "seendate": "20261002T100000Z"},
        {"url": "https://other.no/b", "title": "Fjord Robotics IKS", "seendate": "20261003T100000Z"},
    ]
    import json

    class Search(FakeFetcher):
        def get(self, url, **kwargs):
            if "api.gdeltproject.org" in url:
                body = json.dumps({"articles": articles})
                return Response(ok=True, url=url, final_url=url, status=200, text=body, body=body.encode())
            return super().get(url, **kwargs)

    fetcher = Search({
        "https://paper.no/a": html_page("https://paper.no/a", "<p>Fjord Robotics AS hires 20 people.</p>"),
        "https://other.no/b": html_page("https://other.no/b", "<p>Fjord Robotics IKS in Bergen.</p>"),
    })
    result = NewsResearcher(fetcher, gate=RateGate(0)).research(ORG, identity, "https://fjordrobotics.no/")
    published = [c for c in result.claims if c.field == "news_mention"]
    assert [c.value["url"] for c in published] == ["https://paper.no/a"]
    assert published[0].family == "news" and published[0].evidence_ids == [result.evidence[0].id]
    assert not any("fjordrobotics.no" in url for url in fetcher.asked[1:])


def test_news_rate_limit_text_is_a_failure_not_an_empty_result():
    class Limited(FakeFetcher):
        def get(self, url, **kwargs):
            text = "Please limit requests to one every 5 seconds"
            return Response(ok=True, url=url, final_url=url, status=200, text=text, body=text.encode())

    result = NewsResearcher(Limited({}), gate=RateGate(0)).research(ORG, CompanyIdentity(ORG, "FJORD ROBOTICS AS"))
    assert result.claims[0].availability == "failed" and "no JSON" in result.claims[0].reason


def test_busy_gate_skips_instead_of_stalling():
    gate = RateGate(60)
    assert gate.acquire(0)
    result = NewsResearcher(FakeFetcher({}), gate=gate, max_wait=1).research(ORG, CompanyIdentity(ORG, "FJORD ROBOTICS AS"))
    assert result.claims[0].availability == "not_available" and "rate limit" in result.claims[0].reason


def test_same_as_on_organisation_yields_profiles_but_not_person_or_share_links():
    from signalpost.extract.fields import extract_social_same_as

    blocks = [
        {"_syntax": "json-ld", "_index": 0, "@type": "Organization",
         "sameAs": ["https://www.linkedin.com/company/fjord-robotics", "https://www.facebook.com/sharer.php?u=x",
                    "https://www.linkedin.com/in/some-person"]},
        {"_syntax": "json-ld", "_index": 1, "@type": "Person", "sameAs": "https://www.instagram.com/someone"},
    ]
    found = extract_social_same_as(blocks, "https://fjordrobotics.no/")
    assert [c.value["url"] for c in found] == ["https://www.linkedin.com/company/fjord-robotics"]
    assert found[0].locator_value == "/json-ld/0/sameAs/0" and found[0].method == "schema_org_sameAs"


def test_agency_credit_link_is_not_published_as_company_profile():
    from signalpost.identity import IdentityResolver

    resolver = IdentityResolver(CompanyIdentity(ORG, "FJORD ROBOTICS AS"))
    assert not resolver.assess_profile("https://www.facebook.com/AxiomThemes", linked_from_site=True,
                                       handle="AxiomThemes", website_domain="fjordrobotics.no").publishable
    assert resolver.assess_profile("https://www.facebook.com/fjordrobotics", linked_from_site=True,
                                   handle="fjordrobotics", website_domain="fjordrobotics.no").publishable


def test_http_429_backs_off_the_shared_gate():
    class TooMany(FakeFetcher):
        def get(self, url, **kwargs):
            return Response(ok=False, url=url, final_url=url, status=429, error="HTTP 429")

    gate = RateGate(0)
    first = NewsResearcher(TooMany({}), gate=gate, max_wait=1).research(ORG, CompanyIdentity(ORG, "FJORD ROBOTICS AS"))
    assert first.claims[0].availability == "failed" and "429" in first.claims[0].reason
    second = NewsResearcher(TooMany({}), gate=gate, max_wait=1).research(ORG, CompanyIdentity(ORG, "FJORD ROBOTICS AS"))
    assert "rate limit busy" in second.claims[0].reason
