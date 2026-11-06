#!/usr/bin/env python3
"""Replay a real run through the production change detector in three scenarios.

    uv run python scripts/signalpost/refresh_replay.py out/run/envelopes.jsonl \
        --report out/run/refresh-replay.json

For every company in the run, the envelope's own claims are the "previous" state and a
copy of them is the "current" state:

  unchanged  the copy is identical                      -> expect no change events
  changed    the latest filed revenue is raised by 10 % -> expect exactly one material
                                                           value_changed on that claim
  failed     the source behind the verified website now
             fails (availability `failed`, no value)    -> expect one availability_changed,
                                                           not material

Nothing is fetched. The point is to show, on real claim keys and values, that the diff
reports real changes, ignores unchanged ones, and never reads a failed source as a
fact about the company. The live re-run (`run_batch.py --previous-snapshot`) covers
the unchanged case against the network.
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import gzip
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from signalpost.models import Claim  # noqa: E402
from signalpost.refresh import diff, previous_from_envelope  # noqa: E402

CLAIM_FIELDS = {f.name for f in dataclasses.fields(Claim)}


def read(path: Path) -> list[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def claims_of(envelope: dict[str, Any]) -> list[Claim]:
    return [Claim(**{k: copy.deepcopy(v) for k, v in item.items() if k in CLAIM_FIELDS})
            for item in envelope["claims"]]


def latest_revenue(claims: list[Claim]) -> Claim | None:
    found = [c for c in claims if c.field == "revenue" and c.availability == "available"
             and isinstance(c.value, (int, float)) and c.reporting_period]
    return max(found, key=lambda c: c.reporting_period) if found else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("envelopes", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)

    totals = {
        "unchanged": {"companies": 0, "claims": 0, "events": 0, "offending": []},
        "changed": {"companies": 0, "expected": 0, "detected": 0, "material": 0, "extra_events": 0, "missed": []},
        "failed": {"companies": 0, "expected": 0, "detected": 0, "material": 0, "extra_events": 0, "missed": []},
    }
    for envelope in read(args.envelopes):
        org = envelope["organisation_number"]
        previous = previous_from_envelope(envelope)

        current = claims_of(envelope)
        events = diff(org, previous, current)
        u = totals["unchanged"]
        u["companies"] += 1
        u["claims"] += len(current)
        u["events"] += len(events)
        u["offending"] += [e.claim_key for e in events][:5]

        current = claims_of(envelope)
        target = latest_revenue(current)
        if target is not None:
            target.value = round(target.value * 1.1, 2) if target.value else 1000.0
            events = diff(org, previous, current)
            c = totals["changed"]
            c["companies"] += 1
            c["expected"] += 1
            hit = [e for e in events if e.claim_key == target.claim_key and e.change_type == "value_changed"]
            c["detected"] += len(hit)
            c["material"] += sum(e.material for e in hit)
            c["extra_events"] += len(events) - len(hit)
            if not hit:
                c["missed"].append(org)

        current = claims_of(envelope)
        site = next((x for x in current if x.field == "website" and x.availability == "available"), None)
        if site is not None:
            site.availability, site.value, site.reason = "failed", None, "replay: source returned an error"
            events = diff(org, previous, current)
            f = totals["failed"]
            f["companies"] += 1
            f["expected"] += 1
            hit = [e for e in events if e.claim_key == site.claim_key and e.change_type == "availability_changed"]
            f["detected"] += len(hit)
            f["material"] += sum(e.material for e in hit)
            f["extra_events"] += len(events) - len(hit)
            if not hit:
                f["missed"].append(org)

    u, c, f = totals["unchanged"], totals["changed"], totals["failed"]
    passed = (u["events"] == 0
              and c["detected"] == c["expected"] == c["material"] and c["extra_events"] == 0
              and f["detected"] == f["expected"] and f["material"] == 0 and f["extra_events"] == 0)
    report = {"source": str(args.envelopes).replace("\\", "/"), "passed": passed, **totals}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
