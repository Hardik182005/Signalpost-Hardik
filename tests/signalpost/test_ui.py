"""Web interface over a run's JSONL: explorer, profile, changes, runs and evidence.

The interface is read-only over run output, so it is tested through HTTP against a
small synthetic fixture (tests/fixtures/ui-sample*.jsonl) rather than a live run. The
fixture carries a hostile company name, scraped markup and non-http(s) links on
purpose: escaping and link refusal are properties of the rendered page, so they are
asserted on the page and not only on the helper that is meant to guarantee them.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from signalpost.ui.app import AppConfig, create_app  # noqa: E402
from signalpost.ui.formatting import safe_url  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SAMPLE = FIXTURES / "ui-sample.jsonl"
REFRESH = FIXTURES / "ui-sample-refresh.jsonl"
REPORT = FIXTURES / "ui-sample-report.json"

FULL = "910000004"  # Testfjord Data AS: every section populated
HOSTILE = "910000012"  # markup in the name, description and an evidence excerpt
LINKS = "910000020"  # javascript:, data: and credential-bearing URLs
FAILED = "910000039"  # failed, nameless, almost no claims
ZERO_EMPLOYEES = "910000047"  # Fyllstoff 01 AS: employees 0, available
UNKNOWN_EMPLOYEES = "910000055"  # Fyllstoff 02 AS: employees not_available

HOSTILE_NAME = '<script>alert("owned")</script> & <b>Bold</b> AS'


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def envelope(path: Path, org: str) -> dict:
    return next(item for item in read_jsonl(path) if item["organisation_number"] == org)


def listed(html: str) -> set[str]:
    return set(re.findall(r'href="/companies/(\d{9})"', html))


def api_orgs(client: TestClient, query: str) -> set[str]:
    response = client.get(f"/api/companies?{query}")
    assert response.status_code == 200, response.text
    return {item["organisation_number"] for item in response.json()["items"]}


def make_client(tmp_path: Path, input_path: Path) -> TestClient:
    config = AppConfig(input_path=input_path, scratch_dir=tmp_path / "ui-scratch")
    return TestClient(create_app(config))


@pytest.fixture
def client(tmp_path: Path):
    with make_client(tmp_path, SAMPLE) as test_client:
        yield test_client


@pytest.fixture
def refresh_client(tmp_path: Path):
    with make_client(tmp_path, REFRESH) as test_client:
        yield test_client


# --- explorer ------------------------------------------------------------------------


def test_explorer_paginates_on_the_server(client: TestClient) -> None:
    first = client.get("/api/companies?page_size=10").json()
    second = client.get("/api/companies?page_size=10&page=2").json()

    assert (first["total"], first["pages"], len(first["items"])) == (13, 2, 10)
    assert (second["page"], len(second["items"])) == (2, 3)
    everyone = {item["organisation_number"] for item in first["items"] + second["items"]}
    assert everyone == {item["organisation_number"] for item in read_jsonl(SAMPLE)}

    page_one = client.get("/companies?page_size=10")
    page_two = client.get("/companies?page_size=10&page=2")
    assert page_one.status_code == page_two.status_code == 200
    assert "Page 1 of 2 &middot; showing 1&ndash;10 of 13" in page_one.text
    assert "Page 2 of 2 &middot; showing 11&ndash;13 of 13" in page_two.text
    assert len(listed(page_one.text)) == 10
    assert listed(page_one.text).isdisjoint(listed(page_two.text))


def test_root_redirects_to_the_explorer(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code in {302, 303, 307}
    assert response.headers["location"].endswith("/companies")


@pytest.mark.parametrize(
    "query, message",
    [
        ("page_size=7", "page_size must be one of: 10, 25, 50, 100"),
        ("status=bogus", "Unknown terminal status"),
        ("employees_min=abc", "employees_min must be a whole number"),
        ("employees_min=10&employees_max=2", "employees_min cannot be greater than employees_max"),
        ("sort=secret", "Cannot sort by"),
        ("availability=available", "needs a field family"),
    ],
)
def test_explorer_rejects_invalid_queries(client: TestClient, query: str, message: str) -> None:
    page = client.get(f"/companies?{query}")
    api = client.get(f"/api/companies?{query}")
    assert page.status_code == 400 and message in page.text
    assert api.status_code == 400 and message in api.json()["error"]


def test_search_by_name_is_case_insensitive_and_by_org_number(client: TestClient) -> None:
    assert api_orgs(client, "q=testfjord") == {FULL}
    assert api_orgs(client, "q=TESTFJORD") == {FULL}
    assert api_orgs(client, f"q={FULL}") == {FULL}
    assert listed(client.get("/companies?q=Testfjord").text) >= {FULL}


def test_search_with_no_match_says_so(client: TestClient) -> None:
    response = client.get("/companies?q=no-such-company-anywhere")
    assert response.status_code == 200
    assert client.get("/api/companies?q=no-such-company-anywhere").json()["total"] == 0
    assert "No company in this run matches those filters." in response.text


def test_filters_by_city_status_and_employees(client: TestClient) -> None:
    assert api_orgs(client, "city=BERGEN") == {FULL, "910000071", "910000128"}
    assert api_orgs(client, "status=completed") == {FULL, ZERO_EMPLOYEES, "910000071", "910000128"}

    with_staff = api_orgs(client, "employees_min=1&page_size=25")
    assert len(with_staff) == 8
    # Zero is a known value below the bound; not_available is unknown and must not be
    # silently treated as zero or as matching.
    assert ZERO_EMPLOYEES not in with_staff
    assert UNKNOWN_EMPLOYEES not in with_staff
    assert ZERO_EMPLOYEES in api_orgs(client, "employees_max=0")
    assert UNKNOWN_EMPLOYEES not in api_orgs(client, "employees_max=0")

    page = client.get("/companies?city=BERGEN")
    assert listed(page.text) >= {FULL, "910000071", "910000128"}
    assert "showing 1&ndash;3 of 3" in page.text


def test_unknown_is_shown_distinctly_from_zero(client: TestClient) -> None:
    items = {
        item["organisation_number"]: item
        for item in client.get("/api/companies?page_size=25").json()["items"]
    }
    assert items[ZERO_EMPLOYEES]["employees"] == {"value": 0, "availability": "available"}
    assert items[UNKNOWN_EMPLOYEES]["employees"] == {"value": None, "availability": "not_available"}

    html = client.get("/companies?q=Fyllstoff&page_size=10").text

    def row(org: str) -> str:
        anchor = html.index(f'href="/companies/{org}"')
        return html[html.rindex("<tr", 0, anchor) : html.index("</tr>", anchor)]

    assert '<td class="num">0</td>' in row(ZERO_EMPLOYEES)
    assert '<td class="num">0</td>' not in row(UNKNOWN_EMPLOYEES)
    assert "not available" in row(UNKNOWN_EMPLOYEES)
    assert 'class="unknown"' in row(UNKNOWN_EMPLOYEES)


def test_compare_accepts_two_or_three_companies(client: TestClient) -> None:
    response = client.get(f"/compare?org={FULL}&org={ZERO_EMPLOYEES}")
    assert response.status_code == 200
    assert "Testfjord Data AS" in response.text and "Fyllstoff 01 AS" in response.text
    three = f"/compare?org={FULL}&org={ZERO_EMPLOYEES}&org={UNKNOWN_EMPLOYEES}"
    assert client.get(three).status_code == 200

    assert client.get(f"/compare?org={FULL}").status_code == 400
    four = "&".join(f"org={org}" for org in (FULL, ZERO_EMPLOYEES, UNKNOWN_EMPLOYEES, "910000063"))
    assert client.get(f"/compare?{four}").status_code == 400
    assert client.get(f"/compare?org={FULL}&org=999999999").status_code == 404


# --- profile and evidence ------------------------------------------------------------


def test_profile_renders_every_section_with_periods_currency_and_evidence(client: TestClient) -> None:
    response = client.get(f"/companies/{FULL}")
    assert response.status_code == 200
    html = response.text

    for text in ("Testfjord Data AS", "Kari Testesen", "BERGEN", "NOK", "2024", "2025", "62.010"):
        assert text in html, text
    assert re.search(r'<a [^>]*href="https://testfjord\.example/"[^>]*rel="noopener noreferrer"', html)

    # Every evidence record behind the profile is reachable from the page, down to the
    # snapshot hash that proves what was read.
    data = envelope(SAMPLE, FULL)
    for record in data["evidence"]:
        assert record["id"] in html, record["id"]
    for snapshot in data["snapshots"]:
        assert snapshot["content_sha256"] in html
    assert "Snapshot hash" in html

    job_postings = next(claim for claim in data["claims"] if claim["field"] == "job_postings")
    assert job_postings["availability"] == "not_available"
    assert job_postings["reason"] in html


def test_profile_never_drops_a_published_claim(tmp_path: Path) -> None:
    # A hiring signal, and a field no section knows about, must both appear on the
    # profile with their evidence rather than vanish.
    data = envelope(SAMPLE, FULL)
    template = dict(data["evidence"][0])
    for field_name, value in (("is_hiring", True), ("future_field", "a later engine field")):
        evidence_id = f"ev-{field_name}-0000"
        data["evidence"].append({**template, "id": evidence_id})
        data["claims"].append(
            {
                "claim_key": f"{FULL}:{field_name}",
                "organisation_number": FULL,
                "field": field_name,
                "family": "jobs",
                "value": value,
                "availability": "available",
                "evidence_ids": [evidence_id],
            }
        )
    path = tmp_path / "extra.jsonl"
    path.write_text(json.dumps(data) + "\n", encoding="utf-8")

    with make_client(tmp_path, path) as test_client:
        html = test_client.get(f"/companies/{FULL}").text
    for claim in data["claims"]:
        for evidence_id in claim.get("evidence_ids") or []:
            assert evidence_id in html, (claim["field"], evidence_id)
    assert "Hiring signal on the website" in html
    assert "Other published claims" in html and "a later engine field" in html


def test_sparse_failed_profile_marks_what_was_not_recorded(client: TestClient) -> None:
    response = client.get(f"/companies/{FAILED}")
    assert response.status_code == 200
    assert "Not recorded" in response.text
    assert "Failed" in response.text


def test_profile_for_an_unknown_or_malformed_company_is_404(client: TestClient) -> None:
    assert client.get("/companies/999999999").status_code == 404
    assert client.get("/companies/12345").status_code == 404
    assert client.get("/api/companies/999999999").status_code == 404


def test_claim_evidence_resolves_to_locator_url_time_and_hash(client: TestClient) -> None:
    key = f"{FULL}:revenue:2024"
    response = client.get(f"/api/claim?org={FULL}&key={key}")
    assert response.status_code == 200
    payload = response.json()

    assert payload["claim"]["value"] == 12_500_000
    assert payload["claim"]["currency"] == "NOK"
    assert payload["evidence"]
    assert [item["id"] for item in payload["evidence"]] == payload["claim"]["evidence_ids"]
    for item in payload["evidence"]:
        assert item["source_url"] and item["safe_link"] == item["source_url"]
        assert item["retrieved_at"]
        assert re.fullmatch(r"[0-9a-f]{64}", item["content_sha256"])
        assert item["locator"]["kind"] and item["locator"]["value"]


def test_claim_evidence_for_a_missing_claim_or_company_is_404(client: TestClient) -> None:
    assert client.get(f"/api/claim?org={FULL}&key={FULL}:nope").status_code == 404
    assert client.get("/api/claim?org=999999999&key=x").status_code == 404


def test_evidence_inspector_lists_resolved_records(client: TestClient) -> None:
    response = client.get(f"/evidence?org={FULL}")
    assert response.status_code == 200
    for snapshot in envelope(SAMPLE, FULL)["snapshots"]:
        assert snapshot["content_sha256"] in response.text
    assert client.get("/evidence?org=999999999").status_code == 404
    assert client.get("/evidence?availability=bogus").status_code == 400


# --- changes -------------------------------------------------------------------------


def test_changes_empty_state_is_truthful(client: TestClient) -> None:
    overview = client.get("/changes")
    assert overview.status_code == 200
    assert "No changes recorded in this run" in overview.text

    company = client.get(f"/changes/{FULL}")
    assert company.status_code == 200
    assert "No change events for this company" in company.text
    assert "Value changed" not in company.text
    assert client.get("/changes/999999999").status_code == 404


def test_changes_show_before_and_after_with_evidence_from_both_runs(refresh_client: TestClient) -> None:
    overview = refresh_client.get("/changes")
    assert overview.status_code == 200
    assert "No changes recorded in this run" not in overview.text
    assert "ui-sample" in overview.text

    response = refresh_client.get(f"/changes/{FULL}")
    assert response.status_code == 200
    html = response.text
    assert "Value changed" in html
    assert "Testfjord Data AS" in html  # before, from the baseline run
    assert "Testfjord Digital AS" in html  # after, from this run

    before = envelope(SAMPLE, FULL)
    after = envelope(REFRESH, FULL)
    before_ids = next(c["evidence_ids"] for c in before["claims"] if c["field"] == "legal_name")
    after_ids = next(c["evidence_ids"] for c in after["claims"] if c["field"] == "legal_name")
    for evidence_id in before_ids + after_ids:
        assert evidence_id in html, evidence_id


def test_company_compared_without_changes_says_every_claim_matched(refresh_client: TestClient) -> None:
    response = refresh_client.get(f"/changes/{ZERO_EMPLOYEES}")
    assert response.status_code == 200
    assert "No change events for this company" in response.text
    assert "every claim matched" in response.text


# --- runs and downloads --------------------------------------------------------------


def test_runs_come_from_the_report_file(client: TestClient) -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    runs = client.get("/runs")
    assert runs.status_code == 200
    assert 'href="/runs/ui-sample"' in runs.text
    assert f'{report["budget"]["requests_used"]}<span class="meta">' in runs.text

    detail = client.get("/runs/ui-sample")
    assert detail.status_code == 200
    assert client.get("/runs/not-a-run").status_code == 404

    download = client.get("/download/run/ui-sample.json")
    assert download.status_code == 200
    assert download.json() == report


def test_companies_csv_download_respects_filters(client: TestClient) -> None:
    response = client.get("/download/companies.csv?city=BERGEN")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert {row["organisation_number"] for row in rows} == {FULL, "910000071", "910000128"}


def test_claims_and_evidence_csv_downloads_are_complete(client: TestClient) -> None:
    claims = client.get(f"/download/claims.csv?q={FULL}")
    assert claims.status_code == 200 and claims.headers["content-type"].startswith("text/csv")
    claim_rows = list(csv.DictReader(io.StringIO(claims.text)))
    assert len(claim_rows) == len(envelope(SAMPLE, FULL)["claims"])

    evidence = client.get("/download/evidence.csv")
    assert evidence.status_code == 200 and "attachment" in evidence.headers["content-disposition"]
    evidence_rows = list(csv.DictReader(io.StringIO(evidence.text)))
    assert len(evidence_rows) == sum(len(item["evidence"]) for item in read_jsonl(SAMPLE))


def test_jsonl_download_round_trips_the_loaded_envelopes(client: TestClient) -> None:
    response = client.get("/download/envelopes.jsonl")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert "attachment" in response.headers["content-disposition"]
    downloaded = [json.loads(line) for line in response.text.splitlines() if line.strip()]

    def by_org(items: list[dict]) -> list[dict]:
        return sorted(items, key=lambda item: item["organisation_number"])

    assert by_org(downloaded) == by_org(read_jsonl(SAMPLE))


# --- safe rendering ------------------------------------------------------------------


def test_hostile_company_name_and_scraped_markup_are_escaped(client: TestClient) -> None:
    pages = [
        client.get(f"/companies?q={HOSTILE}").text,
        client.get(f"/companies/{HOSTILE}").text,
        client.get(f"/evidence?org={HOSTILE}").text,
        client.get(f"/compare?org={HOSTILE}&org={FULL}").text,
        client.get(f"/changes/{HOSTILE}").text,
    ]
    for html in pages:
        assert "<script>alert" not in html
        assert "<b>Bold</b>" not in html
        assert "<img src=x" not in html
        assert "<iframe" not in html
    assert "&lt;script&gt;alert(&#34;owned&#34;)&lt;/script&gt; &amp; &lt;b&gt;Bold&lt;/b&gt; AS" in pages[1]
    assert "&lt;img src=x onerror=" in pages[1]
    # The raw value survives in machine-readable output; escaping is a rendering concern.
    assert client.get(f"/api/companies?q={HOSTILE}").json()["items"][0]["name"] == HOSTILE_NAME


def test_non_http_links_are_shown_as_text_never_as_links(client: TestClient) -> None:
    for path in (f"/companies/{LINKS}", f"/evidence?org={LINKS}", f"/companies?q={LINKS}"):
        html = client.get(path).text
        hrefs = re.findall(r'href="([^"]*)"', html)
        assert not [href for href in hrefs if href.lower().startswith(("javascript:", "data:"))], path
        assert not [href for href in hrefs if "secret@" in href], path
        for tag in re.findall(r'<a [^>]*href="https?://[^>]*>', html):
            assert 'rel="noopener noreferrer"' in tag, tag

    profile = client.get(f"/companies/{LINKS}").text
    assert "javascript:alert(document.cookie)" in profile  # visible, as text
    assert "not an http(s) link" in profile

    website = client.get(f"/api/claim?org={LINKS}&key={LINKS}:website").json()
    assert [item["safe_link"] for item in website["evidence"]] == [None]
    social = client.get(f"/api/claim?org={LINKS}&key={LINKS}:social_profile").json()
    assert [item["safe_link"] for item in social["evidence"]] == [None]


@pytest.mark.parametrize(
    "value, expected",
    [
        ("https://example.no/om-oss", "https://example.no/om-oss"),
        ("http://example.no/", "http://example.no/"),
        ("javascript:alert(1)", None),
        ("JAVASCRIPT:alert(1)", None),
        ("data:text/html,<script>alert(1)</script>", None),
        ("file:///etc/passwd", None),
        ("ftp://example.no/", None),
        ("//example.no/", None),
        ("https://user:secret@example.no/", None),
        ('https://example.no/"onmouseover="x', None),
        ("https://example.no/\nSet-Cookie: x", None),
        ("https://" + "a" * 2100 + ".no/", None),
        ("", None),
        (None, None),
        (42, None),
    ],
)
def test_safe_url_allows_only_plain_http_and_https(value: object, expected: str | None) -> None:
    assert safe_url(value) == expected


# --- read-only guarantees ------------------------------------------------------------


def test_research_is_disabled_by_default_and_writes_nothing(tmp_path: Path) -> None:
    with make_client(tmp_path, SAMPLE) as test_client:
        disabled = test_client.post("/research", data={"organisation_number": "910000152"})
        assert disabled.status_code == 403
        assert "disabled" in disabled.text.lower()
        assert test_client.post("/research", data={"organisation_number": "123456789"}).status_code == 400
        known = test_client.post("/research", data={"organisation_number": FULL}, follow_redirects=False)
        assert known.status_code == 303 and known.headers["location"] == f"/companies/{FULL}"
    assert not (tmp_path / "ui-scratch").exists()


def test_browsing_every_area_leaves_the_input_files_untouched(tmp_path: Path) -> None:
    def digest() -> dict[str, str]:
        return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (SAMPLE, REFRESH, REPORT)}

    before = digest()
    with make_client(tmp_path, REFRESH) as test_client:
        for path in (
            "/companies",
            f"/companies/{FULL}",
            "/changes",
            f"/changes/{FULL}",
            "/runs",
            "/runs/ui-sample",
            "/evidence",
            "/download/companies.csv",
            "/download/claims.csv",
            "/download/evidence.csv",
            "/download/envelopes.jsonl",
            "/health",
        ):
            assert test_client.get(path).status_code == 200, path
    assert digest() == before
    assert list(tmp_path.iterdir()) == []
