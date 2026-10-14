"""NAV vacancies: published only when NAV itself names the company as employer.

The cache says where to look; it is never evidence. Each vacancy is re-read from NAV
during the run, and is published only if that read says the ad is still active and the
employer organisation number is the company's or one of its registered workplaces'.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

from signalpost.budget import Budget
from signalpost.fetch import Response
from signalpost.models import Snapshot
from signalpost.nav_jobs import FEED_BASE, TOKEN_URL, NavJobIndex, NavJobsResearcher

ORG = "979498047"
WORKPLACE = "998808316"
TOKEN = "eyJhbGciOiJIUzI1NiJ9.e30.signature"


def json_response(url: str, body: object, status: int = 200) -> Response:
    payload = json.dumps(body).encode()
    digest = hashlib.sha256(payload).hexdigest()
    return Response(
        ok=200 <= status < 300,
        url=url,
        final_url=url,
        status=status,
        body=payload,
        text=payload.decode(),
        content_type="application/json",
        content_sha256=digest,
        retrieved_at="2026-10-13T20:00:00Z",
        snapshot=Snapshot(
            id=f"snap-{digest[:12]}",
            requested_url=url,
            final_url=url,
            status=status,
            content_type="application/json",
            bytes_received=len(payload),
            content_sha256=digest,
            retrieved_at="2026-10-13T20:00:00Z",
            source_class="official_job_register",
        ),
    )


def detail(uuid: str, orgnr: str, *, status: str = "ACTIVE", title: str = "Frontend-utvikler") -> dict:
    return {
        "uuid": uuid,
        "status": status,
        "ad_content": {
            "title": title,
            "published": "2026-10-01T08:00:00+02:00",
            "expires": "2026-10-31T00:00:00+01:00",
            "link": f"https://arbeidsplassen.nav.no/stillinger/stilling/{uuid}",
            "workLocations": [{"city": "BERGEN"}],
            "employer": {"name": "MAKSIMER AS", "orgnr": orgnr, "homepage": "https://maksimer.no"},
        },
    }


class ScriptedFetcher:
    def __init__(self, script: dict[str, Response]) -> None:
        self.script = script
        self.asked: list[str] = []
        self.budget = Budget(max_requests=1000, max_seconds=600)

    def get(self, url: str, **_: object) -> Response:
        self.asked.append(url)
        if url not in self.script:
            raise AssertionError(f"unscripted fetch: {url}")
        return self.script[url]


def write_index(tmp_path: Path, ads: dict[str, dict]) -> Path:
    path = tmp_path / "nav.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump({"cursor_page_id": "page-1", "built_at": "2026-10-13T00:00:00Z", "ads": ads}, handle)
    return path


def feed_end() -> dict:
    return {"id": "page-1", "items": [], "next_id": None, "next_url": None}


def run(tmp_path: Path, ads: dict[str, dict], details: dict[str, dict], feed: dict | None = None):
    script = {
        TOKEN_URL: Response(ok=True, url=TOKEN_URL, final_url=TOKEN_URL, status=200, text=f"token: {TOKEN}"),
        f"{FEED_BASE}/api/v1/feed/page-1": json_response(f"{FEED_BASE}/api/v1/feed/page-1", feed or feed_end()),
    }
    for uuid, body in details.items():
        url = f"{FEED_BASE}/api/v1/feedentry/{uuid}"
        script[url] = json_response(url, body)
    fetcher = ScriptedFetcher(script)
    index = NavJobIndex(fetcher, write_index(tmp_path, ads))  # type: ignore[arg-type]
    agent = NavJobsResearcher(fetcher, index)  # type: ignore[arg-type]
    return agent.research(ORG, {ORG, WORKPLACE}, ["MAKSIMER AS"]), fetcher


def test_an_ad_naming_a_registered_workplace_is_published_with_fresh_evidence(tmp_path: Path) -> None:
    result, fetcher = run(
        tmp_path,
        {"ad-1": {"status": "ACTIVE", "business": "Maksimer", "title": "Frontend-utvikler", "orgnr": WORKPLACE}},
        {"ad-1": detail("ad-1", WORKPLACE)},
    )
    assert [claim.value["title"] for claim in result.claims] == ["Frontend-utvikler"]
    claim = result.claims[0]
    assert claim.family == "jobs" and claim.scope == "subunit"
    assert claim.value["employer_organisation_number"] == WORKPLACE
    # The evidence is the detail read during this run, not the cache.
    assert result.evidence[0].source_url == f"{FEED_BASE}/api/v1/feedentry/ad-1"
    assert claim.evidence_ids == [result.evidence[0].id]
    assert result.homepages == ["https://maksimer.no"]
    assert f"{FEED_BASE}/api/v1/feedentry/ad-1" in fetcher.asked


def test_an_ad_whose_employer_number_differs_is_not_published(tmp_path: Path) -> None:
    """A cached number can be stale; the run-time read decides."""
    result, _ = run(
        tmp_path,
        {"ad-1": {"status": "ACTIVE", "business": "Maksimer", "title": "Selger", "orgnr": ORG}},
        {"ad-1": detail("ad-1", "999999999")},
    )
    assert result.claims == []


def test_an_ad_stopped_since_the_cache_was_built_is_not_published(tmp_path: Path) -> None:
    result, _ = run(
        tmp_path,
        {"ad-1": {"status": "ACTIVE", "business": "Maksimer", "title": "Selger", "orgnr": ORG}},
        {"ad-1": detail("ad-1", ORG, status="INACTIVE")},
    )
    assert result.claims == []


def test_the_feed_refresh_drops_ads_that_became_inactive(tmp_path: Path) -> None:
    feed = {
        "id": "page-1",
        "next_id": None,
        "next_url": None,
        "items": [{"id": "ad-1", "_feed_entry": {"uuid": "ad-1", "status": "INACTIVE", "title": "Selger"}}],
    }
    result, fetcher = run(
        tmp_path,
        {"ad-1": {"status": "ACTIVE", "business": "Maksimer", "title": "Selger", "orgnr": ORG}},
        {},
        feed=feed,
    )
    assert result.claims == []
    assert not any("feedentry" in url for url in fetcher.asked)


def test_a_new_ad_is_read_only_when_its_name_matches_and_published_only_when_nav_confirms(tmp_path: Path) -> None:
    feed = {
        "id": "page-1",
        "next_id": None,
        "next_url": None,
        "items": [
            {"id": "new-1", "_feed_entry": {"uuid": "new-1", "status": "ACTIVE", "businessName": "Maksimer AS", "title": "Designer"}},
            {"id": "new-2", "_feed_entry": {"uuid": "new-2", "status": "ACTIVE", "businessName": "Annet Firma AS", "title": "Kokk"}},
        ],
    }
    result, fetcher = run(tmp_path, {}, {"new-1": detail("new-1", ORG, title="Designer")}, feed=feed)
    assert [claim.value["title"] for claim in result.claims] == ["Designer"]
    assert not any(url.endswith("new-2") for url in fetcher.asked)


def test_a_missing_index_skips_the_source_without_requests(tmp_path: Path) -> None:
    fetcher = ScriptedFetcher({})
    index = NavJobIndex(fetcher, tmp_path / "absent.json.gz")  # type: ignore[arg-type]
    result = NavJobsResearcher(fetcher, index).research(ORG, {ORG}, ["MAKSIMER AS"])  # type: ignore[arg-type]
    assert result.claims == [] and fetcher.asked == []
    assert "index unavailable" in result.detail["skipped"]
