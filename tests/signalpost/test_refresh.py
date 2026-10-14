"""Change detection: idempotency, false-change immunity and materiality.

Qualification requires an idempotent refresh — re-running against unchanged sources
must produce zero change events — and the refresh score punishes false positives. The
cheapest way to fail both is to compare rendered strings instead of normalised facts,
so most of this file is about things that look different but are not changes.
"""

from __future__ import annotations

import pytest

from signalpost.models import Claim
from signalpost.refresh import (
    carry_observation_times,
    diff,
    normalise_value,
    previous_from_envelope,
    replay_check,
    summarise,
)

ORG = "918432639"


def claim(key: str, field: str, value, *, family: str = "legal_identity", availability=None):
    state = availability or ("available" if value is not None else "not_available")
    return Claim(
        claim_key=key,
        field=field,
        family=family,
        value=value,
        availability=state,
        organisation_number=ORG,
        evidence_ids=[f"ev-{key}"],
    )


def envelope_of(claims: list[Claim]) -> dict:
    return {"organisation_number": ORG, "claims": [item.to_dict() for item in claims]}


BASE = [
    claim("legal_name", "legal_name", "JACTUS MEDICAL SERVICES AS"),
    claim("employees", "employees", 24),
    claim("revenue:2024", "revenue", 4500000.0, family="financials"),
    claim("website", "website", "https://jactus.no/", family="web_presence"),
    claim("description", "business_description", "Vi leverer medisinsk utstyr.",
          family="business_description"),
]


class TestIdempotency:
    def test_a_run_diffed_against_itself_reports_nothing(self) -> None:
        """The qualification gate, stated as a test."""
        report = replay_check(ORG, BASE, envelope=envelope_of(BASE))
        assert report["idempotent"]
        assert report["false_changes"] == 0
        assert report["offending_keys"] == []

    def test_the_summary_reports_idempotency_explicitly(self) -> None:
        assert summarise([])["idempotent"] is True
        events = diff(ORG, previous_from_envelope(envelope_of(BASE[:1])), BASE)
        assert summarise(events)["idempotent"] is False

    def test_claim_order_is_not_a_change(self) -> None:
        """Claims are keyed semantically; a source reordering its page is not news."""
        shuffled = list(reversed(BASE))
        assert diff(ORG, previous_from_envelope(envelope_of(BASE)), shuffled) == []


class TestFalseChangeImmunity:
    @pytest.mark.parametrize(
        "before, after",
        [
            ("Vi leverer  medisinsk\n utstyr.", "Vi leverer medisinsk utstyr."),  # whitespace
            ("https://jactus.no/", "https://jactus.no/?utm_source=nyhetsbrev"),  # tracking tag
            ("https://jactus.no/#kontakt", "https://jactus.no/"),  # fragment
        ],
    )
    def test_rendering_differences_are_not_changes(self, before: str, after: str) -> None:
        assert normalise_value(before) == normalise_value(after)

    def test_integer_and_float_forms_of_one_figure_are_equal(self) -> None:
        assert normalise_value(4500000) == normalise_value(4500000.0)

    def test_list_ordering_is_not_a_change(self) -> None:
        assert normalise_value(["b", "a"]) == normalise_value(["a", "b"])

    def test_retrieval_metadata_is_excluded_from_comparison(self) -> None:
        """Timestamps and snapshot ids differ on every run by construction."""
        first = {"value": "Oslo", "retrieved_at": "2026-09-01T10:00:00Z", "snapshot_id": "a"}
        second = {"value": "Oslo", "retrieved_at": "2026-09-13T04:00:00Z", "snapshot_id": "b"}
        assert normalise_value(first) == normalise_value(second)

    def test_refetching_the_same_page_produces_no_events(self) -> None:
        """End to end: same facts, different retrieval detail, zero changes."""
        refetched = [
            claim(item.claim_key, item.field, item.value, family=item.family) for item in BASE
        ]
        for item in refetched:
            item.evidence_ids = ["ev-new-id"]
            item.confidence = 0.91
        assert diff(ORG, previous_from_envelope(envelope_of(BASE)), refetched) == []


class TestRealChanges:
    def test_a_changed_value_is_reported_as_material(self) -> None:
        after = [claim("employees", "employees", 31)]
        before = previous_from_envelope(envelope_of([claim("employees", "employees", 24)]))
        (event,) = diff(ORG, before, after)
        assert event.change_type == "value_changed"
        assert event.material
        assert (event.previous_value, event.current_value) == (24, 31)

    def test_a_new_claim_is_reported(self) -> None:
        before = previous_from_envelope(envelope_of([]))
        (event,) = diff(ORG, before, [claim("employees", "employees", 24)])
        assert event.change_type == "claim_added"
        assert event.material

    def test_a_disappearing_claim_is_reported(self) -> None:
        before = previous_from_envelope(envelope_of([claim("employees", "employees", 24)]))
        (event,) = diff(ORG, before, [])
        assert event.change_type == "claim_removed"
        assert event.previous_value == 24

    def test_bankruptcy_is_material(self) -> None:
        before = previous_from_envelope(envelope_of([claim("bankrupt", "bankrupt", False)]))
        (event,) = diff(ORG, before, [claim("bankrupt", "bankrupt", True)])
        assert event.material


class TestMateriality:
    def test_prose_rewording_is_a_change_but_not_a_material_one(self) -> None:
        """Marketing copy churns constantly; reporting it as news would be noise."""
        before = previous_from_envelope(
            envelope_of([claim("d", "business_description", "Vi selger utstyr.",
                               family="business_description")])
        )
        (event,) = diff(
            ORG,
            before,
            [claim("d", "business_description", "Vi leverer utstyr til sykehus.",
                   family="business_description")],
        )
        assert event.change_type == "value_changed"
        assert not event.material

    def test_losing_access_to_a_source_is_not_a_fact_about_the_company(self) -> None:
        """`available` → `blocked` is an operational event, reported but not material."""
        before = previous_from_envelope(envelope_of([claim("employees", "employees", 24)]))
        blocked = claim("employees", "employees", None, availability="blocked")
        (event,) = diff(ORG, before, [blocked])
        assert event.change_type == "availability_changed"
        assert not event.material
        assert (event.previous_availability, event.current_availability) == ("available", "blocked")


class TestObservationTimes:
    def test_first_observation_survives_a_re_run(self) -> None:
        """Otherwise every claim looks newly discovered and the change signal is lost."""
        previous = previous_from_envelope(envelope_of(BASE))
        for record in previous.values():
            record["first_observed_at"] = "2026-01-04T09:00:00Z"
        current = [claim(item.claim_key, item.field, item.value, family=item.family)
                   for item in BASE]
        carry_observation_times(previous, current, now="2026-09-13T04:00:00Z")
        assert all(item.first_observed_at == "2026-01-04T09:00:00Z" for item in current)
        assert all(item.last_observed_at == "2026-09-13T04:00:00Z" for item in current)

    def test_a_genuinely_new_claim_is_stamped_now(self) -> None:
        fresh = [claim("new", "employees", 5)]
        carry_observation_times({}, fresh, now="2026-09-13T04:00:00Z")
        assert fresh[0].first_observed_at == "2026-09-13T04:00:00Z"
