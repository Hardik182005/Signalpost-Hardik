"""Brønnøysundregistrene (BRREG) client: the authoritative spine of every profile.

BRREG is the legal source of truth for Norwegian companies, so registry claims are
the anchor that external claims are matched against. Everything here produces typed
`Claim` objects with `Evidence` pointing at a JSON pointer inside a stored snapshot,
so any published number can be traced back to the exact field it was read from.

Two deliberate policies:

* **Null is not zero.** A missing `antallAnsatte` becomes `not_available`, never 0.
  Conflating them would publish a false financial fact.
* **Minimal personal data.** Role records include each person's date of birth. Role
  holder names and role types are published because they are the substance of the
  public role register; birth dates are dropped because they are not needed for
  company research and are unnecessary personal data to redistribute.
"""

from __future__ import annotations

from typing import Any, Iterable

from .budget import BudgetExceeded
from .fetch import Fetcher, Response
from .models import Claim, Evidence, Snapshot, claim_key, stable_id

ENTITY_URL = "https://data.brreg.no/enhetsregisteret/api/enheter/{org}"
SUBENTITY_URL = "https://data.brreg.no/enhetsregisteret/api/underenheter/{org}"
ROLES_URL = "https://data.brreg.no/enhetsregisteret/api/enheter/{org}/roller"
SUBUNITS_URL = (
    "https://data.brreg.no/enhetsregisteret/api/underenheter?overordnetEnhet={org}&size=100"
)
GROUP_URL = "https://data.brreg.no/enhetsregisteret/api/konsernstruktur/{org}"
ACCOUNTS_URL = "https://data.brreg.no/regnskapsregisteret/regnskap/{org}"

# The registry is the spine of every profile: legal identity, accounts and roles all
# come from it, so a lost registry answer costs far more coverage than a lost website
# page. Measured in a live 100-company run with the fetcher defaults (2 attempts, 12 s):
# 11 companies lost their accounts or roles to a timeout or a connection reset from
# data.brreg.no, and a 12th lost its whole profile. Registry calls therefore get more
# attempts and a longer timeout. Each attempt is still charged to the request budget.
REGISTRY_ATTEMPTS = 4
REGISTRY_TIMEOUT = 20.0

# Role codes worth publishing as leadership. Excludes purely administrative markers.
LEADERSHIP_ROLES = {
    "DAGL": "Chief executive / managing director",
    "LEDE": "Chair of the board",
    "NEST": "Deputy chair",
    "MEDL": "Board member",
    "VARA": "Deputy board member",
    "INNH": "Sole proprietor",
    "KOMP": "General partner",
    "DTPR": "Signatory (prokura)",
    "DTSO": "Signatory",
    "REVI": "Auditor",
    "REGN": "Accountant",
    "KONT": "Contact person",
    "FFØR": "Accountant",
    "BOBE": "Bankruptcy administrator",
    "BEST": "Managing board",
}

# Financial lines lifted into first-class claims, with their JSON path and meaning.
FINANCIAL_LINES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("revenue", ("resultatregnskapResultat", "driftsresultat", "driftsinntekter", "sumDriftsinntekter"), "Total operating income"),
    ("operating_costs", ("resultatregnskapResultat", "driftsresultat", "driftskostnad", "sumDriftskostnad"), "Total operating costs"),
    ("operating_profit", ("resultatregnskapResultat", "driftsresultat", "driftsresultat"), "Operating profit"),
    ("profit_before_tax", ("resultatregnskapResultat", "ordinaertResultatFoerSkattekostnad"), "Ordinary profit before tax"),
    ("net_profit", ("resultatregnskapResultat", "aarsresultat"), "Profit for the year"),
    ("total_assets", ("eiendeler", "sumEiendeler"), "Total assets"),
    ("total_equity", ("egenkapitalGjeld", "egenkapital", "sumEgenkapital"), "Total equity"),
    ("total_equity_and_liabilities", ("egenkapitalGjeld", "sumEgenkapitalGjeld"), "Total equity and liabilities"),
)


def _dig(value: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _pointer(*path: Any) -> str:
    """RFC 6901 JSON pointer, so evidence names the exact field that was read."""
    escaped = [str(part).replace("~", "~0").replace("/", "~1") for part in path]
    return "/" + "/".join(escaped)


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return value


class RegistryClient:
    """Fetches and normalises the official registry view of one company."""

    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    # -- fetch helpers --------------------------------------------------------

    def _json(self, url: str, purpose: str, source_class: str) -> tuple[Any, Response]:
        try:
            return self.fetcher.get_json(
                url,
                purpose=purpose,
                source_class=source_class,
                # BRREG publishes open data under NLOD and its robots file does not
                # restrict the API, but we still check it like any other host.
                respect_robots=True,
                attempts=REGISTRY_ATTEMPTS,
                timeout=REGISTRY_TIMEOUT,
            )
        except BudgetExceeded as exc:
            return None, Response(
                ok=False, url=url, final_url=url, status=0, error=str(exc), error_kind="budget"
            )

    def _evidence(
        self,
        response: Response,
        pointer: str,
        *,
        excerpt: Any = None,
        method: str = "brreg_json",
    ) -> Evidence | None:
        """Build claim-level evidence anchored to a JSON pointer in a snapshot."""
        if response.snapshot is None:
            return None
        from .models import Locator

        text = None if excerpt is None else str(excerpt)[:200]
        return Evidence(
            id=stable_id("ev", response.snapshot.id, pointer, text or ""),
            snapshot_id=response.snapshot.id,
            source_url=response.snapshot.final_url,
            source_class=response.snapshot.source_class,
            retrieved_at=response.snapshot.retrieved_at,
            content_sha256=response.content_sha256,
            locator=Locator(kind="json_pointer", value=pointer, excerpt=text),
            claim_span=text,
            extraction_method=method,
        )

    # -- entity ---------------------------------------------------------------

    def fetch_entity(self, org: str) -> tuple[dict[str, Any] | None, Response, bool]:
        """Fetch a company, falling back to the sub-entity register.

        Some organisation numbers in the eligible universe are sub-entities
        (underenheter). Treating a 404 on the main register as "not found" without
        checking would wrongly drop real companies.
        """
        body, response = self._json(
            ENTITY_URL.format(org=org), "registry_entity", "official_registry"
        )
        if isinstance(body, dict) and body.get("organisasjonsnummer"):
            return body, response, False
        if response.status in {404, 410}:
            sub_body, sub_response = self._json(
                SUBENTITY_URL.format(org=org), "registry_subentity", "official_registry"
            )
            if isinstance(sub_body, dict) and sub_body.get("organisasjonsnummer"):
                return sub_body, sub_response, True
            return None, sub_response, True
        return None, response, False

    def entity_claims(
        self, org: str, body: dict[str, Any], response: Response
    ) -> tuple[list[Claim], list[Evidence]]:
        claims: list[Claim] = []
        evidence: list[Evidence] = []

        def add(
            field: str,
            family: str,
            value: Any,
            pointer: str,
            *,
            reason_when_missing: str = "Field absent from the registry record",
            **extra: Any,
        ) -> None:
            cleaned = _clean(value)
            key = claim_key(org, field, *extra.pop("discriminators", ()))
            if cleaned is None:
                claims.append(
                    Claim(
                        claim_key=key,
                        field=field,
                        family=family,
                        value=None,
                        availability="not_available",
                        organisation_number=org,
                        reason=reason_when_missing,
                        extraction_method="brreg_json",
                        **extra,
                    )
                )
                return
            item = self._evidence(response, pointer, excerpt=cleaned)
            if item:
                evidence.append(item)
            claims.append(
                Claim(
                    claim_key=key,
                    field=field,
                    family=family,
                    value=cleaned,
                    availability="available",
                    organisation_number=org,
                    evidence_ids=[item.id] if item else [],
                    confidence=1.0,
                    extraction_method="brreg_json",
                    **extra,
                )
            )

        add("legal_name", "legal_identity", body.get("navn"), _pointer("navn"))
        add(
            "organisation_number",
            "legal_identity",
            body.get("organisasjonsnummer"),
            _pointer("organisasjonsnummer"),
        )
        add(
            "legal_form_code",
            "legal_identity",
            _dig(body, "organisasjonsform", "kode"),
            _pointer("organisasjonsform", "kode"),
        )
        add(
            "legal_form_label",
            "legal_identity",
            _dig(body, "organisasjonsform", "beskrivelse"),
            _pointer("organisasjonsform", "beskrivelse"),
        )
        add(
            "registered_at",
            "legal_identity",
            body.get("registreringsdatoEnhetsregisteret"),
            _pointer("registreringsdatoEnhetsregisteret"),
        )
        add(
            "incorporated_at",
            "legal_identity",
            body.get("stiftelsesdato"),
            _pointer("stiftelsesdato"),
        )
        add(
            "industry_code",
            "legal_identity",
            _dig(body, "naeringskode1", "kode"),
            _pointer("naeringskode1", "kode"),
        )
        add(
            "industry_label",
            "legal_identity",
            _dig(body, "naeringskode1", "beskrivelse"),
            _pointer("naeringskode1", "beskrivelse"),
        )
        add(
            "sector_code",
            "legal_identity",
            _dig(body, "institusjonellSektorkode", "beskrivelse"),
            _pointer("institusjonellSektorkode", "beskrivelse"),
        )
        add(
            "vat_registered",
            "legal_identity",
            body.get("registrertIMvaregisteret"),
            _pointer("registrertIMvaregisteret"),
        )

        # Status flags are only meaningful as explicit booleans.
        for field, key_name in (
            ("bankrupt", "konkurs"),
            ("under_liquidation", "underAvvikling"),
            ("under_forced_liquidation", "underTvangsavviklingEllerTvangsopplosning"),
        ):
            raw = body.get(key_name)
            add(field, "legal_identity", raw, _pointer(key_name))

        # Employees: absent and zero are different facts and stay different.
        if body.get("harRegistrertAntallAnsatte") is False:
            claims.append(
                Claim(
                    claim_key=claim_key(org, "employees"),
                    field="employees",
                    family="legal_identity",
                    value=None,
                    availability="not_available",
                    organisation_number=org,
                    reason="Registry states no employee count is registered for this entity",
                    extraction_method="brreg_json",
                )
            )
        else:
            add(
                "employees",
                "legal_identity",
                body.get("antallAnsatte"),
                _pointer("antallAnsatte"),
                effective_date=_clean(body.get("registreringsdatoAntallAnsatteEnhetsregisteret")),
            )

        add("phone", "contact", body.get("telefon"), _pointer("telefon"))
        add("email", "contact", body.get("epostadresse"), _pointer("epostadresse"))

        # The registry website field is a *candidate*, not a verified web presence.
        # It is published under legal_identity and only promoted to web_presence once
        # the identity gate confirms the site belongs to this company.
        add(
            "registry_website",
            "legal_identity",
            body.get("hjemmeside"),
            _pointer("hjemmeside"),
            reason_when_missing="No website recorded in the registry",
        )

        for label, key_name in (("business", "forretningsadresse"), ("postal", "postadresse")):
            block = body.get(key_name)
            if not isinstance(block, dict):
                claims.append(
                    Claim(
                        claim_key=claim_key(org, "address", label),
                        field="address",
                        family="locations",
                        value=None,
                        availability="not_available",
                        organisation_number=org,
                        reason=f"No {label} address in the registry record",
                        extraction_method="brreg_json",
                    )
                )
                continue
            street = " ".join(str(part).strip() for part in (block.get("adresse") or []) if part)
            composed = {
                "kind": label,
                "street": street or None,
                "postal_code": _clean(block.get("postnummer")),
                "city": _clean(block.get("poststed")),
                "municipality": _clean(block.get("kommune")),
                "municipality_number": _clean(block.get("kommunenummer")),
                "country": _clean(block.get("land")),
                "country_code": _clean(block.get("landkode")),
            }
            if not any(value for key, value in composed.items() if key != "kind"):
                continue
            item = self._evidence(response, _pointer(key_name), excerpt=street or composed["city"])
            if item:
                evidence.append(item)
            claims.append(
                Claim(
                    claim_key=claim_key(org, "address", label),
                    field="address",
                    family="locations",
                    value=composed,
                    availability="available",
                    organisation_number=org,
                    evidence_ids=[item.id] if item else [],
                    confidence=1.0,
                    extraction_method="brreg_json",
                )
            )

        for index, entry in enumerate(body.get("historiskeNavn") or []):
            name = _clean(entry.get("navn")) if isinstance(entry, dict) else None
            if not name:
                continue
            # A company can hold the same name over two separate periods (Equinor held
            # "Statoil ASA" twice), so the period start is part of the claim identity.
            held_from = _clean(entry.get("fraDato"))
            pointer = _pointer("historiskeNavn", index, "navn")
            item = self._evidence(response, pointer, excerpt=name)
            if item:
                evidence.append(item)
            claims.append(
                Claim(
                    claim_key=claim_key(org, "former_name", name, held_from or index),
                    field="former_name",
                    family="legal_identity",
                    value={
                        "name": name,
                        "from": _clean(entry.get("fraDato")),
                        "to": _clean(entry.get("tilDato")),
                    },
                    availability="available",
                    organisation_number=org,
                    evidence_ids=[item.id] if item else [],
                    confidence=1.0,
                    extraction_method="brreg_json",
                )
            )

        return claims, evidence

    # -- roles ----------------------------------------------------------------

    def role_claims(self, org: str) -> tuple[list[Claim], list[Evidence], list[Snapshot], dict]:
        body, response = self._json(ROLES_URL.format(org=org), "registry_roles", "official_roles")
        snapshots = [response.snapshot] if response.snapshot else []
        if not isinstance(body, dict):
            return (
                [
                    Claim(
                        claim_key=claim_key(org, "leadership"),
                        field="leadership",
                        family="people",
                        value=None,
                        availability="failed" if response.status == 0 else "not_available",
                        organisation_number=org,
                        reason=response.error or "Role register returned no record",
                        extraction_method="brreg_json",
                    )
                ],
                [],
                snapshots,
                {"status": response.status},
            )

        claims: list[Claim] = []
        evidence: list[Evidence] = []
        published = 0

        for group_index, group in enumerate(body.get("rollegrupper") or []):
            if not isinstance(group, dict):
                continue
            for role_index, role in enumerate(group.get("roller") or []):
                if not isinstance(role, dict) or role.get("avregistrert"):
                    continue
                code = _dig(role, "type", "kode")
                if code not in LEADERSHIP_ROLES:
                    continue
                person = role.get("person") or {}
                enterprise = role.get("enhet") or {}
                if person:
                    names = person.get("navn") or {}
                    holder = " ".join(
                        str(part).strip()
                        for part in (
                            names.get("fornavn"),
                            names.get("mellomnavn"),
                            names.get("etternavn"),
                        )
                        if part
                    ).strip()
                    holder_kind = "person"
                    holder_org = None
                else:
                    holder = _clean(enterprise.get("navn")) or ""
                    holder_kind = "organisation"
                    holder_org = _clean(enterprise.get("organisasjonsnummer"))
                if not holder:
                    continue

                pointer = _pointer("rollegrupper", group_index, "roller", role_index)
                item = self._evidence(response, pointer, excerpt=f"{code}: {holder}")
                if item:
                    evidence.append(item)
                claims.append(
                    Claim(
                        claim_key=claim_key(org, "role", code, holder),
                        field="role",
                        family="people",
                        # Date of birth is deliberately not carried through.
                        value={
                            "role_code": code,
                            "role": _dig(role, "type", "beskrivelse") or LEADERSHIP_ROLES[code],
                            "role_en": LEADERSHIP_ROLES[code],
                            "holder": holder,
                            "holder_kind": holder_kind,
                            "holder_organisation_number": holder_org,
                        },
                        availability="available",
                        organisation_number=org,
                        evidence_ids=[item.id] if item else [],
                        confidence=1.0,
                        effective_date=_clean(group.get("sistEndret")),
                        extraction_method="brreg_json",
                    )
                )
                published += 1

        if not published:
            claims.append(
                Claim(
                    claim_key=claim_key(org, "leadership"),
                    field="leadership",
                    family="people",
                    value=None,
                    availability="not_available",
                    organisation_number=org,
                    reason="No current leadership roles registered",
                    extraction_method="brreg_json",
                )
            )
        return claims, evidence, snapshots, {"status": response.status, "roles": published}

    # -- accounts -------------------------------------------------------------

    def account_claims(
        self, org: str, *, max_years: int = 3
    ) -> tuple[list[Claim], list[Evidence], list[Snapshot], dict]:
        body, response = self._json(
            ACCOUNTS_URL.format(org=org), "registry_accounts", "official_accounts"
        )
        snapshots = [response.snapshot] if response.snapshot else []
        records = body if isinstance(body, list) else []
        if not records:
            availability = "failed" if response.status == 0 else "not_available"
            reason = response.error or "No annual accounts published for this entity"
            return (
                [
                    Claim(
                        claim_key=claim_key(org, "annual_accounts"),
                        field="annual_accounts",
                        family="financials",
                        value=None,
                        availability=availability,
                        organisation_number=org,
                        reason=reason,
                        extraction_method="brreg_json",
                    )
                ],
                [],
                snapshots,
                {"status": response.status, "years": 0},
            )

        claims: list[Claim] = []
        evidence: list[Evidence] = []
        years: list[str] = []

        ordered = sorted(
            records,
            key=lambda item: str(_dig(item, "regnskapsperiode", "tilDato") or ""),
            reverse=True,
        )
        for index, record in enumerate(ordered[:max_years]):
            if not isinstance(record, dict):
                continue
            period_to = _clean(_dig(record, "regnskapsperiode", "tilDato"))
            period_from = _clean(_dig(record, "regnskapsperiode", "fraDato"))
            year = (period_to or "")[:4]
            if not year:
                continue
            years.append(year)
            currency = _clean(record.get("valuta")) or "NOK"
            reporting_period = f"{period_from}/{period_to}" if period_from else year
            scope = "group" if str(record.get("regnskapstype")).upper() == "KONSERN" else "company"

            for field, path, label in FINANCIAL_LINES:
                value = _dig(record, *path)
                key = claim_key(org, field, year, scope)
                if not isinstance(value, (int, float)):
                    claims.append(
                        Claim(
                            claim_key=key,
                            field=field,
                            family="financials",
                            value=None,
                            availability="not_available",
                            organisation_number=org,
                            reason=f"{label} not reported in the {year} filing",
                            reporting_period=reporting_period,
                            scope=scope,
                            extraction_method="brreg_json",
                        )
                    )
                    continue
                pointer = _pointer(index, *path)
                item = self._evidence(response, pointer, excerpt=value)
                if item:
                    evidence.append(item)
                claims.append(
                    Claim(
                        claim_key=key,
                        field=field,
                        family="financials",
                        value=float(value),
                        availability="available",
                        organisation_number=org,
                        evidence_ids=[item.id] if item else [],
                        confidence=1.0,
                        reporting_period=reporting_period,
                        effective_date=period_to,
                        unit="absolute",
                        currency=currency,
                        scope=scope,
                        extraction_method="brreg_json",
                    )
                )

            audited = _dig(record, "revisjon", "ikkeRevidertAarsregnskap")
            if isinstance(audited, bool):
                pointer = _pointer(index, "revisjon", "ikkeRevidertAarsregnskap")
                item = self._evidence(response, pointer, excerpt=audited)
                if item:
                    evidence.append(item)
                claims.append(
                    Claim(
                        claim_key=claim_key(org, "accounts_audited", year),
                        field="accounts_audited",
                        family="financials",
                        value=not audited,
                        availability="available",
                        organisation_number=org,
                        evidence_ids=[item.id] if item else [],
                        confidence=1.0,
                        reporting_period=reporting_period,
                        scope=scope,
                        extraction_method="brreg_json",
                    )
                )

        return claims, evidence, snapshots, {"status": response.status, "years": len(years)}

    # -- structure ------------------------------------------------------------

    def structure_claims(
        self, org: str, *, max_subunits: int = 25
    ) -> tuple[list[Claim], list[Evidence], list[Snapshot], dict]:
        body, response = self._json(
            SUBUNITS_URL.format(org=org), "registry_subunits", "official_subunits"
        )
        snapshots = [response.snapshot] if response.snapshot else []
        claims: list[Claim] = []
        evidence: list[Evidence] = []

        units: Iterable[Any] = _dig(body, "_embedded", "underenheter") or []
        total = _dig(body, "page", "totalElements")
        published = 0
        for index, unit in enumerate(units):
            if published >= max_subunits or not isinstance(unit, dict):
                break
            number = _clean(unit.get("organisasjonsnummer"))
            name = _clean(unit.get("navn"))
            if not number or not name:
                continue
            address = unit.get("beliggenhetsadresse") or unit.get("forretningsadresse") or {}
            pointer = _pointer("_embedded", "underenheter", index)
            item = self._evidence(response, pointer, excerpt=f"{number} {name}")
            if item:
                evidence.append(item)
            claims.append(
                Claim(
                    claim_key=claim_key(org, "subunit", number),
                    field="subunit",
                    family="locations",
                    value={
                        "organisation_number": number,
                        "name": name,
                        "employees": unit.get("antallAnsatte")
                        if unit.get("harRegistrertAntallAnsatte") is not False
                        else None,
                        "city": _clean(address.get("poststed")),
                        "municipality": _clean(address.get("kommune")),
                        "industry_code": _dig(unit, "naeringskode1", "kode"),
                    },
                    availability="available",
                    organisation_number=org,
                    evidence_ids=[item.id] if item else [],
                    confidence=1.0,
                    scope="subunit",
                    extraction_method="brreg_json",
                )
            )
            published += 1

        if not published:
            claims.append(
                Claim(
                    claim_key=claim_key(org, "subunit_count"),
                    field="subunit_count",
                    family="locations",
                    value=None,
                    availability="not_available"
                    if response.status in {200, 404}
                    else "failed",
                    organisation_number=org,
                    reason="No registered sub-units (workplaces) for this entity",
                    extraction_method="brreg_json",
                )
            )
        elif isinstance(total, int):
            item = self._evidence(response, _pointer("page", "totalElements"), excerpt=total)
            if item:
                evidence.append(item)
            claims.append(
                Claim(
                    claim_key=claim_key(org, "subunit_count"),
                    field="subunit_count",
                    family="locations",
                    value=total,
                    availability="available",
                    organisation_number=org,
                    evidence_ids=[item.id] if item else [],
                    confidence=1.0,
                    extraction_method="brreg_json",
                )
            )

        return claims, evidence, snapshots, {"status": response.status, "subunits": published}
