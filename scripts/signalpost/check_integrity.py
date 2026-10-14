#!/usr/bin/env python3
"""Audit a run's envelopes for evidence integrity. Makes no network requests.

Checks, per envelope: every available claim cites evidence; every evidence id resolves;
every evidence record points at a snapshot in the same envelope with a matching content
hash; financial claims carry currency, unit and reporting period; timestamps are UTC;
source URLs are http(s); claim keys are unique. With --snapshot-root, each snapshot's
stored body is re-hashed against its recorded SHA-256.

    uv run python scripts/signalpost/check_integrity.py out/submission-profiles.jsonl \
        --snapshot-root out --expected data/corpus/organisation-numbers.txt \
        --report out/integrity-report.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from signalpost.models import utc_now  # noqa: E402
from signalpost.store import read_envelopes, verify  # noqa: E402

UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")
DRIVE = re.compile(r"^[A-Za-z]:")
FINANCIAL = "financials"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("envelopes", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expected", type=Path, help="Manifest of organisation numbers")
    parser.add_argument("--snapshot-root", type=Path, help="Directory stored_path is relative to")
    args = parser.parse_args()

    problems: Counter[str] = Counter()
    examples: dict[str, list[str]] = {}
    totals: Counter[str] = Counter()
    availability: Counter[str] = Counter()
    families_available: Counter[str] = Counter()
    companies_with_family: Counter[str] = Counter()
    hashed: set[str] = set()

    def flag(kind: str, where: str) -> None:
        problems[kind] += 1
        bucket = examples.setdefault(kind, [])
        if len(bucket) < 10:
            bucket.append(where)

    for envelope in read_envelopes(args.envelopes):
        org = str(envelope["organisation_number"])
        totals["envelopes"] += 1
        evidence = {e["id"]: e for e in envelope.get("evidence") or []}
        snapshots = {s["id"]: s for s in envelope.get("snapshots") or []}
        keys = Counter(c["claim_key"] for c in envelope.get("claims") or [])
        for key, count in keys.items():
            if count > 1:
                flag("duplicate_claim_key", f"{org}:{key}")

        seen_families: set[str] = set()
        for claim in envelope.get("claims") or []:
            totals["claims"] += 1
            availability[claim["availability"]] += 1
            where = claim["claim_key"]
            if claim["organisation_number"] != org:
                flag("claim_org_mismatch", where)
            for stamp in ("first_observed_at", "last_observed_at"):
                if claim.get(stamp) and not UTC.match(claim[stamp]):
                    flag("non_utc_timestamp", f"{where}.{stamp}")
            if claim["availability"] != "available":
                if claim.get("value") is not None:
                    flag("unavailable_claim_with_value", where)
                if not claim.get("reason"):
                    flag("unavailable_claim_without_reason", where)
                continue
            totals["claims_available"] += 1
            families_available[claim["family"]] += 1
            seen_families.add(claim["family"])
            ids = claim.get("evidence_ids") or []
            if not ids and not claim.get("derived_from"):
                flag("available_claim_without_evidence", where)
            for evidence_id in ids:
                if evidence_id not in evidence:
                    flag("unresolved_evidence_id", f"{where}->{evidence_id}")
            if claim["family"] == FINANCIAL:
                totals["financial_claims_available"] += 1
                if not claim.get("reporting_period"):
                    flag("financial_claim_missing_reporting_period", where)
                # Monetary amounts need a currency and unit; flags such as
                # accounts_audited are booleans and correctly carry neither.
                value = claim.get("value")
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    totals["financial_amounts_available"] += 1
                    for required in ("currency", "unit"):
                        if not claim.get(required):
                            flag(f"financial_amount_missing_{required}", where)
        for family in seen_families:
            companies_with_family[family] += 1

        for record in evidence.values():
            totals["evidence"] += 1
            where = f"{org}:{record['id']}"
            if not record.get("locator"):
                flag("evidence_without_locator", where)
            if not UTC.match(record.get("retrieved_at") or ""):
                flag("evidence_non_utc_retrieved_at", where)
            url = record.get("source_url") or ""
            if url and not url.startswith(("https://", "http://")):
                flag("evidence_non_http_url", where)
            snap_id = record.get("snapshot_id")
            if snap_id:
                snap = snapshots.get(snap_id)
                if snap is None:
                    flag("evidence_unresolved_snapshot", where)
                elif snap.get("content_sha256") != record.get("content_sha256"):
                    flag("evidence_snapshot_hash_mismatch", where)

        for snap in snapshots.values():
            totals["snapshots"] += 1
            stored = snap.get("stored_path")
            if not stored:
                continue
            if stored.startswith(("/", "\\")) or DRIVE.match(stored):
                flag("snapshot_absolute_path", f"{org}:{snap['id']}")
            if args.snapshot_root is not None:
                digest = snap.get("content_sha256")
                if digest in hashed:
                    continue
                path = args.snapshot_root / stored.replace("\\", "/")
                if not path.is_file():
                    flag("snapshot_body_missing", f"{org}:{snap['id']}")
                    continue
                hashed.add(digest)
                if sha256_file(path) != digest:
                    flag("snapshot_body_hash_mismatch", f"{org}:{snap['id']}")

    report = {
        "schema_version": "signalpost.integrity_report/1",
        "checked_at": utc_now(),
        "input": str(args.envelopes).replace("\\", "/"),
        "input_sha256": sha256_file(args.envelopes),
        "network_requests": 0,
        "totals": dict(totals),
        "claim_availability": dict(availability),
        "available_claims_by_family": dict(sorted(families_available.items())),
        "companies_with_available_family": dict(sorted(companies_with_family.items())),
        "snapshot_bodies_rehashed": len(hashed),
        "problems": dict(problems),
        "problem_examples": examples,
        "ok": not problems,
        "note": (
            "Automated integrity only. This does not measure whether a claim is true or "
            "attributed to the right company; that needs independent review."
        ),
    }
    if args.expected:
        expected = [
            line.strip()
            for line in args.expected.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        cardinality = verify(args.envelopes, expected)
        for bulky in ("missing", "unexpected", "duplicates"):
            cardinality[bulky] = list(cardinality.get(bulky) or [])[:20]
        report["cardinality"] = cardinality
        report["ok"] = report["ok"] and bool(cardinality.get("ok"))

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    summary = {k: report[k] for k in ("totals", "problems", "snapshot_bodies_rehashed", "ok")}
    print(json.dumps(summary, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
