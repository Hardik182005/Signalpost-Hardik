"""Durable run output.

A batch run is long and can be interrupted — by the wall-clock budget, by a crash, by
the operator. The rules that follow from that:

* **Append, flush, fsync.** Each envelope is durable the moment it is written, so an
  interrupted run keeps everything it had already completed.
* **Resumable.** `completed_numbers()` reads back what a previous attempt finished, so
  a re-run continues rather than starting over and re-spending the request budget.
* **One envelope per organisation number.** The evaluator counts envelopes, and a
  duplicate is as much a defect as a missing one. `RunStore` enforces uniqueness on
  write and `verify()` proves it afterwards.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from .models import TERMINAL_STATES, utc_now


def read_envelopes(path: Path) -> Iterator[dict[str, Any]]:
    """Stream envelopes from a JSONL file, skipping any truncated final line."""
    if not path.exists():
        return
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                # A run killed mid-write leaves a partial last line. Everything before
                # it is intact, so the sane action is to drop it and carry on.
                continue


def completed_numbers(path: Path) -> set[str]:
    """Organisation numbers already terminated in a previous attempt."""
    return {
        str(envelope.get("organisation_number"))
        for envelope in read_envelopes(path)
        if envelope.get("organisation_number")
    }


def prepare_output(path: Path, *, run_id: str, inputs: list[str], resume: bool) -> Path | None:
    """Decide whether an existing output file may be resumed; set it aside if not.

    Resuming is only right for an interrupted attempt at *this* run: the same run id,
    and only companies this run was asked for. Anything else in the file — yesterday's
    batch written to the same path, or a run over different companies — would be handed
    back as this run's answer, stale and possibly for companies nobody asked about. Such
    a file is renamed beside the output, never deleted, so it stays available as a
    previous snapshot. Returns the path it was moved to, if it was moved.
    """
    if not path.exists() or path.stat().st_size == 0:
        return None
    wanted = set(inputs)
    resumable = resume and all(
        (envelope.get("run") or {}).get("run_id") == run_id
        and str(envelope.get("organisation_number")) in wanted
        for envelope in read_envelopes(path)
    )
    if resumable:
        return None
    stamp = utc_now().replace(":", "").replace("-", "")
    target = path.with_name(f"{path.stem}.superseded-{stamp}{path.suffix}")
    path.replace(target)
    return target


def load_previous(path: Path) -> dict[str, dict[str, Any]]:
    """Index a previous run's envelopes by organisation number, for refresh."""
    previous: dict[str, dict[str, Any]] = {}
    for envelope in read_envelopes(path):
        org = str(envelope.get("organisation_number") or "")
        if org:
            previous[org] = envelope
    return previous


@dataclass(slots=True)
class RunStore:
    """Append-only envelope writer, safe for concurrent workers."""

    path: Path
    _handle: Any = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _seen: set[str] = field(default_factory=set)
    written: int = 0

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seen = completed_numbers(self.path)
        self.written = len(self._seen)
        self._handle = open(self.path, "a", encoding="utf-8", newline="\n")

    def has(self, organisation_number: str) -> bool:
        with self._lock:
            return organisation_number in self._seen

    def write(self, envelope: dict[str, Any]) -> bool:
        """Write one envelope. Returns False if this company was already written."""
        org = str(envelope.get("organisation_number") or "")
        if not org:
            raise ValueError("Envelope has no organisation_number")
        with self._lock:
            if org in self._seen:
                return False
            self._handle.write(json.dumps(envelope, ensure_ascii=False) + "\n")
            self._handle.flush()
            os.fsync(self._handle.fileno())
            self._seen.add(org)
            self.written += 1
            return True

    def close(self) -> None:
        with self._lock:
            if self._handle is not None:
                self._handle.flush()
                os.fsync(self._handle.fileno())
                self._handle.close()
                self._handle = None

    def __enter__(self) -> "RunStore":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def write_json(path: Path, payload: Any) -> Path:
    """Write JSON atomically: a crash mid-write must not corrupt an existing report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return path


def verify(path: Path, expected: list[str] | None = None) -> dict[str, Any]:
    """Check a completed run's output against the contract the evaluator enforces.

    Reports rather than raises: a verification failure is information the operator
    needs in full, not an exception that hides the rest of the findings.
    """
    seen: list[str] = []
    duplicates: list[str] = []
    bad_state: list[str] = []
    missing_keys: list[str] = []
    unique: set[str] = set()
    states: dict[str, int] = {}
    required = ("organisation_number", "run", "claims", "evidence", "changes", "errors", "operations")

    for envelope in read_envelopes(path):
        org = str(envelope.get("organisation_number") or "")
        seen.append(org)
        if org in unique:
            duplicates.append(org)
        unique.add(org)

        absent = [key for key in required if key not in envelope]
        if absent:
            missing_keys.append(f"{org}: missing {', '.join(absent)}")

        state = (envelope.get("run") or {}).get("terminal_status")
        states[str(state)] = states.get(str(state), 0) + 1
        if state not in TERMINAL_STATES:
            bad_state.append(f"{org}: {state!r}")

    report: dict[str, Any] = {
        "checked_at": utc_now(),
        "path": str(path).replace("\\", "/"),
        "envelopes": len(seen),
        "unique_companies": len(unique),
        "terminal_states": dict(sorted(states.items())),
        "duplicates": duplicates[:20],
        "invalid_terminal_states": bad_state[:20],
        "envelopes_missing_required_keys": missing_keys[:20],
    }

    if expected is not None:
        wanted = set(expected)
        report["expected"] = len(wanted)
        report["missing"] = sorted(wanted - unique)[:50]
        report["missing_count"] = len(wanted - unique)
        report["unexpected"] = sorted(unique - wanted)[:50]
        report["unexpected_count"] = len(unique - wanted)
        report["exactly_one_per_input"] = (
            not duplicates and not (wanted - unique) and not (unique - wanted)
        )

    report["ok"] = (
        not duplicates
        and not bad_state
        and not missing_keys
        and (expected is None or report.get("exactly_one_per_input", False))
    )
    return report
