#!/usr/bin/env python3
"""Replay every envelope in a run against itself and count false changes.

The refresh gate requires that re-running over unchanged sources produces zero change
events. This rebuilds each stored claim, diffs it against the envelope it came from,
and reports any key whose normalised value is unstable. It makes no network requests.

    uv run python scripts/signalpost/check_idempotency.py out/submission-profiles.jsonl \
        --report out/idempotency-report.json
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from signalpost.models import Claim, utc_now  # noqa: E402
from signalpost.refresh import replay_check  # noqa: E402
from signalpost.store import read_envelopes  # noqa: E402

CLAIM_FIELDS = {f.name for f in dataclasses.fields(Claim)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("envelopes", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    companies = claims = false_changes = 0
    offenders: list[dict] = []
    for envelope in read_envelopes(args.envelopes):
        org = str(envelope["organisation_number"])
        rebuilt = [
            Claim(**{k: v for k, v in record.items() if k in CLAIM_FIELDS})
            for record in envelope.get("claims") or []
        ]
        result = replay_check(org, rebuilt, envelope=envelope)
        companies += 1
        claims += result["replayed_claims"]
        false_changes += result["false_changes"]
        if not result["idempotent"]:
            offenders.append(result)

    report = {
        "schema_version": "signalpost.idempotency_report/1",
        "checked_at": utc_now(),
        "input": str(args.envelopes).replace("\\", "/"),
        "input_sha256": hashlib.sha256(args.envelopes.read_bytes()).hexdigest(),
        "method": "each envelope's claims diffed against the same envelope (offline)",
        "companies": companies,
        "claims_replayed": claims,
        "false_changes": false_changes,
        "idempotent": false_changes == 0,
        "offenders": offenders[:50],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"companies {companies} | claims {claims} | false changes {false_changes}")
    return 0 if false_changes == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
