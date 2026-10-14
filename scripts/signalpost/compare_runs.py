#!/usr/bin/env python3
"""Compare matched company envelopes, without treating coverage as labelled recall."""
from __future__ import annotations
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path


def load(path: Path) -> dict[str, dict]:
    opener = gzip.open if path.suffix == ".gz" else open
    rows = {}
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                org = row["organisation_number"]
                if org in rows:
                    raise ValueError(f"Duplicate company {org} in {path}")
                rows[org] = row
    return rows


def metrics(rows: list[dict]) -> dict:
    fields = Counter()
    claims = Counter()
    for row in rows:
        available = [c for c in row["claims"] if c["availability"] == "available"]
        fields.update({c["field"] for c in available})
        claims.update(c["field"] for c in available)
    return {"companies": len(rows),
            "verified_websites": sum(bool(r.get("identity", {}).get("verified_url")) for r in rows),
            "companies_with_available_field": dict(sorted(fields.items())),
            "available_claims_by_field": dict(sorted(claims.items())),
            "validation_problems": sum(r.get("operations", {}).get("validation_problems", 0) for r in rows),
            "requests_attributed_to_companies": sum(r.get("operations", {}).get("requests_used", 0) for r in rows)}


def compare(before: dict, after: dict) -> dict:
    matched = sorted(before.keys() & after.keys())
    changes = {"gained": [], "lost": [], "changed": []}
    for org in matched:
        left, right = before[org].get("identity", {}), after[org].get("identity", {})
        old, new = left.get("verified_url"), right.get("verified_url")
        if old == new:
            continue
        kind = "gained" if not old else "lost" if not new else "changed"
        changes[kind].append({"organisation_number": org, "names": right.get("names"),
                              "before": old, "after": new,
                              "verdict": right.get("website_verdict"),
                              "rejected_candidates": right.get("rejected_candidates", [])})
    return {"matched_companies": len(matched), "before_only": sorted(before.keys()-after.keys()),
            "after_only": sorted(after.keys()-before.keys()),
            "before": metrics([before[o] for o in matched]),
            "after": metrics([after[o] for o in matched]), "websites": changes,
            "interpretation": "Matched operational coverage, not labelled recall, precision, an official score, or qualification. Live source changes may contribute to differences."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = compare(load(args.before), load(args.after))
    result["inputs"] = {label: {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                        for label, path in (("before", args.before), ("after", args.after))}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    print(json.dumps({"matched": result["matched_companies"],
                      "websites_before": result["before"]["verified_websites"],
                      "websites_after": result["after"]["verified_websites"],
                      **{k: len(v) for k, v in result["websites"].items()}}, indent=2))


if __name__ == "__main__":
    main()
