#!/usr/bin/env python3
"""Draw a frozen, seeded sample of claims for independent human review. No network.

    uv run python scripts/signalpost/build_review_queue.py out/submission-profiles.jsonl \
        --output out/review-queue.jsonl --summary out/review-queue-summary.json

Human-reviewed truth does not exist for this corpus. This writes the queue a reviewer
would work through, with every item carrying the evidence needed to judge it and empty
reviewer fields. It produces no accuracy number and must not be read as one: until two
independent people fill the verdicts in, every precision or recall figure is unverified.

Strata deliberately include abstentions and misses, not just published claims, so the
review can measure recall as well as precision. Selection is by SHA-256 of seed and claim
key, so the same input and seed always give the same queue.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from signalpost.models import utc_now  # noqa: E402
from signalpost.store import read_envelopes  # noqa: E402

VERDICTS = [
    "correct",
    "wrong_value",
    "wrong_company",
    "not_supported_by_cited_source",
    "should_have_been_published",
    "correctly_withheld",
    "cannot_determine",
]

# stratum -> (items to draw, what the reviewer is asked)
STRATA = {
    "website_published": (40, "Is this the website of exactly this legal entity (not a parent, franchise or namesake)?"),
    "website_withheld_ambiguous": (20, "The agent withheld this candidate. Is it this company's website?"),
    "website_none_found": (15, "No website was published. Does this company have one?"),
    "financial_revenue": (25, "Does the cited filing report this revenue, currency, unit and period for this entity?"),
    "contact_from_website": (20, "Is this contact detail on the cited page and does it belong to this entity?"),
    "people_role": (10, "Does the cited register record show this person in this role for this entity?"),
}


def rank(seed: str, key: str) -> str:
    return hashlib.sha256(f"{seed}|{key}".encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def evidence_for(envelope: dict, ids: list[str]) -> list[dict]:
    records = {e["id"]: e for e in envelope.get("evidence") or []}
    return [
        {k: records[i].get(k) for k in ("source_url", "source_class", "retrieved_at", "content_sha256", "locator", "claim_span")}
        for i in ids
        if i in records
    ]


def candidates(envelope: dict):
    """Yield (stratum, item) for every claim or abstention that could be sampled."""
    org = envelope["organisation_number"]
    identity = envelope.get("identity") or {}
    verdict = identity.get("website_verdict") or {}
    names = identity.get("names") or []
    base = {"organisation_number": org, "legal_name": names[0] if names else None}
    claims = envelope.get("claims") or []
    has_registry_site = any(c["field"] == "registry_website" and c["availability"] == "available" for c in claims)

    for claim in claims:
        if claim["availability"] != "available":
            continue
        field, family = claim["field"], claim["family"]
        evidence = evidence_for(envelope, claim.get("evidence_ids") or [])
        stratum = None
        if field == "website":
            stratum = "website_published"
        elif field == "revenue":
            stratum = "financial_revenue"
        elif field in ("email", "phone") and any(e["source_class"] == "company_owned" for e in evidence):
            stratum = "contact_from_website"
        elif family == "people" and field == "role":
            stratum = "people_role"
        if stratum:
            yield stratum, {
                **base,
                "claim_key": claim["claim_key"],
                "field": field,
                "value": claim.get("value"),
                "currency": claim.get("currency"),
                "unit": claim.get("unit"),
                "reporting_period": claim.get("reporting_period"),
                "agent_confidence": claim.get("confidence"),
                "evidence": evidence,
                "identity_verdict": {k: verdict.get(k) for k in ("candidate", "decision", "confidence", "reason")} if field == "website" else None,
            }

    if verdict.get("decision") == "ambiguous":
        yield "website_withheld_ambiguous", {
            **base,
            "claim_key": f"{org}:website",
            "field": "website",
            "value": None,
            "withheld_candidate": verdict.get("candidate"),
            "identity_verdict": {k: verdict.get(k) for k in ("candidate", "decision", "confidence", "reason", "signals")},
            "evidence": [],
        }
    elif not verdict and not has_registry_site:
        yield "website_none_found", {
            **base,
            "claim_key": f"{org}:website",
            "field": "website",
            "value": None,
            "terminal_reason": (envelope.get("operations") or {}).get("terminal_reason"),
            "identity_verdict": None,
            "evidence": [],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("envelopes", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--seed", default="signalpost-review-1")
    args = parser.parse_args()

    pools: dict[str, list[tuple[str, dict]]] = {name: [] for name in STRATA}
    envelopes = 0
    for envelope in read_envelopes(args.envelopes):
        envelopes += 1
        for stratum, item in candidates(envelope):
            pools[stratum].append((rank(args.seed, f"{stratum}|{item['claim_key']}"), item))

    queue = []
    population = {}
    for stratum, (wanted, question) in STRATA.items():
        pool = sorted(pools[stratum], key=lambda pair: pair[0])
        population[stratum] = len(pool)
        # At most one item per company within a stratum, so one company cannot dominate it.
        seen: set[str] = set()
        for _, item in pool:
            if len(seen) >= wanted:
                break
            if item["organisation_number"] in seen:
                continue
            seen.add(item["organisation_number"])
            queue.append({
                "queue_id": f"rq-{len(queue) + 1:04d}",
                "stratum": stratum,
                "question": question,
                **item,
                "allowed_verdicts": VERDICTS,
                "reviewer_1": {"verdict": None, "notes": None, "reviewed_at": None},
                "reviewer_2": {"verdict": None, "notes": None, "reviewed_at": None},
                "adjudicated_verdict": None,
            })

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8", newline="\n") as handle:
        for item in queue:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    drawn = {name: sum(1 for item in queue if item["stratum"] == name) for name in STRATA}
    summary = {
        "schema_version": "signalpost.review_queue/1",
        "generated_at": utc_now(),
        "input": str(args.envelopes).replace("\\", "/"),
        "input_sha256": sha256_file(args.envelopes),
        "envelopes": envelopes,
        "seed": args.seed,
        "selection": "Per stratum, ascending SHA-256 of 'seed|stratum|claim_key', at most one item per company.",
        "population_by_stratum": population,
        "drawn_by_stratum": drawn,
        "items": len(queue),
        "review_protocol": (
            "Two independent human reviewers per item, blind to each other; disagreements adjudicated "
            "by a third. Verdicts are recorded against the claim key and the cited content SHA-256."
        ),
        "review_status": "unreviewed",
        "reviewed_items": 0,
        "metrics": "unverified — no precision, recall or wrong-company rate can be computed until the queue is reviewed",
    }
    args.summary.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("items", "population_by_stratum", "drawn_by_stratum", "review_status")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
