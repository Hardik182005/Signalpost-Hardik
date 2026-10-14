"""Batch runner — the command the evaluator runs.

The locked evaluator budget is 100 inputs, 45 minutes wall clock, 8 vCPU / 16 GB, and
2,000 outbound requests including redirects and retries. Everything in this module is
shaped by the gap between "100 companies" and "45 minutes":

* **Concurrency, because the work is network-bound.** A company takes ~8-20 s of
  mostly waiting. Run sequentially, 100 companies would not fit; the per-host throttle
  in `fetch` keeps concurrency polite rather than abusive, since the concurrent
  companies are on different hosts.
* **A per-company request allowance, recomputed as the run proceeds.** Early companies
  must not spend the budget that later companies need, and companies that finish cheap
  hand their unused allowance back to the ones still to come.
* **A wall-clock reserve.** When time runs short the runner stops *starting* new
  research and terminates the remaining companies as `failed` with a stated reason,
  because an envelope that says "we ran out of time" is worth more than no envelope.

The last point is the contract this module exists to keep: **exactly one terminal
envelope per input, always** — including for companies that crash, are refused, or are
never reached.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import corpus as corpus_mod
from . import store as store_mod
from .budget import (
    DEFAULT_MAX_REQUESTS,
    DEFAULT_MAX_SECONDS,
    DEFAULT_MAX_SPEND_USD,
    Budget,
)
from .fetch import Fetcher
from .models import utc_now
from .pipeline import PIPELINE_VERSION, CompanyPipeline, CompanyRequest, failed_envelope

# Fraction of the wall clock kept back so every remaining company can still be
# terminated and the report written.
TIME_RESERVE_RATIO = 0.06
MIN_TIME_RESERVE_SECONDS = 20.0

# Requests held back for the same reason.
REQUEST_RESERVE_RATIO = 0.04

# The locked envelope is 2,000 requests per 100 inputs. Builderr now supplies a shared
# batch of 1,000 (possibly 1,100) companies, and a fixed 2,000-request default would
# research the first ~250 and terminate the rest as `failed`. Unless the evaluator
# states a cap, the request budget therefore scales with the batch at the same ratio.
REQUESTS_PER_COMPANY = 20

# Above this many inputs the run is wide enough that more concurrent companies are
# needed to finish inside the wall clock; the per-host throttle keeps each site polite.
LARGE_BATCH = 200
LARGE_BATCH_CONCURRENCY = 24

# The evaluator may supply its budget through the environment instead of flags.
ENV_LIMITS = {
    "max_requests": "SIGNALPOST_MAX_REQUESTS",
    "max_seconds": "SIGNALPOST_MAX_SECONDS",
    "max_spend_usd": "SIGNALPOST_MAX_SPEND_USD",
    "concurrency": "SIGNALPOST_CONCURRENCY",
}


def resolve_limits(args: argparse.Namespace, inputs: int) -> dict[str, float]:
    """Budget for this run: explicit flag, then environment, then a batch-scaled default."""
    defaults = {
        "max_requests": max(DEFAULT_MAX_REQUESTS, REQUESTS_PER_COMPANY * inputs),
        "max_seconds": DEFAULT_MAX_SECONDS,
        "max_spend_usd": DEFAULT_MAX_SPEND_USD,
        "concurrency": LARGE_BATCH_CONCURRENCY if inputs > LARGE_BATCH else 8,
    }
    limits: dict[str, float] = {}
    for name, default in defaults.items():
        value = getattr(args, name, None)
        if value is None and os.environ.get(ENV_LIMITS[name], "").strip():
            value = float(os.environ[ENV_LIMITS[name]])
        limits[name] = default if value is None else value
    return limits


@dataclass(slots=True)
class RunConfig:
    """Everything that shapes a run, recorded verbatim into the report."""

    inputs: list[str]
    output: Path
    run_id: str
    concurrency: int = 8
    max_requests: int = DEFAULT_MAX_REQUESTS
    max_seconds: float = DEFAULT_MAX_SECONDS
    max_spend_usd: float = DEFAULT_MAX_SPEND_USD
    max_pages: int = 5
    snapshot_root: Path | None = None
    previous: Path | None = None
    cutoff: str | None = None
    include_financials: bool = True
    include_structure: bool = True
    resume: bool = True
    offline: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "inputs": len(self.inputs),
            "output": str(self.output).replace("\\", "/"),
            "concurrency": self.concurrency,
            "max_requests": self.max_requests,
            "max_seconds": self.max_seconds,
            "max_spend_usd": self.max_spend_usd,
            "max_pages_per_company": self.max_pages,
            "previous_snapshot": str(self.previous).replace("\\", "/") if self.previous else None,
            "cutoff": self.cutoff,
            "include_financials": self.include_financials,
            "include_structure": self.include_structure,
            "resume": self.resume,
            "offline": self.offline,
            "pipeline_version": PIPELINE_VERSION,
        }


@dataclass(slots=True)
class RunStats:
    """Measured results. Every number here was observed, none estimated."""

    started_at: str = field(default_factory=utc_now)
    completed_at: str | None = None
    terminal_states: dict[str, int] = field(default_factory=dict)
    envelopes: int = 0
    claims_available: int = 0
    claims_total: int = 0
    evidence_total: int = 0
    material_changes: int = 0
    total_changes: int = 0
    verified_websites: int = 0
    company_seconds: list[float] = field(default_factory=list)
    exhausted_at: int | None = None

    def record(self, envelope: dict[str, Any]) -> None:
        state = str((envelope.get("run") or {}).get("terminal_status"))
        self.terminal_states[state] = self.terminal_states.get(state, 0) + 1
        self.envelopes += 1
        operations = envelope.get("operations") or {}
        self.claims_available += int(operations.get("claims_available") or 0)
        self.claims_total += int(operations.get("claims_total") or 0)
        self.evidence_total += int(operations.get("evidence_total") or 0)
        seconds = operations.get("wall_seconds")
        if isinstance(seconds, (int, float)):
            self.company_seconds.append(float(seconds))
        refresh = envelope.get("refresh") or {}
        self.material_changes += int(refresh.get("material_changes") or 0)
        self.total_changes += int(refresh.get("total_changes") or 0)
        if (envelope.get("identity") or {}).get("verified_url"):
            self.verified_websites += 1

    def to_dict(self) -> dict[str, Any]:
        seconds = sorted(self.company_seconds)
        def percentile(fraction: float) -> float | None:
            if not seconds:
                return None
            index = min(len(seconds) - 1, int(fraction * len(seconds)))
            return round(seconds[index], 2)

        return {
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "envelopes": self.envelopes,
            "terminal_states": dict(sorted(self.terminal_states.items())),
            "claims_available": self.claims_available,
            "claims_total": self.claims_total,
            "evidence_total": self.evidence_total,
            "verified_websites": self.verified_websites,
            "verified_website_rate": round(
                self.verified_websites / max(self.envelopes, 1) * 100, 2
            ),
            "changes_total": self.total_changes,
            "changes_material": self.material_changes,
            "seconds_per_company": {
                "median": percentile(0.5),
                "p90": percentile(0.9),
                "max": round(seconds[-1], 2) if seconds else None,
            },
            "budget_exhausted_after": self.exhausted_at,
        }


class BatchRunner:
    """Runs a list of organisation numbers to completion inside the budget."""

    def __init__(self, config: RunConfig) -> None:
        self.config = config
        self.budget = Budget(
            max_requests=config.max_requests,
            max_seconds=config.max_seconds,
            max_spend_usd=config.max_spend_usd,
        )
        self.fetcher = Fetcher(
            self.budget,
            snapshot_root=config.snapshot_root,
            offline=config.offline,
        )
        today = None
        if config.cutoff:
            today = datetime.fromisoformat(config.cutoff.replace("Z", "+00:00")).date()
        self.pipeline = CompanyPipeline(
            self.fetcher,
            max_pages=config.max_pages,
            today=today,
            include_financials=config.include_financials,
            include_structure=config.include_structure,
        )
        self.stats = RunStats()
        self.previous = (
            store_mod.load_previous(config.previous) if config.previous else {}
        )

    # -- budget shaping -------------------------------------------------------

    def _time_reserve(self) -> float:
        return max(MIN_TIME_RESERVE_SECONDS, self.config.max_seconds * TIME_RESERVE_RATIO)

    def _time_is_short(self) -> bool:
        snapshot = self.budget.snapshot()
        return snapshot.seconds_elapsed >= self.config.max_seconds - self._time_reserve()

    def _requests_exhausted(self) -> bool:
        snapshot = self.budget.snapshot()
        reserve = self.config.max_requests * REQUEST_RESERVE_RATIO
        return snapshot.requests_used + snapshot.requests_reserved >= (
            self.config.max_requests - reserve
        )

    def _page_allowance(self, remaining: int) -> int:
        """How many pages this company may crawl, given what is left for the rest.

        Recomputed per company so that cheap companies fund expensive ones instead of
        the budget being carved up evenly in advance and wasted.
        """
        if remaining <= 0:
            return self.config.max_pages
        snapshot = self.budget.snapshot()
        spare = max(0, self.config.max_requests - snapshot.requests_used)
        per_company = spare / remaining
        # A company costs roughly 4 registry requests plus robots before any crawling.
        crawl_budget = per_company - 5
        if crawl_budget <= 0:
            return 1
        return max(1, min(self.config.max_pages, int(crawl_budget)))

    # -- execution ------------------------------------------------------------

    def _run_one(self, org: str, page_budget: int) -> dict[str, Any]:
        try:
            return self.pipeline.envelope(
                CompanyRequest(
                    organisation_number=org,
                    previous=self.previous.get(org),
                    page_budget=page_budget,
                ),
                run_id=self.config.run_id,
            )
        except Exception as exc:  # noqa: BLE001 - the contract is an envelope, always
            return failed_envelope(
                org, self.config.run_id, f"{type(exc).__name__}: {exc}"[:400]
            )

    def run(self, *, progress: bool = True) -> dict[str, Any]:
        config = self.config
        moved = store_mod.prepare_output(
            config.output, run_id=config.run_id, inputs=config.inputs, resume=config.resume
        )
        if moved is not None and progress:
            print(f"existing output belongs to another run; kept as {moved}")
        store = store_mod.RunStore(config.output)
        pending = list(config.inputs)
        if config.resume:
            already = store.written
            pending = [org for org in pending if not store.has(org)]
            if already and progress:
                print(f"resuming: {already} envelopes already written, {len(pending)} to go")

        started = time.monotonic()
        done = 0
        if self.pipeline.nav_jobs is not None and pending:
            # Bring the shared vacancy index up to date while the first companies are
            # still in their registry stage, so no worker waits on it from cold.
            threading.Thread(
                target=self.pipeline.nav_jobs.index.ensure_ready, name="nav-index", daemon=True
            ).start()
        try:
            with ThreadPoolExecutor(max_workers=config.concurrency) as pool:
                queue = list(pending)
                in_flight: dict[Future, str] = {}

                def submit_next() -> bool:
                    """Start one more company, unless the budget says stop."""
                    if not queue:
                        return False
                    if self._time_is_short() or self._requests_exhausted():
                        return False
                    org = queue.pop(0)
                    allowance = self._page_allowance(len(queue) + len(in_flight) + 1)
                    in_flight[pool.submit(self._run_one, org, allowance)] = org
                    return True

                for _ in range(config.concurrency):
                    if not submit_next():
                        break

                while in_flight:
                    finished, _ = wait(list(in_flight), return_when=FIRST_COMPLETED)
                    for future in finished:
                        in_flight.pop(future, None)
                        envelope = future.result()
                        store.write(envelope)
                        self.stats.record(envelope)
                        done += 1
                        if progress and done % 10 == 0:
                            snapshot = self.budget.snapshot()
                            rate = done / max(time.monotonic() - started, 0.001)
                            print(
                                f"  {done}/{len(pending)} done | "
                                f"{snapshot.requests_used}/{config.max_requests} req | "
                                f"{snapshot.seconds_elapsed:.0f}s | {rate * 60:.1f}/min",
                                flush=True,
                            )
                        # Keep the pool full: one finished, one starts.
                        submit_next()

                # Anything still queued when the budget ran out still owes an envelope.
                if queue:
                    self.stats.exhausted_at = done
                    snapshot = self.budget.snapshot()
                    reason = (
                        "Wall-clock budget exhausted before this company was researched"
                        if self._time_is_short()
                        else "Outbound request budget exhausted before this company was researched"
                    )
                    if progress:
                        print(
                            f"  budget reached after {done} companies "
                            f"({snapshot.requests_used} requests, {snapshot.seconds_elapsed:.0f}s); "
                            f"terminating remaining {len(queue)} as failed",
                            flush=True,
                        )
                    for org in queue:
                        envelope = failed_envelope(org, config.run_id, reason)
                        store.write(envelope)
                        self.stats.record(envelope)
        finally:
            store.close()

        self.stats.completed_at = utc_now()
        report = self.report(store.path)
        store_mod.write_json(config.output.with_name(config.output.stem + "-report.json"), report)
        return report

    def report(self, output: Path) -> dict[str, Any]:
        snapshot = self.budget.snapshot()
        verification = store_mod.verify(output, expected=self.config.inputs)
        return {
            "schema_version": "signalpost.run_report/1",
            "config": self.config.to_dict(),
            "results": self.stats.to_dict(),
            "budget": {
                "requests_used": snapshot.requests_used,
                "max_requests": snapshot.max_requests,
                "requests_per_company": round(
                    snapshot.requests_used / max(self.stats.envelopes, 1), 2
                ),
                "seconds_elapsed": round(snapshot.seconds_elapsed, 1),
                "max_seconds": snapshot.max_seconds,
                "spend_usd": snapshot.spend_usd,
                "max_spend_usd": snapshot.max_spend_usd,
                "denials": snapshot.denials,
                "nav_job_index": (
                    dict(self.pipeline.nav_jobs.index.status) if self.pipeline.nav_jobs else None
                ),
                "by_purpose": snapshot.by_purpose,
                "top_hosts": dict(
                    sorted(snapshot.by_host.items(), key=lambda item: -item[1])[:15]
                ),
            },
            "verification": verification,
            # Our own measurement only. The official score is determined by the
            # organisers' evaluator, which is not run here.
            "official_qualification": "not_determined",
        }


# -- command line -------------------------------------------------------------


def _read_inputs(args: argparse.Namespace) -> list[str]:
    """Inputs come from a corpus file, an explicit list, or stdin."""
    numbers: list[str] = []
    if args.organisation_numbers:
        numbers.extend(args.organisation_numbers)
    if args.input:
        path = Path(args.input)
        if path.suffix == ".jsonl" or path.name.endswith(".jsonl.gz"):
            numbers.extend(row.organisation_number for row in corpus_mod.load_corpus(path))
        else:
            numbers.extend(
                line.strip()
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith("#")
            )
    if not numbers and not sys.stdin.isatty():
        numbers.extend(
            line.strip() for line in sys.stdin.read().splitlines() if line.strip()
        )
    # De-duplicate, keeping order: one envelope per company, not one per mention.
    seen: set[str] = set()
    unique: list[str] = []
    for org in numbers:
        if org not in seen:
            seen.add(org)
            unique.append(org)
    return unique[: args.limit] if args.limit else unique


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signalpost-run",
        description="Research Norwegian companies and write one terminal envelope each.",
    )
    parser.add_argument("organisation_numbers", nargs="*", help="Organisation numbers to research")
    parser.add_argument("-i", "--input", help="Corpus .jsonl(.gz) or a text file of numbers")
    parser.add_argument("-o", "--output", default="out/run.jsonl", help="Envelope output path")
    parser.add_argument("--run-id", default=None, help="Run identifier (default: timestamp)")
    parser.add_argument("--limit", type=int, default=None, help="Only take the first N inputs")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="Companies researched at once (default: 8, rising to 24 for large batches)",
    )
    parser.add_argument(
        "--max-requests",
        type=int,
        default=None,
        help=f"Outbound request cap (default: {REQUESTS_PER_COMPANY} per input, the locked 2,000-per-100 ratio)",
    )
    parser.add_argument("--max-seconds", type=float, default=None, help="Wall-clock cap (default: 2700)")
    parser.add_argument("--max-spend-usd", type=float, default=None, help="Third-party spend cap (default: 10)")
    parser.add_argument("--max-pages", type=int, default=5, help="Pages per company website")
    parser.add_argument("--snapshots", default=None, help="Directory for raw response snapshots")
    parser.add_argument(
        "--previous-snapshot",
        default=None,
        help="A previous run's .jsonl, to compare against for change detection",
    )
    parser.add_argument(
        "--cutoff",
        default=None,
        help="ISO date treated as 'today' for recency, so a replay is deterministic",
    )
    parser.add_argument("--no-financials", action="store_true")
    parser.add_argument("--no-structure", action="store_true")
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Start fresh even if the output holds an interrupted attempt at this --run-id "
        "(an output from any other run is always set aside, never resumed)",
    )
    parser.add_argument("--offline", action="store_true", help="Make no network requests")
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    inputs = _read_inputs(args)
    if not inputs:
        print("No organisation numbers supplied. See --help.", file=sys.stderr)
        return 2

    run_id = args.run_id or f"run-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    limits = resolve_limits(args, len(inputs))
    config = RunConfig(
        inputs=inputs,
        output=Path(args.output),
        run_id=run_id,
        concurrency=max(1, int(limits["concurrency"])),
        max_requests=int(limits["max_requests"]),
        max_seconds=float(limits["max_seconds"]),
        max_spend_usd=float(limits["max_spend_usd"]),
        max_pages=args.max_pages,
        snapshot_root=Path(args.snapshots) if args.snapshots else None,
        previous=Path(args.previous_snapshot) if args.previous_snapshot else None,
        cutoff=args.cutoff,
        include_financials=not args.no_financials,
        include_structure=not args.no_structure,
        resume=not args.no_resume,
        offline=args.offline,
    )

    if not args.quiet:
        print(f"run {run_id}: {len(inputs)} companies -> {config.output}")

    report = BatchRunner(config).run(progress=not args.quiet)

    if not args.quiet:
        results = report["results"]
        budget = report["budget"]
        verification = report["verification"]
        print("\n--- run complete ---")
        print(f"envelopes        : {results['envelopes']} ({verification['unique_companies']} unique)")
        print(f"terminal states  : {results['terminal_states']}")
        print(f"claims available : {results['claims_available']} across {results['evidence_total']} evidence records")
        print(f"websites verified: {results['verified_websites']} ({results['verified_website_rate']}%)")
        print(f"requests         : {budget['requests_used']}/{budget['max_requests']} ({budget['requests_per_company']}/company)")
        print(f"wall clock       : {budget['seconds_elapsed']}s / {budget['max_seconds']}s")
        print(f"third-party spend: ${budget['spend_usd']:.2f}")
        print(f"one per input    : {verification.get('exactly_one_per_input')}")
        if not verification["ok"]:
            print(f"VERIFICATION ISSUES: {json.dumps({k: v for k, v in verification.items() if k in ('duplicates','missing_count','unexpected_count','invalid_terminal_states')})}")
        print(f"official qualification: {report['official_qualification']}")

    return 0 if report["verification"]["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
