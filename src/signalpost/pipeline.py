"""Per-company orchestration: organisation number in, terminal envelope out.

The evaluator requires **exactly one terminal envelope per input**, always. So the
controlling rule here is that no stage may abort the company. Every stage is wrapped;
a stage that fails contributes an error record and `failed`/`blocked` claims, and the
company still terminates with an envelope.

Stage order is chosen so the cheap authoritative source runs first and the expensive
open web runs last, gated on identity:

1. **Registry** — the official record. Establishes legal identity and supplies the
   discriminators (name, address, website, phone) that every later match is judged
   against. If this fails, there is no identity, so the web stage is skipped rather
   than run blind.
2. **Identity** — builds the match profile from registry claims only.
3. **Website** — verifies a candidate site against that profile before extracting
   anything. Claims from an unverified site are never published as the company's.
4. **Refresh** — diffs against the previous run, if one was supplied.
5. **Synthesis** — a brief built strictly from claims that already passed validation.

Terminal state is derived from what was actually achieved, never asserted:

* `completed` — identity established and the external families were genuinely attempted
* `partial` — identity established but some stage could not finish
* `not_found` — the organisation number is not in the registry
* `blocked` — every source refused access
* `failed` — the company could not be processed at all
"""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from . import refresh as refresh_mod
from . import synthesis as synthesis_mod
from .budget import Budget, BudgetExceeded
from .fetch import Fetcher
from .identity import CompanyIdentity
from .models import (
    Claim,
    CompanyResult,
    Evidence,
    Snapshot,
    build_envelope,
    claim_key,
    utc_now,
    validate_result,
)
from .registry import RegistryClient
from .website import WebsiteResearcher

PIPELINE_VERSION = "1.0.0"

# Families the external evaluator scores. Used to decide whether a run genuinely
# attempted the externally-visible work or only mirrored the registry.
EXTERNAL_ATTEMPT_FIELDS = ("website", "email", "phone", "business_description")


@dataclass(slots=True)
class CompanyRequest:
    """One unit of work."""

    organisation_number: str
    previous: dict[str, Any] | None = None
    page_budget: int | None = None
    hints: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StageOutcome:
    """What one stage produced, including how it failed if it did."""

    name: str
    ok: bool
    claims: list[Claim] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    snapshots: list[Snapshot] = field(default_factory=list)
    error: dict[str, Any] | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    seconds: float = 0.0


class CompanyPipeline:
    """Runs every stage for one company and always terminates it."""

    def __init__(
        self,
        fetcher: Fetcher,
        *,
        max_pages: int = 5,
        today: date | None = None,
        include_financials: bool = True,
        include_structure: bool = True,
        include_roles: bool = True,
    ) -> None:
        self.fetcher = fetcher
        self.registry = RegistryClient(fetcher)
        self.website = WebsiteResearcher(fetcher, max_pages=max_pages, today=today)
        self.include_financials = include_financials
        self.include_structure = include_structure
        self.include_roles = include_roles

    # -- stage wrapper --------------------------------------------------------

    def _stage(self, name: str, org: str, work) -> StageOutcome:
        """Run a stage so that nothing it does can prevent the company terminating."""
        started = time.monotonic()
        try:
            outcome = work()
            outcome.seconds = round(time.monotonic() - started, 3)
            return outcome
        except BudgetExceeded as exc:
            # Budget exhaustion is expected operation, not a defect: record and stop.
            return StageOutcome(
                name=name,
                ok=False,
                error={
                    "stage": name,
                    "organisation_number": org,
                    "type": "budget_exceeded",
                    "message": str(exc),
                    "recoverable": False,
                },
                seconds=round(time.monotonic() - started, 3),
            )
        except Exception as exc:  # noqa: BLE001 - deliberate: one bad company must not stop the batch
            return StageOutcome(
                name=name,
                ok=False,
                error={
                    "stage": name,
                    "organisation_number": org,
                    "type": type(exc).__name__,
                    "message": str(exc)[:500],
                    "trace": traceback.format_exc(limit=3)[-800:],
                    "recoverable": True,
                },
                seconds=round(time.monotonic() - started, 3),
            )

    # -- stages ---------------------------------------------------------------

    def _registry_stage(self, org: str) -> StageOutcome:
        body, response, is_subunit = self.registry.fetch_entity(org)
        snapshots: list[Snapshot] = [response.snapshot] if response.snapshot else []

        if body is None:
            # Distinguish "no such company" from "we could not ask".
            reachable = response.status > 0
            availability = "not_available" if reachable and response.status in {404, 410} else "failed"
            reason = (
                "Organisation number is not present in the Norwegian registry"
                if availability == "not_available"
                else (response.error or f"Registry request failed with status {response.status}")
            )
            return StageOutcome(
                name="registry",
                ok=False,
                claims=[
                    Claim(
                        claim_key=claim_key(org, "legal_name"),
                        organisation_number=org,
                        field="legal_name",
                        family="legal_identity",
                        value=None,
                        availability=availability,
                        reason=reason,
                    )
                ],
                snapshots=snapshots,
                detail={
                    "found": False,
                    "status": response.status,
                    "not_found": availability == "not_available",
                },
            )

        claims, evidence = self.registry.entity_claims(org, body, response)
        detail: dict[str, Any] = {"found": True, "is_subunit": is_subunit, "stages": {}}

        # Optional registry stages. Each is independent: one failing leaves the others.
        for enabled, name, call in (
            (self.include_roles, "roles", lambda: self.registry.role_claims(org)),
            (self.include_financials, "accounts", lambda: self.registry.account_claims(org)),
            (self.include_structure, "structure", lambda: self.registry.structure_claims(org)),
        ):
            if not enabled:
                continue
            try:
                sub_claims, sub_evidence, sub_snapshots, sub_detail = call()
                claims.extend(sub_claims)
                evidence.extend(sub_evidence)
                snapshots.extend(sub_snapshots)
                detail["stages"][name] = sub_detail
            except BudgetExceeded:
                detail["stages"][name] = {"skipped": "budget exhausted"}
                break
            except Exception as exc:  # noqa: BLE001
                detail["stages"][name] = {"error": f"{type(exc).__name__}: {exc}"[:200]}

        if response.snapshot:
            snapshots.append(response.snapshot)
        return StageOutcome(
            name="registry",
            ok=True,
            claims=claims,
            evidence=evidence,
            snapshots=snapshots,
            detail=detail,
        )

    def _website_stage(
        self, org: str, identity: CompanyIdentity, page_budget: int | None
    ) -> StageOutcome:
        result = self.website.research(org, identity, page_budget=page_budget)
        return StageOutcome(
            name="website",
            ok=True,
            claims=result.claims,
            evidence=result.evidence,
            snapshots=result.snapshots,
            detail={
                "verified_url": result.verified_url,
                "pages": list(result.pages),
                "verdict": result.verdict.to_dict() if result.verdict else None,
                "rejected": list(result.rejected),
                "injection_reports": list(result.injection_reports),
                "notes": list(result.notes),
                "discovery": result.discovery.to_dict() if result.discovery else None,
                "access_refused": list(result.access_refused),
            },
        )

    # -- terminal state -------------------------------------------------------

    @staticmethod
    def _terminal_state(
        registry: StageOutcome, website: StageOutcome | None, claims: list[Claim]
    ) -> tuple[str, str]:
        if not registry.ok:
            if registry.detail.get("not_found"):
                return "not_found", "Organisation number is not registered in Norway"
            if registry.error and registry.error.get("type") == "budget_exceeded":
                return "failed", "Run budget was exhausted before the registry could be read"
            return "failed", "The official registry could not be read for this company"

        available = [item for item in claims if item.availability == "available"]
        if not available:
            return "failed", "No claim could be established from any source"

        states = {item.availability for item in claims}
        if states and states.issubset({"blocked", "failed", "not_applicable"}):
            return "blocked", "Every source refused or failed"

        # An explicit not_available still counts as an attempt; the claim simply being
        # absent does not.
        attempted_external = any(item.field in EXTERNAL_ATTEMPT_FIELDS for item in claims)
        if website is not None and not website.ok:
            return "partial", "Website research could not complete"
        if not attempted_external:
            return "partial", "External families were not attempted"
        if website is not None and website.detail.get("verified_url"):
            return "completed", "Registry and verified website were both researched"
        return "partial", "No company website could be verified"

    # -- entry point ----------------------------------------------------------

    def run(self, request: CompanyRequest, *, run_id: str = "adhoc") -> CompanyResult:
        org = request.organisation_number
        started_at = utc_now()
        started = time.monotonic()

        result = CompanyResult(
            organisation_number=org, terminal_status="failed", started_at=started_at
        )
        stage_times: dict[str, float] = {}
        requests_before = self.fetcher.budget.snapshot().requests_used

        registry = self._stage("registry", org, lambda: self._registry_stage(org))
        stage_times["registry"] = registry.seconds
        result.claims.extend(registry.claims)
        result.evidence.extend(registry.evidence)
        result.snapshots.extend(registry.snapshots)
        if registry.error:
            result.errors.append(registry.error)

        website: StageOutcome | None = None
        identity: CompanyIdentity | None = None

        if registry.ok:
            identity = CompanyIdentity.from_claims(org, registry.claims)
            result.identity = {
                "organisation_number": org,
                "names": identity.all_names,
                "registry_website": identity.registry_website,
                "cities": sorted(identity.cities),
                "postal_codes": sorted(identity.postal_codes),
                "subunit_numbers": sorted(identity.subunit_numbers),
            }
            website = self._stage(
                "website", org, lambda: self._website_stage(org, identity, request.page_budget)
            )
            stage_times["website"] = website.seconds
            result.claims.extend(website.claims)
            result.evidence.extend(website.evidence)
            result.snapshots.extend(website.snapshots)
            if website.error:
                result.errors.append(website.error)
            if website.detail:
                result.identity["website_verdict"] = website.detail.get("verdict")
                result.identity["verified_url"] = website.detail.get("verified_url")
                result.identity["rejected_candidates"] = website.detail.get("rejected", [])
                result.identity["discovery"] = website.detail.get("discovery")
                if website.detail.get("injection_reports"):
                    result.operations["injection_reports"] = website.detail["injection_reports"]
                discovery = website.detail.get("discovery") or {}
                result.operations["dns_lookups"] = discovery.get("dns_lookups", 0)
                result.operations["candidates_considered"] = discovery.get("considered", 0)
                result.operations["candidates_fetched"] = len(discovery.get("candidates", []))
        else:
            result.identity = {"organisation_number": org, "names": [], "resolved": False}

        # Refresh: compare against the previous envelope before timestamps are stamped,
        # so retrieval time never leaks into the comparison.
        previous = (
            refresh_mod.previous_from_envelope(request.previous) if request.previous else {}
        )
        refresh_mod.carry_observation_times(previous, result.claims)
        if request.previous:
            events = refresh_mod.diff(org, previous, result.claims)
            result.changes = [item.to_dict() for item in events]
            result.refresh = {
                **refresh_mod.summarise(events),
                "compared_against": request.previous.get("run", {}).get("run_id"),
            }
        else:
            result.refresh = {
                "total_changes": 0,
                "material_changes": 0,
                "by_type": {},
                "by_family": {},
                "idempotent": True,
                "material_fields": [],
                "compared_against": None,
                "note": "First observation of this company; no baseline to compare against",
            }

        # Validate before synthesising, so the brief can only be built from claims that
        # already passed the integrity checks.
        problems = validate_result(result)
        if problems:
            result.errors.append(
                {
                    "stage": "validation",
                    "organisation_number": org,
                    "type": "validation_failed",
                    "message": "; ".join(problems[:10])[:600],
                    "count": len(problems),
                    "recoverable": True,
                }
            )

        brief = synthesis_mod.build_brief(org, result.claims)
        result.synthesis = brief.to_dict()

        state, reason = self._terminal_state(registry, website, result.claims)
        result.terminal_status = state
        result.completed_at = utc_now()

        snapshot = self.fetcher.budget.snapshot()
        result.operations.update(
            {
                "pipeline_version": PIPELINE_VERSION,
                "terminal_reason": reason,
                "stage_seconds": stage_times,
                "wall_seconds": round(time.monotonic() - started, 3),
                "requests_used": snapshot.requests_used - requests_before,
                "validation_problems": len(problems),
                "claims_total": len(result.claims),
                "claims_available": sum(
                    1 for item in result.claims if item.availability == "available"
                ),
                "evidence_total": len(result.evidence),
                "snapshots_total": len(result.snapshots),
            }
        )
        return result

    def envelope(self, request: CompanyRequest, *, run_id: str = "adhoc") -> dict[str, Any]:
        """Run the company and render its terminal envelope."""
        result = self.run(request, run_id=run_id)
        return build_envelope(
            result,
            run_id=run_id,
            started_at=result.started_at,
            completed_at=result.completed_at or utc_now(),
            pipeline_version=PIPELINE_VERSION,
        )


def failed_envelope(org: str, run_id: str, message: str) -> dict[str, Any]:
    """A terminal envelope for a company that could not even enter the pipeline.

    The evaluator counts envelopes, not successes. A company that crashes the worker
    still owes exactly one envelope, and this is it.
    """
    now = utc_now()
    result = CompanyResult(
        organisation_number=org,
        terminal_status="failed",
        started_at=now,
        completed_at=now,
        errors=[
            {
                "stage": "pipeline",
                "organisation_number": org,
                "type": "pipeline_failure",
                "message": message[:500],
                "recoverable": False,
            }
        ],
        operations={"pipeline_version": PIPELINE_VERSION, "terminal_reason": message[:200]},
    )
    return build_envelope(
        result,
        run_id=run_id,
        started_at=now,
        completed_at=now,
        pipeline_version=PIPELINE_VERSION,
    )
