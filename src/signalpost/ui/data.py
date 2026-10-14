"""Load a run's envelopes and expose them as a queryable, read-only dataset.

Three things decide the shape of this module:

* **Nothing is invented.** Every attribute on a row traces back to a claim that the
  run actually wrote. A company with no ``employees`` claim is not "0 employees";
  it carries a distinct ``not_recorded`` state so the interface can render the two
  differently. The same applies to the six organiser availability states, which are
  carried through untouched rather than collapsed into present/absent.

* **The file is opened once and never written.** Run output is treated as immutable
  input. The dataset is read into memory at startup (a 1,000-company envelope file
  is tens of megabytes) and re-read only when the operator asks for it.

* **Queries are validated on the server.** The explorer's filters, sort keys, page
  numbers and page sizes are parsed and checked here, not in the browser, and an
  invalid request produces a stated error rather than a silently empty table.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from ..models import AVAILABILITY_STATES, FIELD_FAMILIES, TERMINAL_STATES
from ..store import read_envelopes

# A seventh state, local to the interface: the run never emitted a claim for this
# field at all. That is genuinely different from "the source said it is absent",
# and conflating the two is how a UI ends up implying facts it does not have.
NOT_RECORDED = "not_recorded"

AVAILABILITY_LABELS: dict[str, str] = {
    "available": "Available",
    "not_available": "Not available",
    "blocked": "Blocked",
    "not_applicable": "Not applicable",
    "ambiguous": "Ambiguous",
    "failed": "Failed",
    NOT_RECORDED: "Not recorded",
}

AVAILABILITY_HELP: dict[str, str] = {
    "available": "The run read this value from a source and recorded evidence for it.",
    "not_available": "The source was read successfully and did not carry this field.",
    "blocked": "The source refused access, so the value could not be read.",
    "not_applicable": "This field does not apply to this company.",
    "ambiguous": "Evidence was found but was not strong enough to publish.",
    "failed": "The attempt to read this field errored.",
    NOT_RECORDED: "This run emitted no claim for this field.",
}

TERMINAL_LABELS: dict[str, str] = {
    "completed": "Completed",
    "partial": "Partial",
    "not_found": "Not found",
    "blocked": "Blocked",
    "failed": "Failed",
}

FAMILY_LABELS: dict[str, str] = {name: meta["label"] for name, meta in FIELD_FAMILIES.items()}

FIELD_LABELS: dict[str, str] = {
    "legal_name": "Legal name",
    "organisation_number": "Organisation number",
    "legal_form_code": "Legal form code",
    "legal_form_label": "Legal form",
    "registered_at": "Registered in Enhetsregisteret",
    "incorporated_at": "Incorporated",
    "industry_code": "Industry code (NACE)",
    "industry_label": "Industry",
    "sector_code": "Institutional sector",
    "vat_registered": "Registered for VAT",
    "bankrupt": "Bankrupt",
    "under_liquidation": "Under liquidation",
    "under_forced_liquidation": "Under forced liquidation",
    "employees": "Registered employees",
    "registry_website": "Website stated in the registry",
    "former_name": "Former name",
    "revenue": "Revenue",
    "operating_costs": "Operating costs",
    "operating_profit": "Operating profit",
    "profit_before_tax": "Profit before tax",
    "net_profit": "Net profit",
    "total_assets": "Total assets",
    "total_equity": "Total equity",
    "total_equity_and_liabilities": "Total equity and liabilities",
    "accounts_audited": "Accounts audited",
    "annual_accounts": "Annual accounts",
    "role": "Role",
    "leadership": "Leadership",
    "address": "Address",
    "subunit": "Subunit",
    "subunit_count": "Subunits",
    "website": "Verified website",
    "social_profile": "Owned profile",
    "business_description": "Business description",
    "email": "Email",
    "phone": "Phone",
    "site_address": "Address stated on the website",
    "site_contact": "Contact details from the website",
    "job_posting": "Job posting",
    "job_postings": "Job postings",
    "is_hiring": "Hiring signal on the website",
    "activity": "Public activity",
    "activity_item": "Public activity",
}

# Fields whose single claim summarises a whole section's outcome. When they are not
# `available` they explain why a section is empty, which is what the UI shows.
SECTION_STATUS_FIELDS: frozenset[str] = frozenset(
    {"annual_accounts", "leadership", "activity", "site_contact", "job_postings"}
)

SORT_KEYS: dict[str, str] = {
    "name": "Company name",
    "organisation_number": "Organisation number",
    "employees": "Employees",
    "revenue": "Latest revenue",
    "claims_available": "Available claims",
    "evidence": "Evidence records",
    "last_checked": "Last checked",
    "status": "Terminal status",
    "changes": "Changes detected",
}

PAGE_SIZES: tuple[int, ...] = (10, 25, 50, 100)
DEFAULT_PAGE_SIZE = 25
MAX_COMPARE = 3
MIN_COMPARE = 2


def field_label(name: str) -> str:
    return FIELD_LABELS.get(name, name.replace("_", " ").capitalize())


class QueryError(ValueError):
    """A request that failed server-side validation, with a message worth showing."""


# -- facts --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Fact:
    """One field of one company, carrying its availability and its provenance."""

    field: str
    label: str
    value: Any = None
    availability: str = NOT_RECORDED
    claim_key: str | None = None
    evidence_ids: tuple[str, ...] = ()
    reason: str | None = None
    reporting_period: str | None = None
    effective_date: str | None = None
    currency: str | None = None
    unit: str | None = None
    scope: str = "company"
    confidence: float | None = None
    extraction_method: str | None = None
    identity_ref: str | None = None
    first_observed_at: str | None = None
    last_observed_at: str | None = None

    @property
    def known(self) -> bool:
        return self.availability == "available"

    @property
    def availability_label(self) -> str:
        return AVAILABILITY_LABELS.get(self.availability, self.availability)

    @property
    def availability_help(self) -> str:
        return AVAILABILITY_HELP.get(self.availability, "")


def fact_from_claim(claim: dict[str, Any]) -> Fact:
    name = str(claim.get("field") or "")
    availability = str(claim.get("availability") or NOT_RECORDED)
    if availability not in AVAILABILITY_STATES:
        availability = NOT_RECORDED
    confidence = claim.get("confidence")
    return Fact(
        field=name,
        label=field_label(name),
        value=claim.get("value"),
        availability=availability,
        claim_key=claim.get("claim_key"),
        evidence_ids=tuple(str(item) for item in (claim.get("evidence_ids") or [])),
        reason=claim.get("reason"),
        reporting_period=claim.get("reporting_period"),
        effective_date=claim.get("effective_date"),
        currency=claim.get("currency"),
        unit=claim.get("unit"),
        scope=str(claim.get("scope") or "company"),
        confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
        extraction_method=claim.get("extraction_method"),
        identity_ref=claim.get("identity_ref"),
        first_observed_at=claim.get("first_observed_at"),
        last_observed_at=claim.get("last_observed_at"),
    )


def missing_fact(name: str) -> Fact:
    return Fact(field=name, label=field_label(name), availability=NOT_RECORDED)


# -- rows ---------------------------------------------------------------------


@dataclass(slots=True)
class CompanyRow:
    """The explorer's view of one company. Derived only from that company's claims."""

    organisation_number: str
    name: str | None
    names: tuple[str, ...]
    headline: str
    terminal_status: str
    run_id: str
    started_at: str | None
    completed_at: str | None
    confidence: str
    city: str | None
    municipality: str | None
    industry_code: str | None
    industry_label: str | None
    legal_form: str | None
    employees: Fact
    revenue: Fact
    website: Fact
    claims_total: int
    claims_available: int
    evidence_count: int
    snapshot_count: int
    error_count: int
    changes_total: int
    changes_material: int
    last_checked: str | None
    families: dict[str, dict[str, int]]
    search_blob: str

    @property
    def display_name(self) -> str:
        return self.name or f"Organisation {self.organisation_number}"

    @property
    def status_label(self) -> str:
        return TERMINAL_LABELS.get(self.terminal_status, self.terminal_status)


def _first_available(claims: Sequence[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for claim in claims:
        if claim.get("field") == name and claim.get("availability") == "available":
            return claim
    return None


def _first_claim(claims: Sequence[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for claim in claims:
        if claim.get("field") == name:
            return claim
    return None


def _latest_financial(claims: Sequence[dict[str, Any]], name: str) -> dict[str, Any] | None:
    """The most recent reporting period for a financial line, by period end."""
    candidates = [
        claim
        for claim in claims
        if claim.get("field") == name and claim.get("availability") == "available"
    ]
    if not candidates:
        return _first_claim(claims, name)

    def period_end(claim: dict[str, Any]) -> str:
        period = str(claim.get("reporting_period") or "")
        if "/" in period:
            return period.split("/")[-1]
        return str(claim.get("effective_date") or period)

    return max(candidates, key=period_end)


def build_row(envelope: dict[str, Any]) -> CompanyRow:
    claims: list[dict[str, Any]] = list(envelope.get("claims") or [])
    identity = envelope.get("identity") or {}
    synthesis = envelope.get("synthesis") or {}
    run = envelope.get("run") or {}
    refresh = envelope.get("refresh") or {}
    org = str(envelope.get("organisation_number") or "")

    legal_name_claim = _first_available(claims, "legal_name")
    name = str(legal_name_claim["value"]) if legal_name_claim else None
    names = tuple(str(item) for item in (identity.get("names") or []) if item)
    if name and name not in names:
        names = (name,) + names

    address_claim = _first_available(claims, "address")
    address_value = address_claim.get("value") if address_claim else None
    city = municipality = None
    if isinstance(address_value, dict):
        city = address_value.get("city") or None
        municipality = address_value.get("municipality") or None
    if not city:
        cities = identity.get("cities") or []
        city = str(cities[0]).upper() if cities else None

    industry_code_claim = _first_available(claims, "industry_code")
    industry_label_claim = _first_available(claims, "industry_label")
    legal_form_claim = _first_available(claims, "legal_form_label")

    employees = _first_claim(claims, "employees")
    revenue = _latest_financial(claims, "revenue")
    website = _first_claim(claims, "website")

    families: dict[str, dict[str, int]] = {}
    for claim in claims:
        family = str(claim.get("family") or "")
        state = str(claim.get("availability") or NOT_RECORDED)
        families.setdefault(family, {})[state] = families.setdefault(family, {}).get(state, 0) + 1

    observed = [
        str(claim.get("last_observed_at"))
        for claim in claims
        if claim.get("last_observed_at")
    ]
    last_checked = max(observed) if observed else (run.get("completed_at") or None)

    searchable = " ".join(
        part
        for part in (
            org,
            *(names or ()),
            str(synthesis.get("headline") or ""),
            str(industry_label_claim["value"]) if industry_label_claim else "",
            city or "",
        )
        if part
    ).casefold()

    return CompanyRow(
        organisation_number=org,
        name=name,
        names=names,
        headline=str(synthesis.get("headline") or "") or (name or f"Organisation {org}"),
        terminal_status=str(run.get("terminal_status") or "unknown"),
        run_id=str(run.get("run_id") or ""),
        started_at=run.get("started_at"),
        completed_at=run.get("completed_at"),
        confidence=str(synthesis.get("confidence") or "none"),
        city=city,
        municipality=municipality,
        industry_code=str(industry_code_claim["value"]) if industry_code_claim else None,
        industry_label=str(industry_label_claim["value"]) if industry_label_claim else None,
        legal_form=str(legal_form_claim["value"]) if legal_form_claim else None,
        employees=fact_from_claim(employees) if employees else missing_fact("employees"),
        revenue=fact_from_claim(revenue) if revenue else missing_fact("revenue"),
        website=fact_from_claim(website) if website else missing_fact("website"),
        claims_total=len(claims),
        claims_available=sum(1 for claim in claims if claim.get("availability") == "available"),
        evidence_count=len(envelope.get("evidence") or []),
        snapshot_count=len({str(item.get("id")) for item in (envelope.get("snapshots") or [])}),
        error_count=len(envelope.get("errors") or []),
        changes_total=int(refresh.get("total_changes") or len(envelope.get("changes") or [])),
        changes_material=int(refresh.get("material_changes") or 0),
        last_checked=last_checked,
        families=families,
        search_blob=searchable,
    )


# -- dataset ------------------------------------------------------------------


@dataclass(slots=True)
class Dataset:
    """One run's envelopes, indexed for the interface. Immutable once loaded."""

    path: Path
    label: str
    envelopes: list[dict[str, Any]] = dc_field(default_factory=list)
    rows: list[CompanyRow] = dc_field(default_factory=list)
    by_org: dict[str, dict[str, Any]] = dc_field(default_factory=dict)
    row_by_org: dict[str, CompanyRow] = dc_field(default_factory=dict)
    load_error: str | None = None
    loaded_at: str | None = None
    truncated: bool = False

    @property
    def exists(self) -> bool:
        return self.load_error is None and bool(self.envelopes)

    @property
    def count(self) -> int:
        return len(self.rows)

    @property
    def run_ids(self) -> list[str]:
        return sorted({row.run_id for row in self.rows if row.run_id})

    @property
    def cities(self) -> list[str]:
        return sorted({row.city for row in self.rows if row.city})

    @property
    def statuses(self) -> list[str]:
        seen = {row.terminal_status for row in self.rows}
        ordered = [state for state in ("completed", "partial", "not_found", "blocked", "failed") if state in seen]
        return ordered + sorted(seen - set(ordered))

    @property
    def total_changes(self) -> int:
        return sum(row.changes_total for row in self.rows)

    @property
    def total_material_changes(self) -> int:
        return sum(row.changes_material for row in self.rows)

    def envelope(self, org: str) -> dict[str, Any] | None:
        return self.by_org.get(org)

    def row(self, org: str) -> CompanyRow | None:
        return self.row_by_org.get(org)

    def changed_rows(self) -> list[CompanyRow]:
        return [row for row in self.rows if row.changes_total]

    def compared_against(self) -> set[str]:
        found: set[str] = set()
        for envelope in self.envelopes:
            baseline = (envelope.get("refresh") or {}).get("compared_against")
            if baseline:
                found.add(str(baseline))
        return found

    def refresh_notes(self) -> dict[str, int]:
        notes: dict[str, int] = {}
        for envelope in self.envelopes:
            note = str((envelope.get("refresh") or {}).get("note") or "").strip()
            if note:
                notes[note] = notes.get(note, 0) + 1
        return notes


def load_dataset(path: Path, *, label: str | None = None, max_records: int | None = None) -> Dataset:
    """Read a JSONL envelope file. A missing or unreadable file is reported, not raised."""
    resolved = Path(path)
    dataset = Dataset(path=resolved, label=label or resolved.name)
    if not resolved.exists():
        dataset.load_error = f"No envelope file at {resolved}"
        return dataset
    if not resolved.is_file():
        dataset.load_error = f"{resolved} is not a file"
        return dataset

    try:
        for envelope in read_envelopes(resolved):
            org = str(envelope.get("organisation_number") or "")
            if not org:
                continue
            if max_records is not None and len(dataset.envelopes) >= max_records:
                dataset.truncated = True
                break
            dataset.envelopes.append(envelope)
            dataset.by_org[org] = envelope
    except OSError as error:
        dataset.load_error = f"Could not read {resolved}: {error}"
        return dataset

    dataset.rows = [build_row(envelope) for envelope in dataset.envelopes]
    dataset.row_by_org = {row.organisation_number: row for row in dataset.rows}
    if not dataset.envelopes:
        dataset.load_error = f"{resolved} contains no envelopes"
    return dataset


# -- evidence resolution ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """An evidence record joined to the snapshot it was read from."""

    id: str
    snapshot_id: str
    source_url: str
    source_class: str
    retrieved_at: str
    content_sha256: str | None
    locator_kind: str | None
    locator_value: str | None
    locator_excerpt: str | None
    claim_span: str | None
    extraction_method: str | None
    snapshot: dict[str, Any] | None

    @property
    def status(self) -> int | None:
        return (self.snapshot or {}).get("status")

    @property
    def final_url(self) -> str | None:
        return (self.snapshot or {}).get("final_url")

    @property
    def content_type(self) -> str | None:
        return (self.snapshot or {}).get("content_type")

    @property
    def redirect_chain(self) -> list[str]:
        return list((self.snapshot or {}).get("redirect_chain") or [])

    @property
    def from_cache(self) -> bool:
        return bool((self.snapshot or {}).get("from_cache"))


class EvidenceIndex:
    """Evidence and snapshot lookup for one envelope."""

    __slots__ = ("_evidence", "_snapshots")

    def __init__(self, envelope: dict[str, Any]) -> None:
        self._evidence = {
            str(item.get("id")): item for item in (envelope.get("evidence") or []) if item.get("id")
        }
        self._snapshots = {
            str(item.get("id")): item
            for item in (envelope.get("snapshots") or [])
            if item.get("id")
        }

    def snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        return self._snapshots.get(snapshot_id)

    @property
    def snapshots(self) -> list[dict[str, Any]]:
        return list(self._snapshots.values())

    def get(self, evidence_id: str) -> EvidenceRecord | None:
        record = self._evidence.get(evidence_id)
        if record is None:
            return None
        locator = record.get("locator") or {}
        snapshot_id = str(record.get("snapshot_id") or "")
        return EvidenceRecord(
            id=str(record.get("id") or evidence_id),
            snapshot_id=snapshot_id,
            source_url=str(record.get("source_url") or ""),
            source_class=str(record.get("source_class") or ""),
            retrieved_at=str(record.get("retrieved_at") or ""),
            content_sha256=record.get("content_sha256"),
            locator_kind=locator.get("kind"),
            locator_value=locator.get("value"),
            locator_excerpt=locator.get("excerpt"),
            claim_span=record.get("claim_span"),
            extraction_method=record.get("extraction_method"),
            snapshot=self._snapshots.get(snapshot_id),
        )

    def resolve(self, evidence_ids: Iterable[str]) -> list[EvidenceRecord]:
        found: list[EvidenceRecord] = []
        for evidence_id in evidence_ids:
            record = self.get(str(evidence_id))
            if record is not None:
                found.append(record)
        return found

    def unresolved(self, evidence_ids: Iterable[str]) -> list[str]:
        return [str(item) for item in evidence_ids if str(item) not in self._evidence]


# -- explorer query -----------------------------------------------------------


@dataclass(slots=True)
class CompanyQuery:
    """A validated explorer request. Constructed only through :meth:`parse`."""

    q: str = ""
    status: tuple[str, ...] = ()
    city: str = ""
    industry: str = ""
    website: str = "any"
    changed: str = "any"
    family: str = ""
    availability: str = ""
    employees_min: int | None = None
    employees_max: int | None = None
    revenue_min: float | None = None
    sort: str = "name"
    order: str = "asc"
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE

    @classmethod
    def parse(cls, params: Any) -> "CompanyQuery":
        """Validate raw query parameters. Raises :class:`QueryError` with a reason."""

        def one(name: str, default: str = "") -> str:
            value = params.get(name)
            if value is None:
                return default
            return str(value).strip()

        def many(name: str) -> list[str]:
            getter = getattr(params, "getlist", None)
            values = getter(name) if getter else ([params.get(name)] if params.get(name) else [])
            return [str(item).strip() for item in values if str(item).strip()]

        def integer(name: str, *, minimum: int, maximum: int) -> int | None:
            raw = one(name)
            if not raw:
                return None
            try:
                value = int(raw)
            except ValueError as error:
                raise QueryError(f"{name} must be a whole number, got {raw!r}") from error
            if value < minimum or value > maximum:
                raise QueryError(f"{name} must be between {minimum} and {maximum}, got {value}")
            return value

        def number(name: str, *, minimum: float) -> float | None:
            raw = one(name).replace(" ", "").replace(",", "")
            if not raw:
                return None
            try:
                value = float(raw)
            except ValueError as error:
                raise QueryError(f"{name} must be a number, got {raw!r}") from error
            if value < minimum:
                raise QueryError(f"{name} must not be below {minimum}")
            return value

        query = one("q")
        if len(query) > 120:
            raise QueryError("Search text is limited to 120 characters")

        statuses = many("status")
        unknown = [item for item in statuses if item not in TERMINAL_STATES]
        if unknown:
            raise QueryError(f"Unknown terminal status: {', '.join(sorted(unknown))}")

        website = one("website", "any") or "any"
        if website not in {"any", "verified", "none"}:
            raise QueryError("website filter must be one of: any, verified, none")

        changed = one("changed", "any") or "any"
        if changed not in {"any", "changed", "material", "unchanged"}:
            raise QueryError("changed filter must be one of: any, changed, material, unchanged")

        family = one("family")
        if family and family not in FIELD_FAMILIES:
            raise QueryError(f"Unknown field family: {family!r}")

        availability = one("availability")
        if availability and availability not in AVAILABILITY_STATES:
            raise QueryError(f"Unknown availability state: {availability!r}")
        if availability and not family:
            raise QueryError("An availability filter needs a field family to apply to")

        sort = one("sort", "name") or "name"
        if sort not in SORT_KEYS:
            raise QueryError(f"Cannot sort by {sort!r}. Available: {', '.join(sorted(SORT_KEYS))}")

        order = (one("order", "asc") or "asc").lower()
        if order not in {"asc", "desc"}:
            raise QueryError("order must be asc or desc")

        page = integer("page", minimum=1, maximum=1_000_000) or 1
        page_size = integer("page_size", minimum=1, maximum=max(PAGE_SIZES)) or DEFAULT_PAGE_SIZE
        if page_size not in PAGE_SIZES:
            raise QueryError(f"page_size must be one of: {', '.join(str(size) for size in PAGE_SIZES)}")

        employees_min = integer("employees_min", minimum=0, maximum=10_000_000)
        employees_max = integer("employees_max", minimum=0, maximum=10_000_000)
        if employees_min is not None and employees_max is not None and employees_min > employees_max:
            raise QueryError("employees_min cannot be greater than employees_max")

        city = one("city")
        if len(city) > 80:
            raise QueryError("City filter is limited to 80 characters")
        industry = one("industry")
        if len(industry) > 80:
            raise QueryError("Industry filter is limited to 80 characters")

        return cls(
            q=query,
            status=tuple(statuses),
            city=city,
            industry=industry,
            website=website,
            changed=changed,
            family=family,
            availability=availability,
            employees_min=employees_min,
            employees_max=employees_max,
            revenue_min=number("revenue_min", minimum=0.0),
            sort=sort,
            order=order,
            page=page,
            page_size=page_size,
        )

    @property
    def is_filtered(self) -> bool:
        return bool(
            self.q
            or self.status
            or self.city
            or self.industry
            or self.website != "any"
            or self.changed != "any"
            or self.family
            or self.employees_min is not None
            or self.employees_max is not None
            or self.revenue_min is not None
        )

    def to_params(self, **overrides: Any) -> list[tuple[str, str]]:
        """Rebuild the query string, so links preserve the operator's filters."""
        pairs: list[tuple[str, str]] = []
        values: dict[str, Any] = {
            "q": self.q,
            "city": self.city,
            "industry": self.industry,
            "website": self.website if self.website != "any" else "",
            "changed": self.changed if self.changed != "any" else "",
            "family": self.family,
            "availability": self.availability,
            "employees_min": self.employees_min,
            "employees_max": self.employees_max,
            "revenue_min": self.revenue_min,
            "sort": self.sort,
            "order": self.order,
            "page": self.page,
            "page_size": self.page_size,
        }
        statuses = list(self.status)
        for key, value in overrides.items():
            if key == "status":
                statuses = list(value or [])
            else:
                values[key] = value
        for status in statuses:
            pairs.append(("status", str(status)))
        for key, value in values.items():
            if value in (None, "", ()):
                continue
            pairs.append((key, str(value)))
        return pairs


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def filter_rows(rows: Sequence[CompanyRow], query: CompanyQuery) -> list[CompanyRow]:
    needle = query.q.casefold()
    digits = re.sub(r"\D", "", query.q)
    city = query.city.casefold()
    industry = query.industry.casefold()

    selected: list[CompanyRow] = []
    for row in rows:
        if needle:
            hit = needle in row.search_blob
            if not hit and digits and digits in row.organisation_number:
                hit = True
            if not hit:
                continue
        if query.status and row.terminal_status not in query.status:
            continue
        if city and (row.city or "").casefold() != city:
            continue
        if industry:
            haystack = f"{row.industry_code or ''} {row.industry_label or ''}".casefold()
            if industry not in haystack:
                continue
        if query.website == "verified" and not row.website.known:
            continue
        if query.website == "none" and row.website.known:
            continue
        if query.changed == "changed" and not row.changes_total:
            continue
        if query.changed == "material" and not row.changes_material:
            continue
        if query.changed == "unchanged" and row.changes_total:
            continue
        if query.family:
            states = row.families.get(query.family)
            if not states:
                continue
            if query.availability and not states.get(query.availability):
                continue
        if query.employees_min is not None or query.employees_max is not None:
            count = _numeric(row.employees.value) if row.employees.known else None
            if count is None:
                continue
            if query.employees_min is not None and count < query.employees_min:
                continue
            if query.employees_max is not None and count > query.employees_max:
                continue
        if query.revenue_min is not None:
            amount = _numeric(row.revenue.value) if row.revenue.known else None
            if amount is None or amount < query.revenue_min:
                continue
        selected.append(row)
    return selected


def sort_rows(rows: Sequence[CompanyRow], query: CompanyQuery) -> list[CompanyRow]:
    """Sort with unknown values pinned last in both directions.

    A company with no recorded revenue is not the smallest company; it is a company
    we do not have a figure for, and it should not lead a "lowest revenue" list.
    """
    descending = query.order == "desc"

    def key(row: CompanyRow) -> tuple[int, Any, str]:
        if query.sort == "organisation_number":
            return (0, row.organisation_number, row.organisation_number)
        if query.sort == "status":
            return (0, row.terminal_status, row.organisation_number)
        if query.sort == "claims_available":
            return (0, row.claims_available, row.organisation_number)
        if query.sort == "evidence":
            return (0, row.evidence_count, row.organisation_number)
        if query.sort == "changes":
            return (0, row.changes_total, row.organisation_number)
        if query.sort == "last_checked":
            return (0 if row.last_checked else 1, row.last_checked or "", row.organisation_number)
        if query.sort == "employees":
            value = _numeric(row.employees.value) if row.employees.known else None
            return (0 if value is not None else 1, value if value is not None else 0.0, row.organisation_number)
        if query.sort == "revenue":
            value = _numeric(row.revenue.value) if row.revenue.known else None
            return (0 if value is not None else 1, value if value is not None else 0.0, row.organisation_number)
        name = (row.name or "").casefold()
        return (0 if name else 1, name, row.organisation_number)

    keyed = [(key(row), row) for row in rows]
    known = [pair for pair in keyed if pair[0][0] == 0]
    unknown = [pair for pair in keyed if pair[0][0] != 0]
    known.sort(key=lambda pair: pair[0][1:], reverse=descending)
    unknown.sort(key=lambda pair: pair[1].organisation_number)
    return [row for _, row in known] + [row for _, row in unknown]


@dataclass(slots=True)
class Page:
    """One page of results, with everything the pagination control needs."""

    items: list[CompanyRow]
    total: int
    page: int
    page_size: int

    @property
    def pages(self) -> int:
        return max(1, (self.total + self.page_size - 1) // self.page_size)

    @property
    def first_index(self) -> int:
        return 0 if not self.total else (self.page - 1) * self.page_size + 1

    @property
    def last_index(self) -> int:
        return min(self.page * self.page_size, self.total)

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    @property
    def window(self) -> list[int]:
        low = max(1, self.page - 2)
        high = min(self.pages, low + 4)
        low = max(1, high - 4)
        return list(range(low, high + 1))


def paginate(rows: Sequence[CompanyRow], query: CompanyQuery) -> Page:
    total = len(rows)
    pages = max(1, (total + query.page_size - 1) // query.page_size)
    page = min(query.page, pages)
    start = (page - 1) * query.page_size
    return Page(items=list(rows[start : start + query.page_size]), total=total, page=page, page_size=query.page_size)


def search(dataset: Dataset, query: CompanyQuery) -> Page:
    return paginate(sort_rows(filter_rows(dataset.rows, query), query), query)


# -- run reports --------------------------------------------------------------


@dataclass(slots=True)
class RunReport:
    """One measured ``*-report.json`` written by the batch runner."""

    path: Path
    run_id: str
    payload: dict[str, Any]

    @property
    def config(self) -> dict[str, Any]:
        return self.payload.get("config") or {}

    @property
    def results(self) -> dict[str, Any]:
        return self.payload.get("results") or {}

    @property
    def budget(self) -> dict[str, Any]:
        return self.payload.get("budget") or {}

    @property
    def verification(self) -> dict[str, Any]:
        return self.payload.get("verification") or {}

    @property
    def output_path(self) -> str:
        return str(self.config.get("output") or "")

    @property
    def started_at(self) -> str | None:
        return self.results.get("started_at")

    @property
    def completed_at(self) -> str | None:
        return self.results.get("completed_at")

    @property
    def complete(self) -> bool:
        return bool(self.completed_at)

    @property
    def terminal_states(self) -> dict[str, int]:
        return dict(self.results.get("terminal_states") or {})

    @property
    def ok(self) -> bool:
        return bool(self.verification.get("ok"))


def load_reports(directory: Path) -> list[RunReport]:
    """Load every ``*-report.json`` beside the envelope file, newest first.

    These files are the only source of the requests/cost/time figures shown in the
    runs area. Nothing here is recomputed or estimated; an absent figure is absent.
    """
    reports: list[RunReport] = []
    if not directory.exists() or not directory.is_dir():
        return reports
    for path in sorted(directory.glob("*-report.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("schema_version") != "signalpost.run_report/1":
            continue
        run_id = str((payload.get("config") or {}).get("run_id") or path.stem)
        reports.append(RunReport(path=path, run_id=run_id, payload=payload))
    reports.sort(key=lambda report: str(report.started_at or ""), reverse=True)
    return reports


class BaselineResolver:
    """Find and cache the previous run's envelopes, for both sides of a diff.

    A change event records the current claim's evidence but not the previous one's,
    because the previous evidence lives in the previous run's envelope. Rather than
    guess, this resolves the baseline through the run reports: the report that names
    a ``run_id`` also names the file that run wrote. Only *completed* runs are
    considered, which keeps an in-progress output file out of the picture.
    """

    def __init__(self, reports: Sequence[RunReport], *, explicit: Path | None = None, root: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._cache: dict[str, Dataset | None] = {}
        self._explicit = Path(explicit) if explicit else None
        self._root = Path(root) if root else None
        self._by_run: dict[str, Path] = {}
        for report in reports:
            if not report.complete or not report.output_path:
                continue
            candidate = Path(report.output_path)
            if not candidate.is_absolute() and self._root is not None:
                candidate = self._root / candidate.name
            self._by_run.setdefault(report.run_id, candidate)

    @property
    def explicit_path(self) -> Path | None:
        return self._explicit

    def known_runs(self) -> list[str]:
        return sorted(self._by_run)

    def for_run(self, run_id: str | None) -> Dataset | None:
        """The baseline dataset for ``run_id``, or None when it cannot be located."""
        if self._explicit is not None:
            return self._load("__explicit__", self._explicit)
        if not run_id:
            return None
        path = self._by_run.get(str(run_id))
        if path is None:
            return None
        return self._load(str(run_id), path)

    def _load(self, key: str, path: Path) -> Dataset | None:
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        dataset = load_dataset(path, label=key)
        result = dataset if dataset.exists else None
        with self._lock:
            self._cache[key] = result
        return result


# -- flattening for downloads -------------------------------------------------

COMPANY_CSV_COLUMNS: tuple[str, ...] = (
    "organisation_number",
    "legal_name",
    "terminal_status",
    "run_id",
    "completed_at",
    "last_checked",
    "summary_confidence",
    "legal_form",
    "industry_code",
    "industry_label",
    "city",
    "municipality",
    "employees",
    "employees_availability",
    "latest_revenue",
    "revenue_currency",
    "revenue_period",
    "revenue_availability",
    "website",
    "website_availability",
    "claims_total",
    "claims_available",
    "evidence_records",
    "snapshots",
    "errors",
    "changes_total",
    "changes_material",
)


def company_csv_rows(rows: Iterable[CompanyRow]) -> Iterator[list[Any]]:
    yield list(COMPANY_CSV_COLUMNS)
    for row in rows:
        yield [
            row.organisation_number,
            row.name or "",
            row.terminal_status,
            row.run_id,
            row.completed_at or "",
            row.last_checked or "",
            row.confidence,
            row.legal_form or "",
            row.industry_code or "",
            row.industry_label or "",
            row.city or "",
            row.municipality or "",
            row.employees.value if row.employees.known else "",
            row.employees.availability,
            row.revenue.value if row.revenue.known else "",
            row.revenue.currency or "",
            row.revenue.reporting_period or "",
            row.revenue.availability,
            row.website.value if row.website.known else "",
            row.website.availability,
            row.claims_total,
            row.claims_available,
            row.evidence_count,
            row.snapshot_count,
            row.error_count,
            row.changes_total,
            row.changes_material,
        ]


CLAIM_CSV_COLUMNS: tuple[str, ...] = (
    "organisation_number",
    "claim_key",
    "field",
    "family",
    "availability",
    "value",
    "scope",
    "unit",
    "currency",
    "reporting_period",
    "effective_date",
    "confidence",
    "reason",
    "extraction_method",
    "first_observed_at",
    "last_observed_at",
    "evidence_ids",
    "source_urls",
)


def claim_csv_rows(envelopes: Iterable[dict[str, Any]]) -> Iterator[list[Any]]:
    yield list(CLAIM_CSV_COLUMNS)
    for envelope in envelopes:
        index = EvidenceIndex(envelope)
        for claim in envelope.get("claims") or []:
            evidence_ids = [str(item) for item in (claim.get("evidence_ids") or [])]
            urls = [record.source_url for record in index.resolve(evidence_ids)]
            value = claim.get("value")
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False, sort_keys=True)
            yield [
                envelope.get("organisation_number", ""),
                claim.get("claim_key", ""),
                claim.get("field", ""),
                claim.get("family", ""),
                claim.get("availability", ""),
                "" if value is None else value,
                claim.get("scope", ""),
                claim.get("unit", "") or "",
                claim.get("currency", "") or "",
                claim.get("reporting_period", "") or "",
                claim.get("effective_date", "") or "",
                claim.get("confidence", "") if claim.get("confidence") is not None else "",
                claim.get("reason", "") or "",
                claim.get("extraction_method", "") or "",
                claim.get("first_observed_at", "") or "",
                claim.get("last_observed_at", "") or "",
                " ".join(evidence_ids),
                " ".join(urls),
            ]


EVIDENCE_CSV_COLUMNS: tuple[str, ...] = (
    "organisation_number",
    "evidence_id",
    "snapshot_id",
    "source_url",
    "final_url",
    "source_class",
    "http_status",
    "content_type",
    "retrieved_at",
    "content_sha256",
    "locator_kind",
    "locator_value",
    "extraction_method",
    "claim_keys",
)


def evidence_csv_rows(envelopes: Iterable[dict[str, Any]]) -> Iterator[list[Any]]:
    yield list(EVIDENCE_CSV_COLUMNS)
    for envelope in envelopes:
        index = EvidenceIndex(envelope)
        used: dict[str, list[str]] = {}
        for claim in envelope.get("claims") or []:
            for evidence_id in claim.get("evidence_ids") or []:
                used.setdefault(str(evidence_id), []).append(str(claim.get("claim_key") or ""))
        for item in envelope.get("evidence") or []:
            record = index.get(str(item.get("id")))
            if record is None:
                continue
            yield [
                envelope.get("organisation_number", ""),
                record.id,
                record.snapshot_id,
                record.source_url,
                record.final_url or "",
                record.source_class,
                record.status if record.status is not None else "",
                record.content_type or "",
                record.retrieved_at,
                record.content_sha256 or "",
                record.locator_kind or "",
                record.locator_value or "",
                record.extraction_method or "",
                " ".join(used.get(record.id, [])),
            ]
