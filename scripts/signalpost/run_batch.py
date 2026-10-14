#!/usr/bin/env python3
"""Evaluator entry point: run an organiser-supplied batch of organisation numbers.

    uv run python scripts/signalpost/run_batch.py \
        --organisations <reviewer-batch.jsonl> \
        --output out/envelopes.jsonl --run-id eval-001 --expected-count 100

Accepts the starter's input forms: a `.jsonl` of objects with `organisation_number` (or bare
values), a `.json` list or `{"organisation_numbers": [...]}`, or a text file with one number
per line. Numbers may be strings or integers; separators such as spaces are removed. As in
the starter, a row that is not nine digits, a duplicate, or a count different from
`--expected-count` refuses the batch before any request is made, so a malformed file is
never half-run.

The accepted numbers are written, in input order, to `<output stem>-inputs.txt` and handed
to the unchanged research runner (`main.py`). Every other option (`--previous-snapshot`,
`--snapshots`, `--cutoff`, `--max-requests`, `--max-seconds`, `--max-spend-usd`,
`--concurrency`, `--no-resume`, `--offline`, ...) passes straight through; see
`uv run main.py --help`. Outputs: `<output>` (one terminal envelope per input) and
`<output stem>-report.json` (run report). Exit status is the runner's: 0 when every input
has exactly one terminal envelope.

Registry facts are read live from the Brønnøysund open-data API; there is no `--bulk`
registry-file option.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass


class BatchError(ValueError):
    """The batch file cannot be run as given."""


def read_organisations(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix == ".json":
        body = json.loads(text)
        values = body if isinstance(body, list) else body.get("organisation_numbers", [])
    elif path.suffix == ".jsonl":
        values = []
        for number, line in enumerate(text.splitlines(), start=1):
            if line.strip():
                try:
                    values.append(json.loads(line))
                except json.JSONDecodeError as error:
                    raise BatchError(f"{path.name} line {number} is not JSON: {error}") from None
    else:
        values = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]

    numbers: list[str] = []
    seen: set[str] = set()
    for value in values:
        raw = value.get("organisation_number") if isinstance(value, dict) else value
        if isinstance(raw, bool) or raw is None:
            raise BatchError(f"Invalid Norwegian organisation number: {value!r}")
        org = "".join(character for character in str(raw) if character.isdigit())
        if len(org) != 9:
            raise BatchError(f"Invalid Norwegian organisation number: {value!r}")
        if org in seen:
            raise BatchError(f"Organisation-number input contains a duplicate: {org}")
        seen.add(org)
        numbers.append(org)
    return numbers


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog="Any other option is passed to the research runner; see `uv run main.py --help`.",
    )
    parser.add_argument("--organisations", required=True, type=Path, help="JSONL, JSON or text list of organisation numbers")
    parser.add_argument("-o", "--output", required=True, help="Envelope output path (.jsonl)")
    parser.add_argument("--expected-count", type=int, default=None, help="Refuse the batch unless it has exactly this many numbers")
    return parser


def main(argv: list[str] | None = None) -> int:
    args, passthrough = build_parser().parse_known_args(argv)
    try:
        numbers = read_organisations(args.organisations)
    except (OSError, BatchError, json.JSONDecodeError) as error:
        print(f"batch refused: {error}", file=sys.stderr)
        return 2
    if not numbers:
        print("batch refused: no organisation numbers in the input", file=sys.stderr)
        return 2
    if args.expected_count is not None and len(numbers) != args.expected_count:
        print(f"batch refused: expected {args.expected_count} organisations, received {len(numbers)}", file=sys.stderr)
        return 2
    if any(option in ("-i", "--input", "--limit") or option.startswith(("--input=", "--limit=")) for option in passthrough):
        print("batch refused: --input and --limit are set by this wrapper from --organisations", file=sys.stderr)
        return 2

    output = Path(args.output)
    inputs_file = output.with_name(f"{output.stem}-inputs.txt")
    inputs_file.parent.mkdir(parents=True, exist_ok=True)
    inputs_file.write_text("".join(f"{org}\n" for org in numbers), encoding="utf-8", newline="\n")

    from signalpost.runner import main as run  # noqa: E402  (after path setup)

    return run(["--input", str(inputs_file), "--output", str(output), *passthrough])


if __name__ == "__main__":
    raise SystemExit(main())
