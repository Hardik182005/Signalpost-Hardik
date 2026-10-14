"""Job vacancies from NAV, the Norwegian Labour and Welfare Administration.

NAV publishes the national vacancy register (arbeidsplassen.nav.no) through an open
feed API, free to use and licensed for reuse: https://navikt.github.io/pam-stilling-feed/.
Its value here is that every ad names its employer by **organisation number**. That is
the same stable legal key this agent is anchored on, so a vacancy found this way needs no
name matching to be attributed: the register says whose ad it is. No other jobs source
available to the agent gives an exact-entity match for companies without a website.

The feed is a change log, not a search index: it cannot be queried by employer, and
each entry carries only the ad's status and display name until its detail is read. So
the work is split in two:

* **A declared cache, built offline** (`scripts/signalpost/build_nav_job_index.py`):
  every ad that was active when the cache was built, with its employer's organisation
  number, plus the feed cursor it was built at. Stored in `data/nav/`.
* **A refresh at run time**: the feed is read forward from that cursor to its end, so
  ads published, stopped or expired since the cache was built are known. Every vacancy
  that is then published is re-read from its NAV detail record during the run — status
  and employer number checked, response snapshotted — so the evidence is current and
  never rests on the cache.

Ads with an employer number are matched against the company and its registered
workplaces (NAV often names the workplace, an `underenhet`, as the employer). Ads added
after the cache have no employer number until their detail is read, so only those whose
display name matches one of the company's registered names are read, and they are
published only if the detail confirms the number.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .budget import BudgetExceeded
from .extract.fields import SOCIAL_HOSTS
from .fetch import Fetcher
from .identity import normalise_name
from .models import Claim, Evidence, Locator, Snapshot, claim_key, stable_id
from .net import registrable_domain

FEED_BASE = "https://pam-stilling-feed.nav.no"
TOKEN_URL = f"{FEED_BASE}/api/publicToken"
TOKEN_ENV = "SIGNALPOST_NAV_FEED_TOKEN"
AD_PAGE = "https://arbeidsplassen.nav.no/stillinger/stilling/{uuid}"
SOURCE_CLASS = "official_job_register"

DEFAULT_INDEX = Path(__file__).resolve().parents[2] / "data" / "nav" / "nav-job-index.json.gz"

# Feed pages read at run time to bring the cache up to date. A page holds 1,000 changes;
# the feed adds a few hundred to a few thousand a day, so this covers several weeks of
# drift while bounding what one batch can spend on it.
MAX_REFRESH_PAGES = 80

# Detail reads per company. A large employer can have dozens of open ads; the profile
# needs evidence of hiring, not the full list.
MAX_ADS_PER_COMPANY = 15


@dataclass(slots=True)
class NavAd:
    uuid: str
    status: str
    business_name: str = ""
    title: str = ""
    orgnr: str | None = None


@dataclass(slots=True)
class NavJobsResult:
    claims: list[Claim] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    snapshots: list[Snapshot] = field(default_factory=list)
    homepages: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)


class NavJobIndex:
    """The cached vacancy index, brought up to date once per run and shared by all companies."""

    def __init__(self, fetcher: Fetcher, path: Path | None = None) -> None:
        self.fetcher = fetcher
        self.path = Path(path) if path else DEFAULT_INDEX
        self._lock = threading.Lock()
        self._ready = False
        self._token: str | None = None
        self.ads: dict[str, NavAd] = {}
        self.by_orgnr: dict[str, list[str]] = {}
        self.by_name: dict[str, list[str]] = {}
        self.status: dict[str, Any] = {"index": str(self.path.name), "loaded": False}

    # -- loading and refresh ----------------------------------------------------

    def ensure_ready(self) -> bool:
        with self._lock:
            if not self._ready:
                self._ready = True
                self._load()
                if self.status.get("loaded"):
                    self._refresh()
            return bool(self.status.get("loaded"))

    def _load(self) -> None:
        try:
            with gzip.open(self.path, "rt", encoding="utf-8") as handle:
                body = json.load(handle)
        except (OSError, ValueError) as exc:
            self.status.update({"loaded": False, "reason": f"index unavailable: {type(exc).__name__}"})
            return
        for uuid, row in (body.get("ads") or {}).items():
            self.ads[uuid] = NavAd(
                uuid=uuid,
                status=str(row.get("status") or "ACTIVE"),
                business_name=str(row.get("business") or ""),
                title=str(row.get("title") or ""),
                orgnr=row.get("orgnr"),
            )
        self.cursor = body.get("cursor_page_id")
        self.status.update(
            {
                "loaded": True,
                "built_at": body.get("built_at"),
                "cursor_at_build": self.cursor,
                "ads_in_index": len(self.ads),
            }
        )

    def token(self) -> str | None:
        if self._token is None:
            configured = os.environ.get(TOKEN_ENV, "").strip()
            if configured:
                self._token = configured
            else:
                response = self.fetcher.get(
                    TOKEN_URL,
                    purpose="nav_token",
                    source_class=SOURCE_CLASS,
                    capture_snapshot=False,
                )
                match = re.search(r"eyJ[A-Za-z0-9._-]+", response.text or "") if response.ok else None
                self._token = match.group(0) if match else ""
        return self._token or None

    def _refresh(self) -> None:
        """Read the feed forward from the cached cursor, applying every status change."""
        token = self.token()
        if not token or not self.cursor:
            self.status["refresh"] = "skipped: no feed token or cursor"
            self._rebuild_lookups()
            return
        page_id, pages, changes = self.cursor, 0, 0
        while page_id and pages < MAX_REFRESH_PAGES:
            if self.fetcher.budget.should_stop_discretionary():
                self.status["refresh_stopped"] = "run budget"
                break
            try:
                response = self.fetcher.get(
                    f"{FEED_BASE}/api/v1/feed/{page_id}",
                    purpose="nav_feed",
                    accept="application/json",
                    source_class=SOURCE_CLASS,
                    capture_snapshot=False,
                    headers={"Authorization": f"Bearer {token}"},
                    max_bytes=8 * 1024 * 1024,
                    timeout=60,
                )
            except BudgetExceeded:
                break
            body = response.json() if response.ok else None
            if not isinstance(body, dict):
                self.status["refresh_error"] = response.error or f"HTTP {response.status}"
                break
            pages += 1
            for item in body.get("items") or []:
                entry = item.get("_feed_entry") or {}
                uuid = str(item.get("id") or entry.get("uuid") or "")
                if not uuid:
                    continue
                known = self.ads.get(uuid)
                status = str(entry.get("status") or "")
                if known is None:
                    if status == "ACTIVE":
                        self.ads[uuid] = NavAd(
                            uuid=uuid,
                            status=status,
                            business_name=str(entry.get("businessName") or ""),
                            title=str(entry.get("title") or ""),
                        )
                        changes += 1
                else:
                    known.status = status or known.status
                    known.title = str(entry.get("title") or known.title)
                    changes += 1
            next_id = body.get("next_id")
            self.cursor = next_id or page_id
            page_id = next_id
        self.status.update({"refresh_pages": pages, "refresh_changes": changes, "cursor_now": self.cursor})
        self._rebuild_lookups()

    def _rebuild_lookups(self) -> None:
        self.by_orgnr.clear()
        self.by_name.clear()
        for ad in self.ads.values():
            if ad.status != "ACTIVE":
                continue
            if ad.orgnr:
                self.by_orgnr.setdefault(str(ad.orgnr), []).append(ad.uuid)
            elif ad.business_name:
                self.by_name.setdefault(normalise_name(ad.business_name), []).append(ad.uuid)
        self.status["active_ads"] = sum(1 for ad in self.ads.values() if ad.status == "ACTIVE")

    # -- per company ------------------------------------------------------------

    def candidates(self, numbers: set[str], names: list[str]) -> list[str]:
        found: list[str] = []
        for number in sorted(numbers):
            found.extend(self.by_orgnr.get(number, []))
        for name in names:
            key = normalise_name(name)
            if key:
                found.extend(self.by_name.get(key, []))
        unique = list(dict.fromkeys(found))
        return unique[:MAX_ADS_PER_COMPANY]


class NavJobsResearcher:
    """Publishes a company's open NAV vacancies, each re-read and verified during the run."""

    def __init__(self, fetcher: Fetcher, index: NavJobIndex | None = None) -> None:
        self.fetcher = fetcher
        self.index = index or NavJobIndex(fetcher)

    def research(self, org: str, numbers: set[str], names: list[str]) -> NavJobsResult:
        result = NavJobsResult(detail={"source": "NAV arbeidsplassen feed"})
        if not self.index.ensure_ready():
            result.detail["skipped"] = self.index.status.get("reason", "index not loaded")
            return result
        token = self.index.token()
        uuids = self.index.candidates(numbers, names)
        result.detail["candidates"] = len(uuids)
        if not uuids or not token:
            return result

        seen_titles: set[str] = set()
        for uuid in uuids:
            if self.fetcher.budget.should_stop_discretionary():
                result.detail["stopped"] = "run budget"
                break
            try:
                response = self.fetcher.get(
                    f"{FEED_BASE}/api/v1/feedentry/{uuid}",
                    purpose="nav_job_detail",
                    accept="application/json",
                    source_class=SOURCE_CLASS,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except BudgetExceeded:
                break
            body = response.json() if response.ok else None
            if not isinstance(body, dict) or response.snapshot is None:
                continue
            ad = body.get("ad_content") or {}
            employer = ad.get("employer") or {}
            employer_number = re.sub(r"\D", "", str(employer.get("orgnr") or ""))
            # The two checks that make this an exact-entity source: NAV still lists the
            # ad as active, and NAV names this company or one of its workplaces as employer.
            if str(body.get("status") or "").upper() != "ACTIVE" or employer_number not in numbers:
                continue
            title = " ".join(str(ad.get("title") or "").split())[:200]
            if not title or title.casefold() in seen_titles:
                continue
            seen_titles.add(title.casefold())
            result.snapshots.append(response.snapshot)

            homepage = str(employer.get("homepage") or "").strip()
            # Employers sometimes give an Instagram or Facebook page as their homepage;
            # that is a social profile, not a website candidate.
            if homepage and registrable_domain(homepage) not in SOCIAL_HOSTS:
                result.homepages.append(homepage)

            location = None
            for place in ad.get("workLocations") or []:
                if isinstance(place, dict) and (place.get("city") or place.get("municipal")):
                    location = str(place.get("city") or place.get("municipal")).title()
                    break
            published = str(ad.get("published") or "")[:10] or None
            evidence = Evidence(
                id=stable_id("ev", response.snapshot.id, "/ad_content/title", title),
                snapshot_id=response.snapshot.id,
                source_url=response.snapshot.final_url,
                source_class=SOURCE_CLASS,
                retrieved_at=response.snapshot.retrieved_at,
                content_sha256=response.content_sha256,
                locator=Locator(
                    kind="json_pointer",
                    value="/ad_content/title",
                    excerpt=f"{title} | employer orgnr {employer_number}",
                ),
                claim_span=f"{title} — employer {employer.get('name') or ''} ({employer_number})"[:400],
                extraction_method="nav_feed_json",
            )
            result.evidence.append(evidence)
            result.claims.append(
                Claim(
                    claim_key=claim_key(org, "job_posting", title.casefold()),
                    organisation_number=org,
                    field="job_posting",
                    family="jobs",
                    value={
                        "title": title,
                        "location": location,
                        "posted_at": published,
                        "expires_at": str(ad.get("expires") or "")[:10] or None,
                        "employment_type": str(ad.get("engagementtype") or "")[:60] or None,
                        "employer_organisation_number": employer_number,
                        "url": str(ad.get("link") or AD_PAGE.format(uuid=uuid)),
                        "source": "NAV arbeidsplassen",
                    },
                    availability="available",
                    scope="company" if employer_number == org else "subunit",
                    evidence_ids=[evidence.id],
                    confidence=0.98,
                    effective_date=published,
                    extraction_method="nav_feed_json",
                    identity_ref=f"NAV names organisation number {employer_number} as the employer",
                )
            )
        result.detail["published"] = len(result.claims)
        return result
