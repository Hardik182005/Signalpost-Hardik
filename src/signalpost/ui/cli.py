"""Command line for the read-only run explorer.

The interface is pointed at a file, never at a hardcoded one: ``--input`` takes any
envelope ``.jsonl`` a run has written, so the same server can open the 100-company
evaluation run, a refreshed run or a scratch job's output without a code change.

Two flags carry a deliberate default. ``--enable-research`` is off, because a server
that browses finished output should not be able to start outbound traffic by
accident; and the research scratch directory is separate from the run output, so
nothing this server does can land beside an evaluator's file.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .app import AppConfig, create_app

DEFAULT_INPUT = "out/submission-profiles.jsonl"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="serve.py",
        description="Serve a read-only web interface over a Signalpost run's output.",
    )
    parser.add_argument(
        "-i",
        "--input",
        default=DEFAULT_INPUT,
        help=f"Envelope .jsonl file to load (default: {DEFAULT_INPUT})",
    )
    parser.add_argument(
        "--reports-dir",
        default=None,
        help="Directory holding *-report.json files (default: the input file's directory)",
    )
    parser.add_argument(
        "--baseline",
        default=None,
        help="Previous run's .jsonl, so both sides of a change can open their evidence",
    )
    parser.add_argument(
        "--scratch-dir",
        default="out/ui-scratch",
        help="Directory for UI-initiated research output (default: out/ui-scratch)",
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=None,
        help="Load at most N envelopes (useful when pointing at a very large run)",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Bind address (default: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port (default: {DEFAULT_PORT})")
    parser.add_argument(
        "--enable-research",
        action="store_true",
        help="Allow the interface to run the real pipeline into the scratch directory",
    )
    parser.add_argument(
        "--research-offline",
        action="store_true",
        help="With --enable-research, run the pipeline with no outbound requests",
    )
    return parser


def build_config(args: argparse.Namespace) -> AppConfig:
    return AppConfig(
        input_path=Path(args.input),
        reports_dir=Path(args.reports_dir) if args.reports_dir else None,
        baseline_path=Path(args.baseline) if args.baseline else None,
        scratch_dir=Path(args.scratch_dir),
        enable_research=bool(args.enable_research),
        research_offline=bool(args.research_offline),
        max_records=args.max_records,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.research_offline and not args.enable_research:
        print("--research-offline only applies with --enable-research; research stays disabled.")

    config = build_config(args)
    app = create_app(config)

    dataset = app.state.signalpost.dataset
    if dataset.load_error:
        # Not fatal: the interface starts and says on every page what is wrong, which
        # is more useful than a traceback when the path is simply mistyped.
        print(f"Warning: {dataset.load_error}")
    else:
        print(f"Loaded {dataset.count} companies from {config.input_path}")
    reports = app.state.signalpost.reports
    print(f"Run reports found: {len(reports)} in {config.resolved_reports_dir}")
    print(
        "Research action: "
        + ("enabled, writing to " + str(config.scratch_dir) if config.enable_research else "disabled (read-only)")
    )
    print(f"Serving on http://{args.host}:{args.port}/  (Ctrl+C to stop)")

    import uvicorn  # imported here so --help works without the optional UI extras

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0
