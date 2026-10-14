#!/usr/bin/env python3
"""Build the declared NAV vacancy cache used by `src/signalpost/nav_jobs.py`.

    uv run python scripts/signalpost/build_nav_job_index.py \
        --output data/nav/nav-job-index.json.gz [--from-walk walk.json] [--workers 4]

Two phases, both against NAV's open vacancy feed (https://pam-stilling-feed.nav.no,
documented at https://navikt.github.io/pam-stilling-feed/, public token):

1. **Walk** the feed from its first page to its end, keeping the latest status of every
   ad, so the set of currently ACTIVE ads and the end-of-feed cursor are known. The feed
   cannot be entered at a date, so this reads every page once (a few hours). A previous
   walk can be supplied with `--from-walk` instead.
2. **Detail**: read each active ad's feed entry once to record its employer organisation
   number — the field that makes NAV an exact-entity source.

The output holds no ad text beyond titles and employer names, and nothing it contains is
published as evidence: at run time every vacancy is re-read from NAV before it is
published. The cache only says where to look. `--refresh` re-reads the feed forward from
an existing index's cursor and details only the new ads, which is how the cache is kept
current between official runs.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

BASE = "https://pam-stilling-feed.nav.no"
USER_AGENT = "SignalpostResearchBot/1.0 (+https://github.com/Hardik182005/signalpost)"


class Feed:
    def __init__(self) -> None:
        self.client = httpx.Client(
            timeout=90,
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip", "Accept": "application/json"},
        )
        self.authorise()

    def authorise(self) -> None:
        text = self.client.get(f"{BASE}/api/publicToken").text
        self.client.headers["Authorization"] = "Bearer " + re.search(r"eyJ[A-Za-z0-9._-]+", text).group(0)

    def get(self, path: str) -> dict | None:
        for attempt in range(8):
            try:
                response = self.client.get(BASE + path)
                if response.status_code == 401:
                    self.authorise()
                    continue
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as error:
                print(f"retry {path}: {error!r}"[:200], file=sys.stderr, flush=True)
                time.sleep(5 * (attempt + 1))
        raise SystemExit(f"gave up at {path}")


def walk(feed: Feed, start: str, ads: dict[str, dict]) -> tuple[str | None, int]:
    """Apply every feed change from `start` to the end; return the last page id."""
    path, pages, last_id = start, 0, None
    while path:
        body = feed.get(path) or {}
        pages += 1
        for item in body.get("items", []):
            entry = item.get("_feed_entry", {})
            uuid = item.get("id")
            if entry.get("status") == "ACTIVE":
                previous = ads.get(uuid, {})
                ads[uuid] = {
                    "business": entry.get("businessName"),
                    "title": entry.get("title"),
                    "orgnr": previous.get("orgnr"),
                    "employer": previous.get("employer"),
                    "status": "ACTIVE",
                }
            else:
                ads.pop(uuid, None)
        last_id = body.get("id") or last_id
        path = body.get("next_url")
        if pages % 200 == 0:
            print(f"  {pages} pages, {len(ads)} active", flush=True)
    return last_id, pages


def detail(feed: Feed, ads: dict[str, dict], workers: int) -> int:
    pending = [uuid for uuid, row in ads.items() if not row.get("orgnr")]

    def one(uuid: str) -> tuple[str, dict | None]:
        time.sleep(0.05)
        return uuid, feed.get(f"/api/v1/feedentry/{uuid}")

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for uuid, body in pool.map(one, pending):
            done += 1
            if not body or str(body.get("status")).upper() != "ACTIVE":
                ads.pop(uuid, None)
                continue
            employer = (body.get("ad_content") or {}).get("employer") or {}
            ads[uuid]["orgnr"] = re.sub(r"\D", "", str(employer.get("orgnr") or "")) or None
            ads[uuid]["employer"] = employer.get("name")
            if done % 1000 == 0:
                print(f"  {done}/{len(pending)} details", flush=True)
    return done


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--output", default="data/nav/nav-job-index.json.gz")
    parser.add_argument("--from-walk", help="JSON from a previous full walk: {cursor_page_id, active}")
    parser.add_argument("--refresh", action="store_true", help="Update an existing --output in place")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    output = Path(args.output)
    feed = Feed()
    ads: dict[str, dict] = {}
    cursor: str | None = None
    pages = 0

    if args.refresh:
        with gzip.open(output, "rt", encoding="utf-8") as handle:
            existing = json.load(handle)
        ads, cursor = existing["ads"], existing["cursor_page_id"]
        cursor, pages = walk(feed, f"/api/v1/feed/{cursor}", ads)
    elif args.from_walk:
        body = json.loads(Path(args.from_walk).read_text(encoding="utf-8"))
        cursor, pages = body["cursor_page_id"], body.get("pages", 0)
        for uuid, row in body["active"].items():
            business, title = (row[0], row[1]) if isinstance(row, list) else (row.get("business"), row.get("title"))
            ads[uuid] = {"business": business, "title": title, "orgnr": None, "employer": None, "status": "ACTIVE"}
        # Catch up from the walk's end to now before reading details.
        cursor, extra = walk(feed, f"/api/v1/feed/{cursor}", ads)
        pages += extra
    else:
        cursor, pages = walk(feed, "/api/v1/feed", ads)

    detailed = detail(feed, ads, args.workers)
    index = {
        "schema_version": "signalpost.nav_job_index/1",
        "source": f"{BASE}/api/v1/feed",
        "licence": "NAV vacancy feed terms of use: https://navikt.github.io/pam-stilling-feed/",
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cursor_page_id": cursor,
        "feed_pages_read": pages,
        "details_read": detailed,
        "ads": ads,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(output, "wt", encoding="utf-8") as handle:
        json.dump(index, handle, ensure_ascii=False, sort_keys=True)
    with_number = sum(1 for row in ads.values() if row.get("orgnr"))
    print(f"wrote {output}: {len(ads)} active ads, {with_number} with an employer number, cursor {cursor}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
