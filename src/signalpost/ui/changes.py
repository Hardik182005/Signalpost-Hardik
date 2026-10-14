"""Version history: what changed between two runs, with evidence for both sides.

A change event stores the current claim's evidence ids but not the previous one's,
because the previous evidence belongs to the previous run's envelope. Showing a
before/after with evidence on only one side would be a diff you cannot check, so
this module resolves the "before" side out of the baseline envelope when that file
can be located, and states plainly that it could not when it cannot.

The empty state is treated as a real state, not an absence of one. "This run found
no changes" and "this run had nothing to compare against" are different facts, and
the refresh block on each envelope says which one applies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .data import (
    AVAILABILITY_LABELS,
    Dataset,
    EvidenceIndex,
    EvidenceRecord,
    field_label,
)
from .formatting import format_scalar, safe_url, summarise_mapping

CHANGE_TYPE_LABELS: dict[str, str] = {
    "value_changed": "Value changed",
    "claim_added": "First observed",
    "claim_removed": "No longer observed",
    "availability_changed": "Availability changed",
    "unchanged": "Unchanged",
}


def describe_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return summarise_mapping(value)
    if isinstance(value, list):
        return "; ".join(describe_value(item) for item in value)
    return format_scalar(value)


@dataclass(slots=True)
class ChangeSide:
    """One side of a diff: the value, its availability, and its evidence."""

    value: Any
    display: str
    link: str | None
    availability: str | None
    evidence: list[EvidenceRecord]
    evidence_note: str | None = None

    @property
    def availability_label(self) -> str | None:
        if not self.availability:
            return None
        return AVAILABILITY_LABELS.get(self.availability, self.availability)


@dataclass(slots=True)
class ChangeView:
    """One change event, ready to render."""

    organisation_number: str
    claim_key: str
    field: str
    label: str
    family: str
    change_type: str
    change_type_label: str
    material: bool
    detected_at: str
    explanation: str
    before: ChangeSide
    after: ChangeSide


def _side(
    value: Any,
    availability: str | None,
    evidence: list[EvidenceRecord],
    note: str | None = None,
) -> ChangeSide:
    return ChangeSide(
        value=value,
        display=describe_value(value),
        link=safe_url(value) if isinstance(value, str) else None,
        availability=availability,
        evidence=evidence,
        evidence_note=note,
    )


def build_change_views(
    envelope: dict[str, Any],
    *,
    baseline: Dataset | None,
) -> list[ChangeView]:
    org = str(envelope.get("organisation_number") or "")
    index = EvidenceIndex(envelope)

    baseline_envelope = baseline.envelope(org) if baseline is not None else None
    baseline_index = EvidenceIndex(baseline_envelope) if baseline_envelope else None
    baseline_claims: dict[str, dict[str, Any]] = {}
    if baseline_envelope:
        baseline_claims = {
            str(claim.get("claim_key")): claim
            for claim in (baseline_envelope.get("claims") or [])
            if claim.get("claim_key")
        }

    if baseline is None:
        before_note = (
            "The baseline run's envelope file was not located, so the previous "
            "evidence record cannot be opened here. Start the server with "
            "--baseline <path to the previous run's .jsonl> to resolve it."
        )
    elif baseline_envelope is None:
        before_note = f"The baseline run ({baseline.label}) contains no envelope for {org}."
    else:
        before_note = None

    views: list[ChangeView] = []
    for change in envelope.get("changes") or []:
        claim_key = str(change.get("claim_key") or "")
        name = str(change.get("field") or "")

        previous_evidence: list[EvidenceRecord] = []
        note = before_note
        if baseline_index is not None:
            previous_claim = baseline_claims.get(claim_key)
            if previous_claim is None:
                note = f"The baseline run ({baseline.label if baseline else '?'}) had no claim {claim_key}."
            else:
                previous_evidence = baseline_index.resolve(
                    str(item) for item in (previous_claim.get("evidence_ids") or [])
                )
                note = (
                    None
                    if previous_evidence
                    else "The baseline claim carried no evidence record."
                )

        views.append(
            ChangeView(
                organisation_number=org,
                claim_key=claim_key,
                field=name,
                label=field_label(name),
                family=str(change.get("family") or ""),
                change_type=str(change.get("change_type") or ""),
                change_type_label=CHANGE_TYPE_LABELS.get(
                    str(change.get("change_type") or ""), str(change.get("change_type") or "")
                ),
                material=bool(change.get("material")),
                detected_at=str(change.get("detected_at") or ""),
                explanation=str(change.get("explanation") or ""),
                before=_side(
                    change.get("previous_value"),
                    change.get("previous_availability"),
                    previous_evidence,
                    note,
                ),
                after=_side(
                    change.get("current_value"),
                    change.get("current_availability"),
                    index.resolve(str(item) for item in (change.get("evidence_ids") or [])),
                ),
            )
        )
    return views


@dataclass(slots=True)
class ChangesOverview:
    """Why the changes area looks the way it does for this dataset."""

    companies_with_changes: int
    total_changes: int
    material_changes: int
    compared_against: list[str]
    compared_count: int
    first_observation_count: int
    notes: dict[str, int]
    baseline_label: str | None

    @property
    def has_changes(self) -> bool:
        return self.total_changes > 0

    @property
    def had_baseline(self) -> bool:
        return self.compared_count > 0


def build_overview(dataset: Dataset, *, baseline: Dataset | None) -> ChangesOverview:
    compared = 0
    first_observation = 0
    for envelope in dataset.envelopes:
        refresh = envelope.get("refresh") or {}
        if refresh.get("compared_against"):
            compared += 1
        else:
            first_observation += 1
    return ChangesOverview(
        companies_with_changes=len(dataset.changed_rows()),
        total_changes=dataset.total_changes,
        material_changes=dataset.total_material_changes,
        compared_against=sorted(dataset.compared_against()),
        compared_count=compared,
        first_observation_count=first_observation,
        notes=dataset.refresh_notes(),
        baseline_label=baseline.label if baseline is not None else None,
    )
