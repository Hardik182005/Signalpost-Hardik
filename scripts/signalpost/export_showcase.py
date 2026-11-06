"""Export a run's envelopes as a static, versioned, read-only dataset for the website.

    uv run python scripts/signalpost/export_showcase.py out/run/envelopes.jsonl \
        --output showcase/data/v1 --label "100 held-out companies"

The website (built in JBox) only reads these files; it never crawls, computes
financial figures or matches identities itself. Every value is copied from an
envelope claim, and every source link from that claim's evidence.

Written files:
  manifest.json            schema, run id, commit, counts, caveats
  index.json               one search row per company
  companies/<org>.json     the company profile: brief, facts by family, sources

Sanitising: local file paths, stack traces, snapshot storage paths, request logs,
query strings on source URLs and dates of birth are dropped. No secret is ever read.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

SCHEMA = "signalpost-showcase/1"
ROOT = Path(__file__).resolve().parents[2]

FAMILY_ORDER = ["legal_identity", "financials", "people", "locations", "web_presence",
                "business_description", "contact", "jobs", "activity", "news"]
FAMILY_LABELS = {
    "legal_identity": "Legal identity",
    "financials": "Filed accounts",
    "people": "Roles (official register)",
    "locations": "Locations",
    "web_presence": "Website and owned profiles",
    "business_description": "What it does (own website)",
    "contact": "Contact",
    "jobs": "Hiring",
    "activity": "Company-published activity",
    "news": "Independent news",
}
DROP_KEYS = {"birth_date", "date_of_birth", "birthdate", "fodselsdato"}
TRACKING_PARAMS = ("utm_", "fbclid", "gclid", "token", "key", "sig", "session")


def read_envelopes(path: Path) -> list[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def clean_url(url: Any) -> Any:
    """Drop query strings that could carry tracking ids or credentials."""
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return url
    parts = urlsplit(url)
    query = parts.query
    if query and any(marker in query.casefold() for marker in TRACKING_PARAMS):
        query = ""
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def clean_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: clean_value(v) for k, v in value.items() if k not in DROP_KEYS}
    if isinstance(value, list):
        return [clean_value(item) for item in value]
    return clean_url(value)


def available(envelope: dict[str, Any], field: str) -> list[dict[str, Any]]:
    return [c for c in envelope["claims"] if c["field"] == field and c["availability"] == "available"]


def first_value(envelope: dict[str, Any], field: str) -> Any:
    found = available(envelope, field)
    return found[0]["value"] if found else None


def company_scoped_latest(envelope: dict[str, Any], field: str) -> dict[str, Any] | None:
    claims = [c for c in available(envelope, field) if c.get("reporting_period")]
    own = [c for c in claims if c.get("scope") == "company"] or [c for c in claims if c.get("scope") == "group"]
    return max(own, key=lambda c: c["reporting_period"]) if own else None


def fact(claim: dict[str, Any]) -> dict[str, Any]:
    out = {
        "key": claim["claim_key"],
        "field": claim["field"],
        "availability": claim["availability"],
        "value": clean_value(claim.get("value")),
        "evidence": list(claim.get("evidence_ids") or []),
    }
    for name in ("reporting_period", "scope", "currency", "unit", "reason", "effective_date", "confidence"):
        if claim.get(name) not in (None, ""):
            out[name] = claim[name]
    if claim.get("derived_from"):
        out["derived_from"] = list(claim["derived_from"])
    return out


def source(evidence: dict[str, Any]) -> dict[str, Any]:
    locator = evidence.get("locator") or {}
    return {
        "id": evidence["id"],
        "url": clean_url(evidence.get("source_url")),
        "source_class": evidence.get("source_class"),
        "retrieved_at": evidence.get("retrieved_at"),
        "sha256": evidence.get("content_sha256"),
        "locator": {"kind": locator.get("kind"), "value": locator.get("value")} if locator else None,
        "excerpt": (evidence.get("claim_span") or locator.get("excerpt") or "")[:400] or None,
        "method": evidence.get("extraction_method"),
    }


def profile(envelope: dict[str, Any]) -> dict[str, Any]:
    org = envelope["organisation_number"]
    claims = envelope["claims"]
    used_evidence = {eid for c in claims for eid in (c.get("evidence_ids") or [])}
    families: dict[str, list[dict[str, Any]]] = {}
    for claim in claims:
        families.setdefault(claim["family"], []).append(fact(claim))
    synthesis = envelope.get("synthesis") or {}
    identity = envelope.get("identity") or {}
    news_detail = identity.get("news") or {}
    discovery = identity.get("discovery") or {}
    return {
        "schema": SCHEMA,
        "organisation_number": org,
        "name": first_value(envelope, "legal_name") or f"Organisation {org}",
        "terminal_status": envelope["run"]["terminal_status"],
        "terminal_reason": (envelope.get("operations") or {}).get("terminal_reason"),
        "run_id": envelope["run"]["run_id"],
        "researched_at": envelope["run"].get("completed_at"),
        "brief": {
            "headline": synthesis.get("headline"),
            "summary": synthesis.get("summary"),
            "sentences": synthesis.get("sentences", []),
            "highlights": synthesis.get("highlights", []),
            "gaps": synthesis.get("gaps", []),
            "confidence": synthesis.get("confidence"),
            "method": synthesis.get("method"),
            "revenue_comparison": synthesis.get("revenue_comparison"),
        },
        "website": {
            "verdict": identity.get("website_verdict"),
            "verified_url": clean_url(identity.get("verified_url")),
            "candidates_considered": len(discovery.get("candidates", []) or []),
            "rejected": [
                {"url": clean_url(item.get("url")), "reason": item.get("reason")}
                for item in (identity.get("rejected_candidates") or [])[:8] if isinstance(item, dict)
            ],
        },
        "news_search": {k: news_detail.get(k) for k in ("source", "searched", "candidates", "fetched", "published", "reason")
                        if k in news_detail} or None,
        "families": [
            {"family": name, "label": FAMILY_LABELS.get(name, name), "facts": families[name]}
            for name in FAMILY_ORDER + sorted(set(families) - set(FAMILY_ORDER)) if name in families
        ],
        "sources": [source(item) for item in envelope.get("evidence", []) if item["id"] in used_evidence],
        "errors": [
            {"stage": e.get("stage"), "type": e.get("type"), "message": (e.get("message") or "")[:200]}
            for e in envelope.get("errors", [])
        ],
    }


def index_row(envelope: dict[str, Any]) -> dict[str, Any]:
    address = next((c["value"] for c in available(envelope, "address")
                    if isinstance(c["value"], dict) and c["value"].get("kind") == "business"), None) \
        or first_value(envelope, "address") or {}
    revenue = company_scoped_latest(envelope, "revenue")
    comparison = (envelope.get("synthesis") or {}).get("revenue_comparison") or {}
    socials = [c["value"].get("platform") for c in available(envelope, "social_profile") if isinstance(c["value"], dict)]
    names = [first_value(envelope, "legal_name") or ""]
    names += [c["value"].get("name") for c in available(envelope, "former_name") if isinstance(c["value"], dict)]
    return {
        "org": envelope["organisation_number"],
        "name": names[0],
        "former_names": [n for n in names[1:] if n],
        "legal_form": first_value(envelope, "legal_form_code"),
        "city": address.get("city") if isinstance(address, dict) else None,
        "industry": first_value(envelope, "industry_label"),
        "status": envelope["run"]["terminal_status"],
        "website": clean_url(first_value(envelope, "website")),
        "socials": sorted(set(filter(None, socials))),
        "news_count": len(available(envelope, "news_mention")),
        "employees": first_value(envelope, "employees"),
        "revenue": {
            "value": revenue["value"], "period": revenue["reporting_period"],
            "currency": revenue.get("currency"), "scope": revenue.get("scope"),
        } if revenue else None,
        "revenue_change": {
            "status": comparison.get("status"),
            "absolute": comparison.get("absolute_change"),
            "percent": comparison.get("percent_change"),
        } if comparison else None,
        "facts": sum(1 for c in envelope["claims"] if c["availability"] == "available"),
        "sources": len(envelope.get("evidence", [])),
    }


def git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def write_json(path: Path, payload: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=False).encode("utf-8")
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("envelopes", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "showcase" / "data" / "v1")
    parser.add_argument("--label", default="Signalpost research snapshot")
    args = parser.parse_args(argv)

    envelopes = read_envelopes(args.envelopes)
    if not envelopes:
        print("no envelopes", file=sys.stderr)
        return 1
    rows, checksums = [], {}
    for envelope in envelopes:
        org = envelope["organisation_number"]
        checksums[f"companies/{org}.json"] = write_json(args.output / "companies" / f"{org}.json", profile(envelope))
        rows.append(index_row(envelope))
    rows.sort(key=lambda row: (row["name"] or "").casefold())
    checksums["index.json"] = write_json(args.output / "index.json", {"schema": SCHEMA, "companies": rows})

    status = Counter(row["status"] for row in rows)
    run_ids = sorted({e["run"]["run_id"] for e in envelopes})
    manifest = {
        "schema": SCHEMA,
        "label": args.label,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "run_ids": run_ids,
        "researched_between": [min(e["run"].get("started_at") or "" for e in envelopes),
                               max(e["run"].get("completed_at") or "" for e in envelopes)],
        "repository": "https://github.com/Hardik182005/Signalpost-Hardik",
        "commit": git_commit(),
        "counts": {
            "companies": len(rows),
            "terminal_status": dict(status),
            "verified_websites": sum(1 for r in rows if r["website"]),
            "with_social_profiles": sum(1 for r in rows if r["socials"]),
            "with_news": sum(1 for r in rows if r["news_count"]),
            "with_revenue": sum(1 for r in rows if r["revenue"]),
            "with_revenue_comparison": sum(1 for r in rows if (r["revenue_change"] or {}).get("status") == "compared"),
        },
        "caveats": [
            "A snapshot of one research run, not live data.",
            "Facts are machine-extracted from the cited sources; no human has reviewed each one.",
            "A missing value means it was not established from permitted sources, not that it is zero.",
            "Company-published activity and independent news are separate families.",
        ],
        "files": {"index": "index.json", "company": "companies/{org}.json"},
        "checksums_sha256": checksums,
    }
    write_json(args.output / "manifest.json", manifest)
    print(json.dumps({"output": str(args.output), **manifest["counts"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
