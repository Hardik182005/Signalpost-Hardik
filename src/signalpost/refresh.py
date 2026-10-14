"""Change detection between two runs.

The refresh score rewards detecting real changes and punishes false ones, and the
qualification gate requires an idempotent refresh: re-running against unchanged
sources must produce zero change events. Everything here follows from that.

What makes a change real:

* Comparison is by **stable claim key**, never by position in a list. Reordered
  content is not change.
* Values are compared **after normalisation** — whitespace collapsed, URLs stripped of
  tracking parameters, numbers compared as numbers. A page that re-renders "1 234"
  as "1234" has not changed.
* Retrieval metadata (timestamps, snapshot ids, confidence, evidence ids) is excluded
  from comparison entirely. Those change on every run by definition.

Availability transitions are reported separately from value changes, because a field
going `available` → `blocked` is an operational event, not a fact about the company.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .models import Claim, utc_now
from .net import canonical_url

# Fields where any change matters to a user tracking the company.
MATERIAL_FIELDS = frozenset(
    {
        "legal_name",
        "legal_form_code",
        "bankrupt",
        "under_liquidation",
        "under_forced_liquidation",
        "employees",
        "website",
        "revenue",
        "operating_profit",
        "profit_before_tax",
        "net_profit",
        "total_assets",
        "total_equity",
        "role",
        "address",
        "industry_code",
        "subunit_count",
        "email",
        "phone",
        "social_profile",
        "job_posting",
    }
)

# Fields that churn harmlessly and should not be reported as material.
COSMETIC_FIELDS = frozenset({"business_description", "site_person", "activity_item"})

CHANGE_TYPES = (
    "value_changed",
    "claim_added",
    "claim_removed",
    "availability_changed",
    "unchanged",
)


def normalise_value(value: Any) -> Any:
    """Canonical comparison form: equal facts compare equal regardless of rendering."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        # 1000 and 1000.0 are the same figure.
        return round(float(value), 6)
    if isinstance(value, str):
        text = re.sub(r"\s+", " ", value).strip()
        if text.lower().startswith(("http://", "https://")):
            return canonical_url(text)
        return text
    if isinstance(value, dict):
        return {
            key: normalise_value(item)
            for key, item in sorted(value.items())
            # Retrieval detail, not fact: excluded so re-fetching is not a change.
            if key not in {"retrieved_at", "snapshot_id", "evidence_ids", "confidence"}
        }
    if isinstance(value, (list, tuple)):
        normalised = [normalise_value(item) for item in value]
        try:
            return sorted(normalised, key=lambda item: str(item))
        except TypeError:
            return normalised
    return value


@dataclass(slots=True)
class ChangeEvent:
    """One typed, explainable difference between two runs."""

    claim_key: str
    field: str
    family: str
    change_type: str
    organisation_number: str
    previous_value: Any = None
    current_value: Any = None
    previous_availability: str | None = None
    current_availability: str | None = None
    material: bool = False
    detected_at: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_key": self.claim_key,
            "organisation_number": self.organisation_number,
            "field": self.field,
            "family": self.family,
            "change_type": self.change_type,
            "previous_value": self.previous_value,
            "current_value": self.current_value,
            "previous_availability": self.previous_availability,
            "current_availability": self.current_availability,
            "material": self.material,
            "detected_at": self.detected_at,
            "evidence_ids": list(self.evidence_ids),
            "explanation": self.explanation,
        }


def _is_material(field_name: str, change_type: str) -> bool:
    if field_name in COSMETIC_FIELDS:
        return False
    if change_type == "availability_changed":
        # Losing access to a source says nothing about the company itself.
        return False
    return field_name in MATERIAL_FIELDS


def claims_by_key(claims: Iterable[Claim]) -> dict[str, Claim]:
    return {claim.claim_key: claim for claim in claims}


def previous_from_envelope(envelope: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Read the comparable state out of a stored envelope.

    Only the fields that define a claim's *meaning* are kept, which is what makes a
    re-run over unchanged sources produce no differences.
    """
    previous: dict[str, dict[str, Any]] = {}
    for record in envelope.get("claims") or []:
        key = record.get("claim_key")
        if not key:
            continue
        previous[key] = {
            "value": record.get("value"),
            "availability": record.get("availability"),
            "field": record.get("field"),
            "family": record.get("family"),
            "first_observed_at": record.get("first_observed_at"),
        }
    return previous


def diff(
    organisation_number: str,
    previous: dict[str, dict[str, Any]],
    current: Iterable[Claim],
    *,
    detected_at: str | None = None,
) -> list[ChangeEvent]:
    """Compare a previous envelope's claims against this run's claims."""
    stamp = detected_at or utc_now()
    events: list[ChangeEvent] = []
    current_map = claims_by_key(current)

    for key, claim in current_map.items():
        before = previous.get(key)
        if before is None:
            events.append(
                ChangeEvent(
                    claim_key=key,
                    field=claim.field,
                    family=claim.family,
                    change_type="claim_added",
                    organisation_number=organisation_number,
                    current_value=claim.value,
                    current_availability=claim.availability,
                    material=_is_material(claim.field, "claim_added")
                    and claim.availability == "available",
                    detected_at=stamp,
                    evidence_ids=list(claim.evidence_ids),
                    explanation=f"{claim.field} observed for the first time",
                )
            )
            continue

        before_availability = before.get("availability")
        if before_availability != claim.availability:
            events.append(
                ChangeEvent(
                    claim_key=key,
                    field=claim.field,
                    family=claim.family,
                    change_type="availability_changed",
                    organisation_number=organisation_number,
                    previous_value=before.get("value"),
                    current_value=claim.value,
                    previous_availability=before_availability,
                    current_availability=claim.availability,
                    material=False,
                    detected_at=stamp,
                    evidence_ids=list(claim.evidence_ids),
                    explanation=(
                        f"{claim.field} moved from {before_availability} to {claim.availability}"
                        f"{f': {claim.reason}' if claim.reason else ''}"
                    ),
                )
            )
            continue

        if normalise_value(before.get("value")) != normalise_value(claim.value):
            events.append(
                ChangeEvent(
                    claim_key=key,
                    field=claim.field,
                    family=claim.family,
                    change_type="value_changed",
                    organisation_number=organisation_number,
                    previous_value=before.get("value"),
                    current_value=claim.value,
                    previous_availability=before_availability,
                    current_availability=claim.availability,
                    material=_is_material(claim.field, "value_changed"),
                    detected_at=stamp,
                    evidence_ids=list(claim.evidence_ids),
                    explanation=f"{claim.field} changed",
                )
            )

    for key, before in previous.items():
        if key in current_map:
            continue
        events.append(
            ChangeEvent(
                claim_key=key,
                field=str(before.get("field") or "unknown"),
                family=str(before.get("family") or "legal_identity"),
                change_type="claim_removed",
                organisation_number=organisation_number,
                previous_value=before.get("value"),
                previous_availability=before.get("availability"),
                material=_is_material(str(before.get("field") or ""), "claim_removed")
                and before.get("availability") == "available",
                detected_at=stamp,
                explanation=f"{before.get('field')} is no longer reported by any source",
            )
        )

    return events


def carry_observation_times(
    previous: dict[str, dict[str, Any]], current: Iterable[Claim], *, now: str | None = None
) -> None:
    """Preserve `first_observed_at` across runs and stamp `last_observed_at`.

    Without this, every claim would look newly discovered on every run, which would
    destroy the "when did this change" signal the refresh score measures.
    """
    stamp = now or utc_now()
    for claim in current:
        before = previous.get(claim.claim_key)
        claim.first_observed_at = (before or {}).get("first_observed_at") or stamp
        claim.last_observed_at = stamp


def summarise(events: Iterable[ChangeEvent]) -> dict[str, Any]:
    """Run-level refresh summary, including the idempotency check."""
    listed = list(events)
    by_type: dict[str, int] = {}
    by_family: dict[str, int] = {}
    for event in listed:
        by_type[event.change_type] = by_type.get(event.change_type, 0) + 1
        by_family[event.family] = by_family.get(event.family, 0) + 1
    material = [event for event in listed if event.material]
    return {
        "total_changes": len(listed),
        "material_changes": len(material),
        "by_type": by_type,
        "by_family": by_family,
        # A refresh over unchanged sources must report nothing.
        "idempotent": len(listed) == 0,
        "material_fields": sorted({event.field for event in material}),
    }


def replay_check(
    organisation_number: str, claims: list[Claim], *, envelope: dict[str, Any]
) -> dict[str, Any]:
    """Verify that diffing a run against its own output yields no changes.

    This is the self-test for the idempotency gate: if this ever reports changes, the
    claim keys or the normalisation are unstable and refresh precision is compromised.
    """
    previous = previous_from_envelope(envelope)
    events = diff(organisation_number, previous, claims)
    return {
        "organisation_number": organisation_number,
        "replayed_claims": len(claims),
        "false_changes": len(events),
        "idempotent": not events,
        "offending_keys": [event.claim_key for event in events][:20],
    }
