"""The envelope contract: six availability states, five terminal states, resolvable
evidence and stable claim keys.

Every published material claim has to resolve to a snapshot a reviewer can re-fetch,
and "we did not find it" has to be distinguishable from "we were refused" and from
"this does not apply". These are the invariants `validate_result` exists to enforce,
pinned here so a future change cannot quietly publish an unsupported fact.
"""

from __future__ import annotations

import pytest

from signalpost.models import (
    AVAILABILITY_STATES,
    EXTERNAL_FAMILIES,
    FIELD_FAMILIES,
    TERMINAL_STATES,
    Claim,
    CompanyResult,
    Evidence,
    Locator,
    Snapshot,
    build_envelope,
    claim_key,
    coverage_summary,
    parse_utc,
    stable_id,
    validate_result,
)

ORG = "918432639"


def snapshot(identifier: str = "snap-1") -> Snapshot:
    return Snapshot(
        id=identifier,
        requested_url="https://data.brreg.no/enhetsregisteret/api/enheter/918432639",
        final_url="https://data.brreg.no/enhetsregisteret/api/enheter/918432639",
        status=200,
        content_type="application/json",
        bytes_received=1024,
        content_sha256="a" * 64,
        retrieved_at="2026-09-13T04:00:00Z",
        source_class="official_registry",
    )


def evidence(identifier: str = "ev-1", *, snapshot_id: str = "snap-1") -> Evidence:
    return Evidence(
        id=identifier,
        snapshot_id=snapshot_id,
        source_url="https://data.brreg.no/enhetsregisteret/api/enheter/918432639",
        source_class="official_registry",
        retrieved_at="2026-09-13T04:00:00Z",
        content_sha256="a" * 64,
        locator=Locator(kind="json_pointer", value="/navn", excerpt="JACTUS MEDICAL SERVICES AS"),
    )


def available_claim(**overrides) -> Claim:
    base = dict(
        claim_key=claim_key(ORG, "legal_name"),
        field="legal_name",
        family="legal_identity",
        value="JACTUS MEDICAL SERVICES AS",
        availability="available",
        organisation_number=ORG,
        evidence_ids=["ev-1"],
    )
    base.update(overrides)
    return Claim(**base)


def result_with(claims: list[Claim], *, status: str = "completed") -> CompanyResult:
    return CompanyResult(
        organisation_number=ORG,
        terminal_status=status,
        claims=claims,
        evidence=[evidence()],
        snapshots=[snapshot()],
    )


class TestStateVocabularies:
    def test_exactly_the_six_organiser_availability_states_exist(self) -> None:
        assert AVAILABILITY_STATES == {
            "available",
            "not_available",
            "blocked",
            "not_applicable",
            "ambiguous",
            "failed",
        }

    def test_exactly_the_five_terminal_states_exist(self) -> None:
        assert TERMINAL_STATES == {"completed", "partial", "not_found", "blocked", "failed"}

    def test_the_five_externally_scored_families_are_marked(self) -> None:
        """Coverage against the pooled reference collection is measured on these."""
        assert set(EXTERNAL_FAMILIES) == {
            "web_presence",
            "business_description",
            "contact",
            "jobs",
            "activity",
        }
        assert len(FIELD_FAMILIES) == 9

    @pytest.mark.parametrize("state", sorted(AVAILABILITY_STATES))
    def test_every_state_can_be_expressed_on_a_claim(self, state: str) -> None:
        value = "x" if state == "available" else None
        assert Claim(
            claim_key="k",
            field="website",
            family="web_presence",
            value=value,
            availability=state,
            organisation_number=ORG,
            evidence_ids=["ev-1"] if state == "available" else [],
            reason=None if state == "available" else "reason recorded",
        ).availability == state

    def test_an_unknown_state_is_rejected_at_construction(self) -> None:
        """A typo like "unavailable" must fail loudly, not become a seventh state."""
        with pytest.raises(ValueError, match="Invalid availability"):
            Claim(
                claim_key="k",
                field="website",
                family="web_presence",
                value=None,
                availability="unavailable",
                organisation_number=ORG,
            )

    def test_an_unknown_family_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unknown field family"):
            Claim(
                claim_key="k",
                field="website",
                family="web_prescence",
                value=None,
                availability="not_available",
                organisation_number=ORG,
            )


class TestValueAndStateAgreement:
    def test_available_without_a_value_is_refused(self) -> None:
        """"available" asserts a fact; there has to be one."""
        with pytest.raises(ValueError, match="carries no value"):
            Claim(
                claim_key="k",
                field="employees",
                family="legal_identity",
                value=None,
                availability="available",
                organisation_number=ORG,
            )

    @pytest.mark.parametrize(
        "state", ["not_available", "blocked", "not_applicable", "ambiguous", "failed"]
    )
    def test_a_non_available_claim_must_not_smuggle_a_value(self, state: str) -> None:
        """Otherwise a guess rides along under a state that disclaims it."""
        with pytest.raises(ValueError, match="must not carry a value"):
            Claim(
                claim_key="k",
                field="employees",
                family="legal_identity",
                value=24,
                availability=state,
                organisation_number=ORG,
            )

    def test_zero_is_a_value_and_stays_available(self) -> None:
        """A company with zero employees is a fact, not a missing field."""
        claim = Claim(
            claim_key="k",
            field="employees",
            family="legal_identity",
            value=0,
            availability="available",
            organisation_number=ORG,
            evidence_ids=["ev-1"],
        )
        assert claim.value == 0
        assert claim.is_material


class TestEvidenceResolution:
    def test_a_clean_result_validates(self) -> None:
        assert validate_result(result_with([available_claim()])) == []

    def test_a_published_fact_without_evidence_is_a_defect(self) -> None:
        """The accuracy score requires resolvable provenance for every material claim."""
        result = result_with([available_claim(evidence_ids=[])])
        problems = validate_result(result)
        assert any("no evidence" in problem for problem in problems)

    def test_evidence_that_does_not_exist_is_a_defect(self) -> None:
        """A dangling id looks like provenance to a reader and is not."""
        result = result_with([available_claim(evidence_ids=["ev-missing"])])
        assert any("unknown evidence" in problem for problem in validate_result(result))

    def test_evidence_pointing_at_no_snapshot_is_a_defect(self) -> None:
        """A reviewer must be able to get from a claim to re-fetchable bytes."""
        result = result_with([available_claim()])
        result.evidence = [evidence(snapshot_id="snap-missing")]
        assert any("unknown snapshot" in problem for problem in validate_result(result))

    def test_a_derived_claim_may_cite_other_claims_instead_of_a_source(self) -> None:
        """Computed values have no page to point at; they point at their inputs."""
        derived = available_claim(
            claim_key="derived",
            field="revenue_growth",
            family="financials",
            value=0.14,
            evidence_ids=[],
            derived_from=["revenue:2024", "revenue:2023"],
        )
        assert validate_result(result_with([derived])) == []

    def test_a_non_available_claim_needs_no_evidence(self) -> None:
        absent = available_claim(
            claim_key="absent",
            field="website",
            family="web_presence",
            value=None,
            availability="not_available",
            evidence_ids=[],
            reason="no candidate domain resolved",
        )
        assert validate_result(result_with([absent])) == []

    def test_a_locator_is_carried_so_the_exact_span_can_be_found(self) -> None:
        """A bare URL is not provenance for a specific number."""
        record = evidence().to_dict()
        assert record["locator"]["kind"] == "json_pointer"
        assert record["locator"]["value"] == "/navn"
        assert record["content_sha256"]


class TestClaimIntegrity:
    def test_a_duplicate_claim_key_is_a_defect(self) -> None:
        result = result_with([available_claim(), available_claim()])
        assert any("duplicate claim key" in problem for problem in validate_result(result))

    def test_a_claim_about_another_company_is_a_defect(self) -> None:
        """This is the wrong-company publication the hard gate forbids."""
        stray = Claim(
            claim_key="stray",
            field="legal_name",
            family="legal_identity",
            value="ANNEN BEDRIFT AS",
            availability="available",
            organisation_number="986270027",
            evidence_ids=["ev-1"],
        )
        assert any("another company" in problem for problem in validate_result(result_with([stray])))

    def test_a_non_terminal_status_is_a_defect(self) -> None:
        result = result_with([available_claim()], status="in_progress")
        assert any("non-terminal" in problem for problem in validate_result(result))


class TestClaimKeys:
    def test_keys_are_stable_across_runs(self) -> None:
        """Refresh compares by key; instability would manufacture false changes."""
        assert claim_key(ORG, "revenue", 2024) == claim_key(ORG, "revenue", "2024")

    def test_whitespace_and_case_in_a_discriminator_do_not_change_the_key(self) -> None:
        assert claim_key(ORG, "job_posting", "  Senior  Selger ") == claim_key(
            ORG, "job_posting", "senior selger"
        )

    def test_different_instances_of_a_repeated_field_get_different_keys(self) -> None:
        assert claim_key(ORG, "revenue", 2024) != claim_key(ORG, "revenue", 2023)

    def test_keys_are_scoped_to_the_company(self) -> None:
        assert claim_key(ORG, "legal_name") != claim_key("986270027", "legal_name")

    def test_ids_are_deterministic_over_identical_inputs(self) -> None:
        assert stable_id("ev", "https://example.no", "/navn") == stable_id(
            "ev", "https://example.no", "/navn"
        )
        assert stable_id("ev", "https://example.no") != stable_id("ev", "https://example.com")


class TestEnvelopeShape:
    def test_the_organiser_required_sections_are_all_present(self) -> None:
        envelope = build_envelope(
            result_with([available_claim()]),
            run_id="t",
            started_at="2026-09-13T04:00:00Z",
            completed_at="2026-09-13T04:00:09Z",
            pipeline_version="1.0.0",
        )
        for key in (
            "organisation_number",
            "run",
            "claims",
            "evidence",
            "changes",
            "errors",
            "operations",
        ):
            assert key in envelope
        assert envelope["run"]["terminal_status"] in TERMINAL_STATES

    def test_timestamps_are_parseable_utc(self) -> None:
        """Reviewers compare observation times across runs; they have to be real."""
        parsed = parse_utc("2026-09-13T04:00:00Z")
        assert parsed is not None and parsed.tzinfo is not None
        assert parse_utc("not a time") is None
        assert parse_utc(None) is None

    def test_coverage_is_counted_per_family_and_state(self) -> None:
        claims = [
            available_claim(),
            available_claim(
                claim_key="site",
                field="website",
                family="web_presence",
                value=None,
                availability="blocked",
                evidence_ids=[],
                reason="403 from the registry-declared site",
            ),
        ]
        summary = coverage_summary(claims)
        assert summary["legal_identity"]["available"] == 1
        assert summary["web_presence"]["blocked"] == 1
        assert summary["web_presence"]["available"] == 0
