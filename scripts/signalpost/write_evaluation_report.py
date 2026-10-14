#!/usr/bin/env python3
"""Write the evaluation report from measured artifacts. No network.

    uv run python scripts/signalpost/write_evaluation_report.py --config eval-report.json \
        --output out/evaluation-report.md

Every number in the report is read from a run, integrity, idempotency, resource or review
artifact named in the config; none is typed in by hand. The config supplies only what the
artifacts cannot know about themselves: the exact command, the commit it ran on and the
test-suite result. Sections keep fixture tests, automated integrity, live operations and
human-reviewed quality apart, and a measurement that does not exist is printed as
unavailable rather than as zero.

Config keys: generated_for, environment{...}, tests{command, commit, result},
corpus{label, command, commit, input, report, integrity, idempotency},
live{label, command, commit, input, report, resources, limits{requests, seconds, spend_usd,
vcpu, memory_gib, disk_gib}, note}, refresh{label, command, commit, report, note},
review{summary}, uncertainty[].
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(ROOT / path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def pct(part: int, whole: int) -> str:
    return f"{part}/{whole} ({100 * part / whole:.1f}%)" if whole else "unavailable"


def provenance(section: dict) -> list[str]:
    lines = [f"- Command: `{section['command']}`", f"- Commit: `{section['commit']}`"]
    if section.get("input"):
        lines.append(f"- Input: `{section['input']}` (SHA-256 `{sha256(section['input'])}`)")
    return lines


def states_line(states: dict) -> str:
    return ", ".join(f"{name} {states.get(name, 0)}" for name in ("completed", "partial", "failed"))


def render(config: dict) -> str:
    corpus = config["corpus"]
    run = load(corpus["report"])
    integrity = load(corpus["integrity"])
    idempotency = load(corpus["idempotency"])
    live = config["live"]
    live_run = load(live["report"])
    resources = load(live["resources"]) if live.get("resources") else None
    refresh = config.get("refresh")
    refresh_run = load(refresh["report"]) if refresh else None
    review = load(config["review"]["summary"])

    out: list[str] = []
    add = out.append
    add("# Signalpost evaluation report")
    add("")
    add(f"Written for {config['generated_for']}. Every figure below is read from the artifact named "
        "beside it by `scripts/signalpost/write_evaluation_report.py`.")
    add("")
    add("This report separates four kinds of result. **Fixture tests** show the code does what its "
        "tests say. **Automated integrity checks** show the output is structurally sound and every "
        "citation resolves. **Live operational metrics** show what real runs used. **Independently "
        "reviewed research quality** (whether published facts are true and belong to the right "
        "company) has not been measured, and no accuracy, precision or recall figure appears here.")
    add("")
    add("Official qualification: `not_determined`. Official coverage is scored by the organiser "
        "against a pooled reference collection we do not have; the local field coverage below is "
        "not that score and is not an estimate of it.")
    add("")
    add("## Environment")
    add("")
    for key, value in config["environment"].items():
        add(f"- {key}: {value}")
    add("")

    tests = config["tests"]
    add("## 1. Fixture and unit tests")
    add("")
    add(f"- Command: `{tests['command']}`")
    add(f"- Commit: `{tests['commit']}`")
    add(f"- Result: {tests['result']}")
    add("")
    add("Fixtures live only under `tests/`. They cover identity collisions, null versus reported "
        "zero and Norwegian number parsing, availability states, evidence locators, refresh "
        "idempotency and real changes, output cardinality and resume, budget enforcement under "
        "concurrency, unsafe URLs, packaging and the web interface's routes.")
    add("")

    totals = integrity["totals"]
    envelopes = totals["envelopes"]
    verification = run["verification"]
    add(f"## 2. Automated integrity checks: {corpus['label']}")
    add("")
    out.extend(provenance(corpus))
    add(f"- Run window: {run['results']['started_at']} to {run['results']['completed_at']}")
    add("")
    add("| Check | Result |")
    add("| --- | --- |")
    add(f"| Envelopes vs expected inputs | {verification['envelopes']} / {verification['expected']} |")
    add(f"| Exactly one terminal envelope per input | {verification['exactly_one_per_input']} |")
    add(f"| Duplicates / missing / unexpected | {len(verification['duplicates'])} / "
        f"{verification['missing_count']} / {verification['unexpected_count']} |")
    add(f"| Terminal states | {states_line(run['results']['terminal_states'])} |")
    add(f"| Claims (available of total) | {totals['claims_available']} of {totals['claims']} |")
    add(f"| Evidence records / snapshots | {totals['evidence']} / {totals['snapshots']} |")
    add(f"| Snapshot bodies re-hashed against recorded SHA-256 | {integrity['snapshot_bodies_rehashed']} |")
    problems = integrity.get("problems") or {}
    detail = "" if not problems else " (" + ", ".join(f"{k} {v}" for k, v in problems.items()) + ")"
    add(f"| Integrity problems | {sum(problems.values())}{detail} |")
    add(f"| Integrity verdict (`check_integrity.py`) | ok = {integrity['ok']} |")
    add(f"| Refresh self-diff false changes (`check_idempotency.py`) | {idempotency['false_changes']} over "
        f"{idempotency['claims_replayed']} claims; idempotent = {idempotency['idempotent']} |")
    add("")
    add(f"Integrity input SHA-256 `{integrity['input_sha256']}`; checked {integrity['checked_at']}; "
        f"{integrity['network_requests']} network requests.")
    add("")
    add("### Local field coverage (not official coverage)")
    add("")
    add("Share of the corpus with at least one evidence-backed available claim in each family. "
        "Availability counts what the agent published with a citation; it says nothing about "
        "whether the value is correct. A family with no claim is recorded as not available, not as zero.")
    add("")
    add("| Family | Companies | Available claims |")
    add("| --- | --- | --- |")
    by_company = integrity.get("companies_with_available_family") or {}
    by_claims = integrity.get("available_claims_by_family") or {}
    for family in sorted(by_company, key=lambda name: -by_company[name]):
        add(f"| {family} | {pct(by_company[family], envelopes)} | {by_claims.get(family, 0)} |")
    add("")
    add(f"Verified company websites: {pct(run['results']['verified_websites'], envelopes)}. Claim availability: "
        + ", ".join(f"{k} {v}" for k, v in integrity["claim_availability"].items()) + ".")
    add("")

    add("## 3. Live operational metrics")
    add("")
    add(f"### {corpus['label']}")
    add("")
    budget = run["budget"]
    add(f"{run['results']['envelopes']} companies, {budget['seconds_elapsed']} s wall clock, "
        f"{budget['requests_used']} outbound requests ({budget['requests_per_company']} per company), "
        f"${budget['spend_usd']:.2f} third-party spend, concurrency {run['config']['concurrency']}. "
        "This is the one-off corpus generation with corpus-sized limits, not the per-100 "
        "evaluator budget, which is measured next.")
    add("")
    add(f"### {live['label']}")
    add("")
    out.extend(provenance(live))
    add(f"- Run window: {live_run['results']['started_at']} to {live_run['results']['completed_at']}")
    add("")
    lb, limits = live_run["budget"], live["limits"]
    add("| Resource | Measured | Limit |")
    add("| --- | --- | --- |")
    add(f"| Companies | {live_run['results']['envelopes']} | 100 |")
    add(f"| Outbound requests | {lb['requests_used']} | {limits['requests']} |")
    add(f"| Wall clock | {lb['seconds_elapsed']} s | {limits['seconds']} s |")
    add(f"| Third-party spend | ${lb['spend_usd']:.2f} | ${limits['spend_usd']:.2f} |")
    if resources:
        add(f"| Peak memory | {resources['peak_memory_mib']} MiB ({resources['peak_memory_source']}) | "
            f"{limits['memory_gib']} GiB |")
        add(f"| Mean CPU cores used | {resources['mean_cpu_cores_used']} (user {resources['cpu_user_seconds']} s, "
            f"system {resources['cpu_system_seconds']} s; host has {resources['host']['logical_cpus']} logical CPUs) | "
            f"{limits['vcpu']} vCPU |")
        add(f"| Disk written | {resources['disk_mib_total']} MiB | {limits['disk_gib']} GiB |")
    else:
        add(f"| Peak memory, CPU, disk | unavailable (not measured) | "
            f"{limits['memory_gib']} GiB / {limits['vcpu']} vCPU / {limits['disk_gib']} GiB |")
    add("")
    per_company = live_run["results"]["seconds_per_company"]
    add(f"Terminal states: {states_line(live_run['results']['terminal_states'])}. "
        f"Exactly one envelope per input: {live_run['verification']['exactly_one_per_input']}. "
        f"Seconds per company, median / p90 / max: {per_company['median']} / {per_company['p90']} / "
        f"{per_company['max']}. Budget exhausted after: {live_run['results']['budget_exhausted_after'] or 'never'}.")
    add("")
    add("Requests by purpose: " + ", ".join(f"{k} {v}" for k, v in lb["by_purpose"].items()) + ".")
    add("")
    if live.get("note"):
        add(live["note"])
        add("")
    if refresh_run:
        rr = refresh_run["results"]
        add(f"### {refresh['label']}")
        add("")
        out.extend(provenance(refresh))
        add("")
        add(f"{rr['envelopes']} companies re-researched against `{refresh_run['config']['previous_snapshot']}`: "
            f"{rr['changes_total']} changes, {rr['changes_material']} material; "
            f"{refresh_run['budget']['requests_used']} requests, {refresh_run['budget']['seconds_elapsed']} s, "
            f"${refresh_run['budget']['spend_usd']:.2f}. Terminal states: {states_line(rr['terminal_states'])}.")
        add("")
        if refresh.get("note"):
            add(refresh["note"])
            add("")

    add("## 4. Independently reviewed research quality")
    add("")
    add("**Unavailable.** No human-reviewed truth exists for this corpus, and no model-produced label "
        "is treated as truth. Internal confidence scores are not measured accuracy.")
    add("")
    add(f"A frozen, seeded review queue is prepared instead: {review['items']} items from "
        f"{review['envelopes']} envelopes (seed `{review['seed']}`, input SHA-256 `{review['input_sha256']}`). "
        f"Selection: {review['selection']}")
    add("")
    add("| Stratum | Population | Drawn |")
    add("| --- | --- | --- |")
    for name, population in review["population_by_stratum"].items():
        add(f"| {name} | {population} | {review['drawn_by_stratum'][name]} |")
    add("")
    add(f"Protocol: {review['review_protocol']} Status: `{review['review_status']}`, "
        f"{review['reviewed_items']} items reviewed. Metrics: {review['metrics']}.")
    add("")
    add("## 5. Remaining uncertainty")
    add("")
    for item in config.get("uncertainty", []):
        add(f"- {item}")
    add("")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(config), encoding="utf-8", newline="\n")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
