"""Turn one envelope into the sections the company profile renders.

The profile is organised around a single rule from the brief: **every material fact
opens its evidence**. So the unit of the page is not a string but a
:class:`FactRow` — a claim, its availability, and the evidence records it resolves
to, each joined to the snapshot it came from. A row that cannot resolve its evidence
says so; it does not quietly render as a bare value.

Unavailable fields are kept, not dropped. "The registry does not carry a phone
number" and "we never looked" are different statements, and the profile makes that
difference visible instead of showing an identical blank.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any, Iterable, Sequence

from .data import (
    NOT_RECORDED,
    SECTION_STATUS_FIELDS,
    CompanyRow,
    EvidenceIndex,
    EvidenceRecord,
    Fact,
    build_row,
    fact_from_claim,
    field_label,
)
from .formatting import format_fact, safe_url

IDENTITY_ORDER: tuple[str, ...] = (
    "legal_name",
    "organisation_number",
    "legal_form_label",
    "legal_form_code",
    "sector_code",
    "industry_code",
    "industry_label",
    "registered_at",
    "incorporated_at",
    "vat_registered",
    "employees",
    "bankrupt",
    "under_liquidation",
    "under_forced_liquidation",
    "former_name",
    "registry_website",
)

ACCOUNT_LINE_ORDER: tuple[str, ...] = (
    "revenue",
    "operating_costs",
    "operating_profit",
    "profit_before_tax",
    "net_profit",
    "total_assets",
    "total_equity",
    "total_equity_and_liabilities",
    "accounts_audited",
)

CONTACT_ORDER: tuple[str, ...] = ("email", "phone", "site_address")

# Every field some section of the profile places. Claims outside this set are listed
# under "Other published claims" instead of being dropped.
PLACED_FIELDS: frozenset[str] = (
    frozenset(IDENTITY_ORDER)
    | frozenset(ACCOUNT_LINE_ORDER)
    | frozenset(CONTACT_ORDER)
    | SECTION_STATUS_FIELDS
    | frozenset(
        {
            "business_description",
            "role",
            "address",
            "subunit",
            "subunit_count",
            "website",
            "social_profile",
            "is_hiring",
            "job_posting",
            "activity_item",
        }
    )
)


@dataclass(slots=True)
class FactRow:
    """One displayed fact together with the evidence that supports it."""

    fact: Fact
    display: str
    link: str | None
    evidence: list[EvidenceRecord]
    unresolved: list[str]
    structured: Any = None

    @property
    def known(self) -> bool:
        return self.fact.known

    @property
    def has_evidence(self) -> bool:
        return bool(self.evidence)

    @property
    def evidence_missing(self) -> bool:
        """A published fact whose evidence ids do not resolve inside this envelope."""
        return self.known and bool(self.unresolved) and not self.evidence

    @property
    def anchor(self) -> str:
        key = self.fact.claim_key or self.fact.field
        return "claim-" + "".join(char if char.isalnum() else "-" for char in key)


@dataclass(slots=True)
class Section:
    """A titled block of the profile. Carries its own empty/blocked explanation."""

    key: str
    title: str
    rows: list[FactRow] = dc_field(default_factory=list)
    status: FactRow | None = None
    note: str | None = None

    @property
    def available_rows(self) -> list[FactRow]:
        return [row for row in self.rows if row.known]

    @property
    def is_empty(self) -> bool:
        return not self.available_rows


@dataclass(slots=True)
class AccountPeriod:
    """One reporting period's filed figures."""

    period: str
    currency: str | None
    rows: list[FactRow]

    @property
    def audited(self) -> FactRow | None:
        return next((row for row in self.rows if row.fact.field == "accounts_audited"), None)

    @property
    def figures(self) -> list[FactRow]:
        return [row for row in self.rows if row.fact.field != "accounts_audited"]


@dataclass(slots=True)
class Sentence:
    """A summary sentence with the claims it was generated from."""

    text: str
    topic: str
    rows: list[FactRow]


@dataclass(slots=True)
class Profile:
    """Everything the company page shows, already resolved and ordered."""

    organisation_number: str
    row: CompanyRow
    envelope: dict[str, Any]
    index: EvidenceIndex
    rows_by_key: dict[str, FactRow]
    identity: Section
    description: Section
    people: Section
    locations: Section
    subunits: Section
    web: Section
    contact: Section
    jobs: Section
    activity: Section
    other: Section
    accounts: list[AccountPeriod]
    accounts_status: FactRow | None
    sentences: list[Sentence]
    highlights: list[dict[str, Any]]
    gaps: list[str]

    @property
    def synthesis(self) -> dict[str, Any]:
        return self.envelope.get("synthesis") or {}

    @property
    def operations(self) -> dict[str, Any]:
        return self.envelope.get("operations") or {}

    @property
    def refresh(self) -> dict[str, Any]:
        return self.envelope.get("refresh") or {}

    @property
    def errors(self) -> list[dict[str, Any]]:
        return list(self.envelope.get("errors") or [])

    @property
    def changes(self) -> list[dict[str, Any]]:
        return list(self.envelope.get("changes") or [])

    @property
    def identity_block(self) -> dict[str, Any]:
        return self.envelope.get("identity") or {}

    @property
    def website_verdict(self) -> dict[str, Any]:
        return (self.identity_block.get("website_verdict") or {})

    @property
    def snapshots(self) -> list[dict[str, Any]]:
        return sorted(self.index.snapshots, key=lambda item: str(item.get("retrieved_at") or ""))

    @property
    def sections(self) -> list[Section]:
        return [
            self.identity,
            self.description,
            self.people,
            self.locations,
            self.subunits,
            self.web,
            self.contact,
            self.jobs,
            self.activity,
        ]


def _row(fact: Fact, index: EvidenceIndex) -> FactRow:
    display = format_fact(fact) if fact.known else ""
    link = safe_url(fact.value) if fact.known else None
    if link is None and isinstance(fact.value, dict):
        link = safe_url(fact.value.get("url"))
    structured = fact.value if isinstance(fact.value, (dict, list)) else None
    return FactRow(
        fact=fact,
        display=display,
        link=link,
        evidence=index.resolve(fact.evidence_ids),
        unresolved=index.unresolved(fact.evidence_ids),
        structured=structured,
    )


def _claims(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    return list(envelope.get("claims") or [])


def _of_field(claims: Sequence[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [claim for claim in claims if claim.get("field") == name]


def _status_row(claims: Sequence[dict[str, Any]], name: str, index: EvidenceIndex) -> FactRow | None:
    """The section-status claim, shown only when it is not itself an available value."""
    found = _of_field(claims, name)
    if not found:
        return None
    fact = fact_from_claim(found[0])
    if fact.known:
        return None
    return _row(fact, index)


def build_profile(envelope: dict[str, Any]) -> Profile:
    claims = _claims(envelope)
    index = EvidenceIndex(envelope)
    rows_by_key: dict[str, FactRow] = {}

    def make(fact: Fact) -> FactRow:
        row = _row(fact, index)
        if fact.claim_key:
            rows_by_key.setdefault(fact.claim_key, row)
        return row

    by_field: dict[str, list[dict[str, Any]]] = {}
    for claim in claims:
        by_field.setdefault(str(claim.get("field") or ""), []).append(claim)

    # Legal identity, in a fixed reading order so two companies are comparable.
    identity_rows: list[FactRow] = []
    for name in IDENTITY_ORDER:
        found = by_field.get(name)
        if found:
            identity_rows.extend(make(fact_from_claim(claim)) for claim in found)
        else:
            # The run emitted nothing for this field. Shown as "not recorded", which
            # is a different statement from the registry saying the field is empty.
            identity_rows.append(_row(Fact(field=name, label=field_label(name)), index))
    identity = Section(key="identity", title="Legal identity", rows=identity_rows)

    description = Section(
        key="description",
        title="Business description",
        rows=[make(fact_from_claim(claim)) for claim in by_field.get("business_description", [])],
    )

    people = Section(
        key="people",
        title="People and roles",
        rows=[make(fact_from_claim(claim)) for claim in by_field.get("role", [])],
        status=_status_row(claims, "leadership", index),
    )

    locations = Section(
        key="locations",
        title="Locations",
        rows=[make(fact_from_claim(claim)) for claim in by_field.get("address", [])],
    )

    subunit_rows = [make(fact_from_claim(claim)) for claim in by_field.get("subunit", [])]
    subunit_count = [make(fact_from_claim(claim)) for claim in by_field.get("subunit_count", [])]
    subunits = Section(key="subunits", title="Registered subunits", rows=subunit_count + subunit_rows)

    web = Section(
        key="web",
        title="Website and owned profiles",
        rows=[make(fact_from_claim(claim)) for claim in by_field.get("website", [])]
        + [make(fact_from_claim(claim)) for claim in by_field.get("social_profile", [])],
    )

    contact_rows: list[FactRow] = []
    for name in CONTACT_ORDER:
        contact_rows.extend(make(fact_from_claim(claim)) for claim in by_field.get(name, []))
    contact = Section(
        key="contact",
        title="Contact details",
        rows=contact_rows,
        status=_status_row(claims, "site_contact", index),
    )

    jobs = Section(
        key="jobs",
        title="Hiring and job postings",
        rows=[make(fact_from_claim(claim)) for claim in by_field.get("is_hiring", [])]
        + [make(fact_from_claim(claim)) for claim in by_field.get("job_posting", [])],
        status=_status_row(claims, "job_postings", index),
    )

    activity_rows = [make(fact_from_claim(claim)) for claim in by_field.get("activity_item", [])]
    activity_rows.sort(key=lambda row: str(row.fact.effective_date or ""), reverse=True)
    activity = Section(
        key="activity",
        title="Dated public activity",
        rows=activity_rows,
        status=_status_row(claims, "activity", index),
    )

    # Accounts are grouped by reporting period, newest first, so a figure is never
    # shown without the period and currency it belongs to.
    periods: dict[str, list[FactRow]] = {}
    currencies: dict[str, str | None] = {}
    for name in ACCOUNT_LINE_ORDER:
        for claim in by_field.get(name, []):
            fact = fact_from_claim(claim)
            period = fact.reporting_period or "Period not stated"
            periods.setdefault(period, []).append(make(fact))
            if fact.currency:
                currencies.setdefault(period, fact.currency)

    def period_key(period: str) -> str:
        return period.split("/")[-1] if "/" in period else period

    accounts = [
        AccountPeriod(
            period=period,
            currency=currencies.get(period),
            rows=sorted(
                rows,
                key=lambda row: ACCOUNT_LINE_ORDER.index(row.fact.field)
                if row.fact.field in ACCOUNT_LINE_ORDER
                else len(ACCOUNT_LINE_ORDER),
            ),
        )
        for period, rows in sorted(periods.items(), key=lambda item: period_key(item[0]), reverse=True)
    ]
    accounts_status = _status_row(claims, "annual_accounts", index)

    # Anything the sections above do not place is still a published claim with
    # evidence. It is shown here rather than dropped, so a field the engine adds later
    # never disappears from the profile silently.
    other = Section(
        key="other",
        title="Other published claims",
        rows=[
            make(fact_from_claim(claim))
            for claim in claims
            if str(claim.get("field") or "") not in PLACED_FIELDS
        ],
    )

    synthesis = envelope.get("synthesis") or {}
    sentences = [
        Sentence(
            text=str(item.get("text") or ""),
            topic=str(item.get("topic") or ""),
            rows=[rows_by_key[key] for key in (item.get("claim_keys") or []) if key in rows_by_key],
        )
        for item in (synthesis.get("sentences") or [])
        if item.get("text")
    ]

    return Profile(
        organisation_number=str(envelope.get("organisation_number") or ""),
        row=build_row(envelope),
        envelope=envelope,
        index=index,
        rows_by_key=rows_by_key,
        identity=identity,
        description=description,
        people=people,
        locations=locations,
        subunits=subunits,
        web=web,
        contact=contact,
        jobs=jobs,
        activity=activity,
        other=other,
        accounts=accounts,
        accounts_status=accounts_status,
        sentences=sentences,
        highlights=list(synthesis.get("highlights") or []),
        gaps=[str(item) for item in (synthesis.get("gaps") or [])],
    )


# -- comparison ---------------------------------------------------------------

COMPARE_FIELDS: tuple[str, ...] = (
    "legal_name",
    "legal_form_label",
    "industry_label",
    "industry_code",
    "registered_at",
    "employees",
    "revenue",
    "operating_profit",
    "profit_before_tax",
    "net_profit",
    "total_assets",
    "total_equity",
    "website",
    "business_description",
    "subunit_count",
)


@dataclass(slots=True)
class CompareCell:
    row: FactRow | None
    profile: Profile


@dataclass(slots=True)
class CompareLine:
    field: str
    label: str
    cells: list[CompareCell]

    @property
    def any_known(self) -> bool:
        return any(cell.row is not None and cell.row.known for cell in self.cells)


def _latest_for_field(profile: Profile, name: str) -> FactRow | None:
    """The newest available row for a field, falling back to its stated unavailability."""
    candidates = [
        row
        for row in profile.rows_by_key.values()
        if row.fact.field == name
    ]
    if not candidates:
        return None
    available = [row for row in candidates if row.known]
    if not available:
        return candidates[0]
    return max(available, key=lambda row: str(row.fact.reporting_period or row.fact.effective_date or ""))


def build_comparison(profiles: Sequence[Profile]) -> list[CompareLine]:
    lines: list[CompareLine] = []
    for name in COMPARE_FIELDS:
        cells = [CompareCell(row=_latest_for_field(profile, name), profile=profile) for profile in profiles]
        lines.append(CompareLine(field=name, label=field_label(name), cells=cells))
    return lines


def compare_counts(profiles: Iterable[Profile]) -> list[dict[str, Any]]:
    """Row-level run measurements, so a comparison shows how much was actually found."""
    summary: list[dict[str, Any]] = []
    for profile in profiles:
        row = profile.row
        summary.append(
            {
                "organisation_number": row.organisation_number,
                "claims_available": row.claims_available,
                "claims_total": row.claims_total,
                "evidence": row.evidence_count,
                "snapshots": row.snapshot_count,
                "status": row.terminal_status,
                "last_checked": row.last_checked,
            }
        )
    return summary
