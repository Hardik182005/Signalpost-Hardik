#!/usr/bin/env python3
"""Before/after comparison of two 100-company runs on the same inputs.

    uv run python scripts/signalpost/before_after.py \
        docs/evidence/smoke100-holdout/envelopes.jsonl.gz out/nov2026-final/envelopes.jsonl \
        --before-report docs/evidence/smoke100-holdout/envelopes-report.json \
        --after-report out/nov2026-final/envelopes-report.json \
        --json docs/evidence/nov2026/before-after.json --markdown docs/evidence/nov2026/BEFORE_AFTER.md

Operational coverage only: counts of what each run published or abstained on, with the
reasons. It is not labelled recall or precision, and live sources change between runs.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from compare_runs import compare, load  # noqa: E402


def bucket_website(reason: str) -> str:
    if reason.startswith("No candidate domain"):
        return "no candidate domain resolved in DNS"
    if reason.startswith("Candidate websites failed"):
        return "candidates failed the identity gate"
    if reason.startswith("evidence suggests a match"):
        return "match suggested but evidence insufficient"
    return "other: " + reason[:60]


def bucket_news(claim: dict) -> str:
    reason = claim.get("reason") or ""
    if claim["availability"] == "available":
        return "published independent article"
    if "429" in reason:
        return "search failed: HTTP 429 (rate limited)"
    if "timed out" in reason:
        return "search failed: timeout"
    if "rate limit busy" in reason:
        return "skipped: shared rate-limit gate busy"
    if "too generic" in reason:
        return "skipped: name too generic for exact search"
    if "none confirmed" in reason:
        return "hits found, none confirmed to name the company"
    if "no independent article" in reason:
        return "searched, no independent article found"
    return f"{claim['availability']}: {reason[:60]}"


def summarise(rows: list[dict], report: dict | None) -> dict:
    website_reasons, news_status, revenue = Counter(), Counter(), Counter()
    socials = news_companies = resolved = unresolved = 0
    walls, requests, cost, news_seconds = [], 0, 0.0, 0.0
    for row in rows:
        claims = row["claims"]
        evidence = {e["id"] for e in row.get("evidence") or []}
        for claim in claims:
            if claim["availability"] == "available":
                ok = all(i in evidence for i in claim.get("evidence_ids") or [])
                resolved += ok and bool(claim.get("evidence_ids"))
                unresolved += not ok or not claim.get("evidence_ids")
        site = [c for c in claims if c["field"] == "website"]
        if site and site[0]["availability"] != "available":
            website_reasons[bucket_website(site[0].get("reason") or "")] += 1
        socials += any(c["field"] == "social_profile" and c["availability"] == "available" for c in claims)
        news = [c for c in claims if c["family"] == "news"]
        if not news:
            news_status["not searched (no news family in this version)"] += 1
        else:
            published = [c for c in news if c["availability"] == "available"]
            news_companies += bool(published)
            news_status[bucket_news(published[0] if published else news[0])] += 1
        comparison = (row.get("synthesis") or {}).get("revenue_comparison")
        revenue[comparison["status"] if comparison else "not produced (no comparison in this version)"] += 1
        ops = row.get("operations") or {}
        walls.append(ops.get("wall_seconds") or 0)
        requests += ops.get("requests_used") or 0
        cost += ops.get("third_party_cost_usd") or 0
        news_seconds += (ops.get("stage_seconds") or {}).get("news", 0)
    results = (report or {}).get("results", {})
    return {
        "companies": len(rows),
        "terminal_states": dict(Counter(r["run"]["terminal_status"] for r in rows)),
        "verified_websites": sum(bool(r["identity"].get("verified_url")) for r in rows),
        "website_abstentions": dict(website_reasons.most_common()),
        "companies_with_social_profile": socials,
        "companies_with_independent_news": news_companies,
        "news_outcome": dict(news_status.most_common()),
        "revenue_comparison": dict(revenue.most_common()),
        "available_claims": resolved + unresolved,
        "available_claims_with_resolving_evidence": resolved,
        "available_claims_without_resolving_evidence": unresolved,
        "requests": requests,
        "third_party_cost_usd": round(cost, 4),
        "run_wall_clock": [results.get("started_at"), results.get("completed_at")],
        "seconds_per_company_median": round(statistics.median(walls), 2) if walls else None,
        "seconds_per_company_p90": round(sorted(walls)[int(0.9 * (len(walls) - 1))], 2) if walls else None,
        "news_stage_seconds_total": round(news_seconds, 1),
    }


def markdown(before: dict, after: dict, websites: dict) -> str:
    def row(label, key, fmt=str):
        return f"| {label} | {fmt(before[key])} | {fmt(after[key])} |"

    def dist(d):
        return "<br>".join(f"{k}: {v}" for k, v in d.items()) or "—"

    lines = ["| Measure | Before (13 Oct 2026, `smoke100-holdout`) | After (this revision) |", "|---|---|---|",
             row("Companies", "companies"), row("Terminal states", "terminal_states", dist),
             row("Verified company websites", "verified_websites"),
             row("Website abstention reasons", "website_abstentions", dist),
             row("Companies with a company-owned social profile", "companies_with_social_profile"),
             row("Companies with an independent news article", "companies_with_independent_news"),
             row("News outcome per company", "news_outcome", dist),
             row("Revenue comparison", "revenue_comparison", dist),
             row("Available claims", "available_claims"),
             row("…whose evidence references all resolve", "available_claims_with_resolving_evidence"),
             row("Requests", "requests"), row("Third-party cost (USD)", "third_party_cost_usd"),
             row("Seconds per company, median", "seconds_per_company_median"),
             row("Seconds per company, p90", "seconds_per_company_p90"),
             row("Time spent in news lookups (sum over companies, s)", "news_stage_seconds_total")]
    lines += ["", f"Website changes on matched companies: gained {len(websites['gained'])}, "
              f"lost {len(websites['lost'])}, changed {len(websites['changed'])}."]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--before-report", type=Path)
    parser.add_argument("--after-report", type=Path)
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    args = parser.parse_args()
    left, right = load(args.before), load(args.after)
    matched = sorted(left.keys() & right.keys())
    read = lambda p: json.loads(p.read_text(encoding="utf-8")) if p else None  # noqa: E731
    before = summarise([left[o] for o in matched], read(args.before_report))
    after = summarise([right[o] for o in matched], read(args.after_report))
    websites = compare(left, right)["websites"]
    result = {"matched_companies": len(matched), "before": before, "after": after, "website_changes": websites,
              "interpretation": "Operational coverage on the same 100 inputs, not labelled recall or precision. "
                                "Live sources change between runs."}
    for path in (args.json, args.markdown):
        path.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.markdown.write_text(markdown(before, after, websites), encoding="utf-8")
    print(args.markdown.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
