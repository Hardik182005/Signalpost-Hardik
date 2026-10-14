"""The one part of the interface that performs new work, and its guard rails.

Everything else in the UI reads finished run output. Research is different: it runs
the real pipeline. That makes it the place where an interface can quietly become a
liability, so the constraints are explicit:

* **Off unless the operator turns it on.** Without ``--enable-research`` the endpoint
  refuses with a stated reason. A browsing reviewer cannot start outbound traffic.
* **One run at a time, with a bounded budget.** A single worker thread and a request
  ceiling derived from the batch size mean the action cannot be used to amplify
  traffic at anyone.
* **Validated before anything is fetched.** Organisation numbers are checked against
  the MOD-11 control digit the registry uses; uploads are bounded in bytes and in
  records. Input that fails is reported per line and never reaches the pipeline.
* **Writes only to its own scratch directory.** Research output never lands beside
  the evaluator's run output.
* **Progress is measured, not animated.** The completed count is the number of
  terminal envelopes actually on disk.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any

from ..identity import valid_org_number
from ..models import utc_now
from ..runner import BatchRunner, RunConfig
from ..store import read_envelopes

# Upload limits. Both are enforced before the body is parsed.
MAX_UPLOAD_BYTES = 1_048_576
MAX_UPLOAD_RECORDS = 500

# Per-company request ceiling for a UI-initiated run. The evaluator run measured
# about 8 requests per company; this leaves headroom without allowing a blow-out.
REQUESTS_PER_COMPANY = 40
DEFAULT_JOB_SECONDS = 600.0


class ResearchDisabled(RuntimeError):
    """Raised when a research action is attempted on a read-only server."""


@dataclass(slots=True)
class ParsedBatch:
    """The result of validating an uploaded batch, before any research happens."""

    accepted: list[str] = dc_field(default_factory=list)
    rejected: list[dict[str, Any]] = dc_field(default_factory=list)
    duplicates: list[str] = dc_field(default_factory=list)
    total_lines: int = 0
    truncated: bool = False
    byte_size: int = 0

    @property
    def ok(self) -> bool:
        return bool(self.accepted)


def parse_batch(raw: bytes, *, max_records: int = MAX_UPLOAD_RECORDS) -> ParsedBatch:
    """Read organisation numbers from a JSONL or plain-text upload.

    Accepts one JSON object per line with an ``organisation_number`` (or ``orgnr``)
    key, or a bare number per line. Every rejection keeps its line number so the
    operator can fix the file rather than guess.
    """
    result = ParsedBatch(byte_size=len(raw))
    if len(raw) > MAX_UPLOAD_BYTES:
        result.rejected.append(
            {
                "line": 0,
                "value": "",
                "reason": f"File is {len(raw)} bytes; the limit is {MAX_UPLOAD_BYTES} bytes",
            }
        )
        return result

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        result.rejected.append({"line": 0, "value": "", "reason": "File is not valid UTF-8 text"})
        return result

    seen: set[str] = set()
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip().lstrip("﻿")
        if not stripped:
            continue
        result.total_lines += 1
        if len(result.accepted) >= max_records:
            result.truncated = True
            break

        candidate = stripped
        if stripped.startswith("{"):
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                result.rejected.append({"line": number, "value": stripped[:80], "reason": "Not valid JSON"})
                continue
            if not isinstance(record, dict):
                result.rejected.append({"line": number, "value": stripped[:80], "reason": "JSON line is not an object"})
                continue
            candidate = str(record.get("organisation_number") or record.get("orgnr") or "").strip()
            if not candidate:
                result.rejected.append(
                    {"line": number, "value": stripped[:80], "reason": "No organisation_number field"}
                )
                continue

        digits = "".join(char for char in candidate if char.isdigit())
        if not valid_org_number(digits):
            result.rejected.append(
                {
                    "line": number,
                    "value": candidate[:80],
                    "reason": "Not a valid Norwegian organisation number (MOD-11 check failed)",
                }
            )
            continue
        if digits in seen:
            result.duplicates.append(digits)
            continue
        seen.add(digits)
        result.accepted.append(digits)
    return result


@dataclass(slots=True)
class Job:
    """One UI-initiated pipeline run."""

    id: str
    kind: str
    inputs: list[str]
    output: Path
    created_at: str
    state: str = "queued"
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    report: dict[str, Any] | None = None
    offline: bool = False

    @property
    def report_path(self) -> Path:
        return self.output.with_name(self.output.stem + "-report.json")

    @property
    def total(self) -> int:
        return len(self.inputs)

    @property
    def completed(self) -> int:
        """Envelopes actually written. Not an estimate and not a timer."""
        try:
            return sum(1 for _ in read_envelopes(self.output))
        except OSError:
            return 0

    @property
    def finished(self) -> bool:
        return self.state in {"completed", "failed"}

    def to_dict(self) -> dict[str, Any]:
        completed = self.completed
        return {
            "id": self.id,
            "kind": self.kind,
            "state": self.state,
            "total": self.total,
            "completed": completed,
            "remaining": max(0, self.total - completed),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "offline": self.offline,
            "output": str(self.output).replace("\\", "/"),
            "organisation_numbers": list(self.inputs),
            "report": self.report,
        }


class JobManager:
    """Runs and tracks UI-initiated pipeline jobs. Serialised to one at a time."""

    def __init__(
        self,
        *,
        scratch_dir: Path,
        enabled: bool,
        offline: bool = False,
        max_seconds: float = DEFAULT_JOB_SECONDS,
    ) -> None:
        self.scratch_dir = Path(scratch_dir)
        self.enabled = bool(enabled)
        self.offline = bool(offline)
        self.max_seconds = float(max_seconds)
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="signalpost-ui-job")

    # -- inspection -----------------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self, limit: int = 20) -> list[Job]:
        with self._lock:
            jobs = list(self._jobs.values())
        jobs.sort(key=lambda job: job.created_at, reverse=True)
        return jobs[:limit]

    @property
    def active(self) -> int:
        with self._lock:
            return sum(1 for job in self._jobs.values() if not job.finished)

    # -- submission -----------------------------------------------------------

    def submit(self, organisation_numbers: list[str], *, kind: str) -> Job:
        if not self.enabled:
            raise ResearchDisabled(
                "Research is disabled on this server. Start it with --enable-research "
                "to allow the interface to run the pipeline."
            )
        if not organisation_numbers:
            raise ValueError("No valid organisation numbers to research")
        if self.active:
            raise RuntimeError("A research job is already running; wait for it to finish")

        self.scratch_dir.mkdir(parents=True, exist_ok=True)
        job_id = f"ui-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:6]}"
        job = Job(
            id=job_id,
            kind=kind,
            inputs=list(organisation_numbers),
            output=self.scratch_dir / f"{job_id}.jsonl",
            created_at=utc_now(),
            offline=self.offline,
        )
        with self._lock:
            self._jobs[job.id] = job
        self._pool.submit(self._run, job)
        return job

    def _run(self, job: Job) -> None:
        job.state = "running"
        job.started_at = utc_now()
        try:
            config = RunConfig(
                inputs=list(job.inputs),
                output=job.output,
                run_id=job.id,
                concurrency=min(4, max(1, len(job.inputs))),
                max_requests=REQUESTS_PER_COMPANY * len(job.inputs),
                max_seconds=self.max_seconds,
                max_spend_usd=0.0,
                max_pages=5,
                resume=False,
                offline=self.offline,
            )
            job.report = BatchRunner(config).run(progress=False)
            job.state = "completed"
        except Exception as error:  # noqa: BLE001 - surfaced to the operator verbatim
            job.state = "failed"
            job.error = f"{type(error).__name__}: {error}"
        finally:
            job.finished_at = utc_now()

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
