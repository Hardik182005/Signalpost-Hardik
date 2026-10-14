"""Typed envelope, claim, evidence and snapshot schema.

The organiser's minimal contract (OUTPUT_CONTRACT.md) requires an envelope with
organisation_number, run, claims, evidence, changes, errors and operations. This
module keeps that shape exactly and adds documented extensions (identity, synthesis,
refresh, snapshots) as extra top-level keys, which the contract does not forbid.

Availability is spelled with the six organiser states everywhere it appears.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Literal

Availability = Literal[
    "available",
    "not_available",
    "blocked",
    "not_applicable",
    "ambiguous",
    "failed",
]

AVAILABILITY_STATES: frozenset[str] = frozenset(
    {"available", "not_available", "blocked", "not_applicable", "ambiguous", "failed"}
)

# Terminal envelope states. Every input must end in exactly one of these.
TERMINAL_STATES: frozenset[str] = frozenset(
    {"completed", "partial", "not_found", "blocked", "failed"}
)

SourceClass = Literal[
    "official_registry",
    "official_accounts",
    "official_roles",
    "official_subunits",
    "official_group",
    "company_owned",
    "licensed_api",
    "permitted_public",
    "derived",
]

# Field families used by the coverage score. "external" marks families whose recall
# is measured against the pooled external reference collection.
FIELD_FAMILIES: dict[str, dict[str, Any]] = {
    "legal_identity": {"external": False, "label": "Legal identity and public brand"},
    "financials": {"external": False, "label": "Annual accounts and history"},
    "people": {"external": False, "label": "Leadership and roles"},
    "locations": {"external": False, "label": "Registered workplaces and locations"},
    "web_presence": {"external": True, "label": "Verified website and owned profiles"},
    "business_description": {"external": True, "label": "Business description"},
    "contact": {"external": True, "label": "Contact details"},
    "jobs": {"external": True, "label": "Hiring and job postings"},
    "activity": {"external": True, "label": "Dated public activity"},
}

EXTERNAL_FAMILIES: tuple[str, ...] = tuple(
    name for name, meta in FIELD_FAMILIES.items() if meta["external"]
)


def utc_now() -> str:
    """Current time as a UTC RFC3339 string with a Z suffix."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def stable_id(prefix: str, *parts: Any) -> str:
    """Deterministic short id, so reruns over identical bytes produce identical ids."""
    digest = hashlib.sha256("\x1f".join(str(part) for part in parts).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:16]}"


@dataclass(slots=True)
class Locator:
    """Where inside a source a value was actually read.

    A bare URL is not sufficient provenance for a specific number, so every published
    material claim carries one of these.
    """

    kind: Literal[
        "json_pointer", "css_selector", "text_span", "table_cell", "pdf_page", "row_key"
    ]
    value: str
    excerpt: str | None = None

    def to_dict(self) -> dict[str, Any]:
        record: dict[str, Any] = {"kind": self.kind, "value": self.value}
        if self.excerpt:
            record["excerpt"] = self.excerpt[:400]
        return record


@dataclass(slots=True)
class Snapshot:
    """An immutable record of one retrieved source document."""

    id: str
    requested_url: str
    final_url: str
    status: int
    content_type: str | None
    bytes_received: int
    content_sha256: str
    retrieved_at: str
    source_class: str
    redirect_chain: list[str] = field(default_factory=list)
    from_cache: bool = False
    stored_path: str | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "requested_url": self.requested_url,
            "final_url": self.final_url,
            "status": self.status,
            "content_type": self.content_type,
            "bytes_received": self.bytes_received,
            "content_sha256": self.content_sha256,
            "retrieved_at": self.retrieved_at,
            "source_class": self.source_class,
            "redirect_chain": list(self.redirect_chain),
            "from_cache": self.from_cache,
            "stored_path": self.stored_path,
            "note": self.note,
        }


@dataclass(slots=True)
class Evidence:
    """A claim-to-source link: which snapshot, and where inside it."""

    id: str
    snapshot_id: str
    source_url: str
    source_class: str
    retrieved_at: str
    content_sha256: str | None = None
    locator: Locator | None = None
    claim_span: str | None = None
    extraction_method: str | None = None

    def to_dict(self) -> dict[str, Any]:
        span = self.claim_span[:400] if self.claim_span else None
        return {
            "id": self.id,
            "snapshot_id": self.snapshot_id,
            "source_url": self.source_url,
            "source_class": self.source_class,
            "retrieved_at": self.retrieved_at,
            "content_sha256": self.content_sha256,
            "locator": self.locator.to_dict() if self.locator else None,
            "claim_span": span,
            "extraction_method": self.extraction_method,
        }


@dataclass(slots=True)
class Claim:
    """One typed statement about one company.

    claim_key is a stable semantic identity used by refresh. It must not embed
    retrieval timestamps, HTML whitespace, ordering or tracking parameters, so that a
    re-run over unchanged sources produces zero false changes.
    """

    claim_key: str
    field: str
    family: str
    value: Any
    availability: Availability
    organisation_number: str
    evidence_ids: list[str] = field(default_factory=list)
    confidence: float | None = None
    reporting_period: str | None = None
    effective_date: str | None = None
    scope: Literal["company", "group", "subunit"] = "company"
    unit: str | None = None
    currency: str | None = None
    reason: str | None = None
    derived_from: list[str] = field(default_factory=list)
    identity_ref: str | None = None
    extraction_method: str | None = None
    first_observed_at: str | None = None
    last_observed_at: str | None = None

    def __post_init__(self) -> None:
        if self.availability not in AVAILABILITY_STATES:
            raise ValueError(f"Invalid availability state: {self.availability!r}")
        if self.family not in FIELD_FAMILIES:
            raise ValueError(f"Unknown field family: {self.family!r}")
        if self.availability == "available" and self.value is None:
            raise ValueError(f"Claim {self.claim_key!r} is available but carries no value")
        if self.availability != "available" and self.value is not None:
            raise ValueError(
                f"Claim {self.claim_key!r} is {self.availability} and must not carry a value"
            )

    @property
    def is_material(self) -> bool:
        """Material claims are published facts that must carry resolvable evidence."""
        return self.availability == "available"

    def to_dict(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "claim_key": self.claim_key,
            "organisation_number": self.organisation_number,
            "field": self.field,
            "family": self.family,
            "value": self.value,
            "availability": self.availability,
            "scope": self.scope,
            "evidence_ids": list(self.evidence_ids),
        }
        for name in (
            "confidence",
            "reporting_period",
            "effective_date",
            "unit",
            "currency",
            "reason",
            "identity_ref",
            "extraction_method",
            "first_observed_at",
            "last_observed_at",
        ):
            value = getattr(self, name)
            if value is not None:
                record[name] = value
        if self.derived_from:
            record["derived_from"] = list(self.derived_from)
        return record


def claim_key(organisation_number: str, field_name: str, *discriminators: Any) -> str:
    """Build a stable semantic claim key.

    Discriminators identify which instance of a repeated field this is (a specific job
    title, a role holder, an accounting year), never how or when it was fetched.
    """
    parts = [str(organisation_number), field_name]
    for item in discriminators:
        text = re.sub(r"\s+", " ", str(item or "")).strip().casefold()
        if text:
            parts.append(text)
    return ":".join(parts)


@dataclass(slots=True)
class CompanyResult:
    """Everything the pipeline produced for one organisation number."""

    organisation_number: str
    terminal_status: str
    claims: list[Claim] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    snapshots: list[Snapshot] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    identity: dict[str, Any] = field(default_factory=dict)
    synthesis: dict[str, Any] = field(default_factory=dict)
    changes: list[dict[str, Any]] = field(default_factory=list)
    refresh: dict[str, Any] = field(default_factory=dict)
    operations: dict[str, Any] = field(default_factory=dict)
    started_at: str = field(default_factory=utc_now)
    completed_at: str | None = None

    def claim(self, key: str) -> Claim | None:
        return next((item for item in self.claims if item.claim_key == key), None)

    def available(self, field_name: str) -> list[Claim]:
        return [
            item
            for item in self.claims
            if item.field == field_name and item.availability == "available"
        ]

    def first_value(self, field_name: str) -> Any:
        found = self.available(field_name)
        return found[0].value if found else None


def validate_result(result: CompanyResult) -> list[str]:
    """Integrity checks that must hold before an envelope may be published."""
    problems: list[str] = []
    if result.terminal_status not in TERMINAL_STATES:
        problems.append(f"non-terminal status {result.terminal_status!r}")

    evidence_ids = {item.id for item in result.evidence}
    snapshot_ids = {item.id for item in result.snapshots}

    seen_keys: set[str] = set()
    for claim in result.claims:
        if claim.claim_key in seen_keys:
            problems.append(f"duplicate claim key {claim.claim_key!r}")
        seen_keys.add(claim.claim_key)
        if claim.organisation_number != result.organisation_number:
            problems.append(f"claim {claim.claim_key!r} attributed to another company")
        missing = [item for item in claim.evidence_ids if item not in evidence_ids]
        if missing:
            problems.append(f"claim {claim.claim_key!r} references unknown evidence {missing}")
        if claim.is_material and not claim.evidence_ids and not claim.derived_from:
            problems.append(f"material claim {claim.claim_key!r} has no evidence")

    for item in result.evidence:
        if item.snapshot_id not in snapshot_ids:
            problems.append(
                f"evidence {item.id!r} references unknown snapshot {item.snapshot_id!r}"
            )

    return problems


def build_envelope(
    result: CompanyResult,
    *,
    run_id: str,
    started_at: str,
    completed_at: str,
    pipeline_version: str,
    include_snapshots: bool = True,
) -> dict[str, Any]:
    """Render the organiser-compatible terminal envelope."""
    return {
        "schema_version": "signalpost.envelope/1",
        "organisation_number": result.organisation_number,
        "run": {
            "run_id": run_id,
            "started_at": started_at,
            "completed_at": completed_at,
            "terminal_status": result.terminal_status,
            "pipeline_version": pipeline_version,
        },
        "identity": result.identity,
        "claims": [claim.to_dict() for claim in result.claims],
        "evidence": [item.to_dict() for item in result.evidence],
        "snapshots": [item.to_dict() for item in result.snapshots] if include_snapshots else [],
        "synthesis": result.synthesis,
        "changes": list(result.changes),
        "refresh": result.refresh,
        "errors": list(result.errors),
        "operations": result.operations,
    }


def coverage_summary(claims: Iterable[Claim]) -> dict[str, Any]:
    """Local field coverage. This is our own measurement, not an official score."""
    families: dict[str, dict[str, int]] = {}
    for claim in claims:
        bucket = families.setdefault(
            claim.family,
            {
                "available": 0,
                "not_available": 0,
                "blocked": 0,
                "ambiguous": 0,
                "failed": 0,
                "not_applicable": 0,
            },
        )
        bucket[claim.availability] = bucket.get(claim.availability, 0) + 1
    return families
