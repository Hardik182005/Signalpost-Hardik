"""Company briefs assembled from claims, never from a model's memory.

The synthesis score rewards a useful summary; the accuracy gate punishes fabrication.
Both point the same way: **every sentence here is generated from claims that already
passed validation, and every sentence carries the claim keys it was built from.**

That is why this module is deterministic and template-driven rather than generative.
A reviewer can take any sentence, follow its `claim_keys` to the claims, follow those
to evidence, and land on a stored snapshot. A language model cannot be audited that
way, so one is not used to state facts. `render_llm_context` exists for the optional
paid path: it hands a model the claims and asks only for rephrasing, with the same
claim keys attached, so the audit trail survives.

No sentence is emitted for a fact that is missing. Absence stays absence.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable

from .models import Claim

# Norwegian legal-form codes to readable English, for the forms that carry meaning to
# a reader. Codes outside this map are reported verbatim rather than guessed at.
LEGAL_FORMS = {
    "AS": "private limited company",
    "ASA": "public limited company",
    "ANS": "general partnership",
    "DA": "partnership with shared liability",
    "ENK": "sole proprietorship",
    "NUF": "Norwegian-registered foreign company",
    "SA": "cooperative",
    "BA": "cooperative",
    "KS": "limited partnership",
    "IKS": "inter-municipal company",
    "KF": "municipal enterprise",
    "SF": "state enterprise",
    "STI": "foundation",
    "FLI": "voluntary organisation",
    "BRL": "housing cooperative",
    "BBL": "housing cooperative federation",
    "ORGL": "other legal entity",
    "SPA": "savings bank",
}

@dataclass(slots=True)
class Sentence:
    """One statement plus the claims that license it."""

    text: str
    claim_keys: list[str] = field(default_factory=list)
    topic: str = "general"

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "claim_keys": list(self.claim_keys), "topic": self.topic}


@dataclass(slots=True)
class Brief:
    """The synthesised company brief."""

    organisation_number: str
    headline: str
    sentences: list[Sentence] = field(default_factory=list)
    highlights: list[dict[str, Any]] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    confidence: str = "medium"
    method: str = "deterministic-template"

    @property
    def summary(self) -> str:
        return " ".join(item.text for item in self.sentences)

    def to_dict(self) -> dict[str, Any]:
        return {
            "organisation_number": self.organisation_number,
            "headline": self.headline,
            "summary": self.summary,
            "sentences": [item.to_dict() for item in self.sentences],
            "highlights": list(self.highlights),
            "gaps": list(self.gaps),
            "confidence": self.confidence,
            "method": self.method,
            # Everything above is traceable to these keys and nothing else.
            "grounded_in": sorted({key for item in self.sentences for key in item.claim_keys}),
        }


def _available(claims: Iterable[Claim]) -> list[Claim]:
    return [item for item in claims if item.availability == "available"]


def _by_field(claims: Iterable[Claim], name: str) -> list[Claim]:
    return [item for item in claims if item.field == name and item.availability == "available"]


def _first(claims: Iterable[Claim], name: str) -> Claim | None:
    found = _by_field(claims, name)
    return found[0] if found else None


def _format_money(value: Any, currency: str | None) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return str(value)
    unit = currency or "NOK"
    if abs(amount) >= 1_000_000_000:
        return f"{amount / 1_000_000_000:,.1f} bn {unit}"
    if abs(amount) >= 1_000_000:
        return f"{amount / 1_000_000:,.1f} m {unit}"
    return f"{amount:,.0f} {unit}"


def _year(value: Any) -> str | None:
    text = str(value or "")
    return text[:4] if len(text) >= 4 and text[:4].isdigit() else None


def _company_scoped(claims: list[Claim], name: str) -> list[Claim]:
    """Company-level figures first; group accounts only when no company figure exists."""
    found = _by_field(claims, name)
    own = [item for item in found if item.scope == "company"]
    return sorted(own or found, key=lambda item: item.reporting_period or "", reverse=True)


def _identity_sentence(claims: list[Claim], org: str) -> Sentence | None:
    name = _first(claims, "legal_name")
    if name is None:
        return None
    keys = [name.claim_key]
    text = f"{name.value} (organisation number {org})"

    form = _first(claims, "legal_form_code")
    if form is not None:
        keys.append(form.claim_key)
        code = str(form.value).upper()
        text += f" is a {LEGAL_FORMS.get(code, f'{code} entity')}"
    else:
        text += " is a registered Norwegian entity"

    label = _first(claims, "industry_label")
    code = _first(claims, "industry_code")
    if label is not None:
        keys.append(label.claim_key)
        suffix = ""
        if code is not None:
            keys.append(code.claim_key)
            suffix = f" (NACE {code.value})"
        text += f" in {str(label.value).lower()}{suffix}"
    elif code is not None:
        keys.append(code.claim_key)
        text += f" under NACE industry code {code.value}"

    founded = _first(claims, "incorporated_at") or _first(claims, "registered_at")
    if founded is not None and _year(founded.value):
        keys.append(founded.claim_key)
        verb = "founded" if founded.field == "incorporated_at" else "registered"
        text += f", {verb} in {_year(founded.value)}"

    city = _registered_city(claims)
    if city is not None:
        keys.append(city[1])
        text += f", with its registered business address in {city[0]}"
    return Sentence(text + ".", keys, "identity")


def _registered_city(claims: list[Claim]) -> tuple[str, str] | None:
    for item in _by_field(claims, "address"):
        value = item.value
        if isinstance(value, dict) and value.get("city") and value.get("kind", "business") == "business":
            return str(value["city"]).title(), item.claim_key
    return None


def _status_sentence(claims: list[Claim]) -> Sentence | None:
    """Only emitted when a status flag is actually set — silence is not a claim."""
    flags: list[str] = []
    keys: list[str] = []
    for name, text in (
        ("bankrupt", "registered as bankrupt"),
        ("under_liquidation", "under liquidation"),
        ("under_forced_liquidation", "under forced liquidation or dissolution"),
    ):
        claim = _first(claims, name)
        if claim is not None and claim.value is True:
            flags.append(text)
            keys.append(claim.claim_key)
    if not flags:
        return None
    return Sentence(
        f"Caution: the registry records the company as {', and '.join(flags)}.", keys, "status"
    )


def _description_sentence(claims: list[Claim]) -> Sentence | None:
    """What it does: the company's own words if verified, else its registered activity."""
    described = _by_field(claims, "business_description")
    if described:
        best = max(described, key=lambda item: len(str(item.value or "")))
        text = _clip(" ".join(str(best.value).split()), 300)
        return Sentence(f'On its verified website the company describes itself as: "{text}".', [best.claim_key], "description")
    registered = _first(claims, "registered_activity")
    if registered is not None:
        text = _clip(" ".join(str(registered.value).split()), 260)
        return Sentence(f'Its registered activity is: "{text}".', [registered.claim_key], "description")
    return None


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text.rstrip(".")
    return text[: limit - 3].rsplit(" ", 1)[0] + "…"


ROLE_ORDER = ("DAGL", "LEDE", "NEST", "INNH", "DTPR", "DTSO", "KOMP", "BEST", "MEDL", "VARA", "REVI", "REGN")
ROLE_LABELS = {
    "DAGL": "chief executive",
    "LEDE": "chair of the board",
    "NEST": "deputy chair",
    "INNH": "owner",
    "DTPR": "partner",
    "DTSO": "partner",
    "KOMP": "general partner",
    "BEST": "managing shipowner",
    "MEDL": "board member",
    "VARA": "deputy board member",
    "REVI": "auditor",
    "REGN": "accountant",
}


def _holder(value: dict[str, Any]) -> str | None:
    holder = value.get("holder") or value.get("name")
    if isinstance(holder, list):
        holder = " ".join(str(item) for item in holder if item)
    holder = " ".join(str(holder or "").split())
    return holder or None


def _people_sentence(claims: list[Claim]) -> Sentence | None:
    """Who runs it, from the registered roles: executive, chair, board size, auditor."""
    by_code: dict[str, list[tuple[str, str]]] = {}
    for role in _by_field(claims, "role"):
        value = role.value
        if not isinstance(value, dict):
            continue
        holder = _holder(value)
        code = str(value.get("role_code") or "").upper()
        if holder and code:
            by_code.setdefault(code, []).append((holder, role.claim_key))
    if not by_code:
        return None

    # One person holding two posts is named once: "Ola Nordmann (chief executive and
    # chair of the board)", not twice.
    titles: dict[str, list[str]] = {}
    keys: list[str] = []
    for code in ("DAGL", "INNH", "LEDE", "KOMP", "BEST"):
        for holder, key in by_code.get(code, [])[:2]:
            titles.setdefault(holder, []).append(ROLE_LABELS[code])
            keys.append(key)
    parts = [f"{holder} ({_join(labels)})" for holder, labels in titles.items()]
    others = {holder for holder, _ in by_code.get("MEDL", [])} - set(titles)
    if others:
        parts.append(f"{len(others)} further board member{'s' if len(others) != 1 else ''}")
        keys.extend(key for holder, key in by_code.get("MEDL", []) if holder in others)
    auditor = by_code.get("REVI") or by_code.get("REGN")
    if auditor:
        code = "REVI" if by_code.get("REVI") else "REGN"
        parts.append(f"{auditor[0][0]} as {ROLE_LABELS[code]}")
        keys.append(auditor[0][1])
    if not parts:
        return None
    return Sentence(f"Leadership on the register: {'; '.join(parts)}.", keys, "people")


def _scale_sentence(claims: list[Claim]) -> Sentence | None:
    parts: list[str] = []
    keys: list[str] = []
    employees = _first(claims, "employees")
    if employees is not None:
        try:
            count = int(employees.value)
        except (TypeError, ValueError):
            count = None
        if count is not None:
            when = f" (registered {employees.effective_date})" if employees.effective_date else ""
            parts.append(f"{count:,} employee{'s' if count != 1 else ''}{when}")
            keys.append(employees.claim_key)
    subunits = [item for item in _by_field(claims, "subunit") if isinstance(item.value, dict)]
    if subunits:
        cities = sorted({str(item.value.get("city")).title() for item in subunits if item.value.get("city")})
        keys.extend(item.claim_key for item in subunits)
        where = f" in {', '.join(cities[:4])}{' and more' if len(cities) > 4 else ''}" if cities else ""
        total = _first(claims, "subunit_count")
        count = total.value if total is not None and isinstance(total.value, int) else len(subunits)
        if total is not None:
            keys.append(total.claim_key)
        qualifier = f"; {len(subunits)} detailed in this profile{where}" if count > len(subunits) else where
        parts.append(f"{count} registered workplace{'s' if count != 1 else ''}{qualifier}")
    if not parts:
        return None
    return Sentence(f"Scale: {'; '.join(parts)}.", keys, "scale")


def _ratio(numerator: Claim | None, denominator: Claim | None) -> float | None:
    if numerator is None or denominator is None:
        return None
    if any(getattr(numerator, key) != getattr(denominator, key)
           for key in ("scope", "reporting_period", "currency", "unit")):
        return None
    try:
        top, bottom = float(numerator.value), float(denominator.value)
    except (TypeError, ValueError):
        return None
    return top / bottom if bottom and math.isfinite(top) and math.isfinite(bottom) else None


def _financial_sentence(claims: list[Claim]) -> Sentence | None:
    # Choose one reporting scope for the entire sentence; missing company figures
    # must never be filled with consolidated group figures.
    financial_fields = {"revenue", "net_profit", "profit_before_tax", "operating_profit",
                        "total_equity", "total_assets", "accounts_audited"}
    figures = [item for item in _available(claims) if item.field in financial_fields]
    anchors = [item for item in figures if item.field in {"revenue", "net_profit", "profit_before_tax"}]
    own = [item for item in anchors if item.scope == "company"]
    anchors = own or [item for item in anchors if item.scope == "group"]
    if not anchors:
        return None
    anchor = max(anchors, key=lambda item: item.reporting_period or "")
    period, scope = anchor.reporting_period, anchor.scope
    year = _year((period or "").split("/")[-1]) or "the latest filed year"

    def figure(field: str) -> Claim | None:
        return next((item for item in figures if item.field == field
                     and item.reporting_period == period and item.scope == scope), None)

    keys: list[str] = []
    parts: list[str] = []
    latest_revenue = figure("revenue")
    operating = figure("operating_profit")
    net = figure("net_profit") or figure("profit_before_tax")
    for item, label in ((latest_revenue, "revenue"), (operating, "an operating result"),
                        (net, "a net result" if net and net.field == "net_profit" else "a pre-tax result")):
        if item is not None:
            keys.append(item.claim_key)
            parts.append(f"{label} of {_format_money(item.value, item.currency)}")
    subject = "the group" if scope == "group" else "the company"
    text = f"In its filed accounts for {year}, {subject} reported {_join(parts)}"

    derived: list[str] = []
    margin = _ratio(operating, latest_revenue)
    if margin is not None and latest_revenue is not None and float(latest_revenue.value) > 0:
        derived.append(f"an operating margin of {margin * 100:.1f}%")
    equity, assets = figure("total_equity"), figure("total_assets")
    equity_ratio = _ratio(equity, assets)
    if equity_ratio is not None and equity is not None and assets is not None and float(assets.value) > 0:
        keys.extend([equity.claim_key, assets.claim_key])
        derived.append(f"an equity ratio of {equity_ratio * 100:.0f}%")
        if float(equity.value) < 0:
            derived.append("negative equity")
    if derived:
        text += f"; calculated from those figures, that is {_join(derived)}"
    audited = figure("accounts_audited")
    if audited is not None and audited.value is False:
        keys.append(audited.claim_key)
        text += " (the accounts were not audited)"
    if latest_revenue is not None and period:
        older = sorted((item for item in figures if item.field == "revenue"
                        and item.scope == scope and item.reporting_period
                        and item.reporting_period < period
                        and item.currency == latest_revenue.currency
                        and item.unit == latest_revenue.unit),
                       key=lambda item: item.reporting_period, reverse=True)
        if older:
            previous = older[0]
            try:
                old, new = float(previous.value), float(latest_revenue.value)
                change = (new - old) / old * 100 if old > 0 and math.isfinite(old) and math.isfinite(new) else None
            except (TypeError, ValueError):
                change = None
            if change is not None:
                keys.append(previous.claim_key)
                previous_period = previous.reporting_period
                text += f"; revenue was {'up' if change >= 0 else 'down'} {abs(change):.0f}% versus the reported period {previous_period}"
    return Sentence(text + ".", keys, "financials")


def _join(parts: list[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


PLATFORM_NAMES = {
    "linkedin": "LinkedIn",
    "youtube": "YouTube",
    "x": "X",
    "tiktok": "TikTok",
    "github": "GitHub",
}


def _presence_sentence(claims: list[Claim]) -> Sentence | None:
    website = _first(claims, "website")
    socials = _by_field(claims, "social_profile")
    if website is None and not socials:
        return None
    keys: list[str] = []
    parts: list[str] = []
    if website is not None:
        keys.append(website.claim_key)
        parts.append(f"a verified website at {website.value}")
    if socials:
        keys.extend(item.claim_key for item in socials)
        networks = sorted(
            {
                PLATFORM_NAMES.get(str(item.value.get("platform")), str(item.value.get("platform")).title())
                if isinstance(item.value, dict)
                else "social"
                for item in socials
            }
        )
        parts.append(f"company-owned profiles on {_join(networks[:6])}")
    return Sentence(f"Online, it has {_join(parts)}.", keys, "web_presence")


def _contact_sentence(claims: list[Claim]) -> Sentence | None:
    emails = _by_field(claims, "email")
    phones = _by_field(claims, "phone")
    if not (emails or phones):
        return None
    keys: list[str] = []
    parts: list[str] = []
    if emails:
        keys.append(emails[0].claim_key)
        parts.append(f"by email at {emails[0].value}")
    if phones:
        keys.append(phones[0].claim_key)
        parts.append(f"by phone on {phones[0].value}")
    return Sentence(f"It can be contacted {' and '.join(parts)}.", keys, "contact")


def _careers_checked(claim: Claim) -> bool:
    """A careers page was read and listed nothing: an answer, unlike an unread site."""
    return (
        claim.field == "job_postings"
        and claim.availability == "not_available"
        and (claim.reason or "").startswith("Careers page reachable")
    )


def _jobs_sentence(claims: list[Claim]) -> Sentence | None:
    """Whether it appears to be hiring — including a clear 'no' or 'unknown'."""
    jobs = _by_field(claims, "job_posting")
    if jobs:
        titles = []
        for job in jobs[:3]:
            value = job.value
            title = value.get("title") if isinstance(value, dict) else value
            if title:
                titles.append(f'"{title}"')
        more = f" and {len(jobs) - len(titles)} more" if len(jobs) > len(titles) else ""
        return Sentence(
            f"It appears to be hiring: {len(jobs)} open role{'s' if len(jobs) != 1 else ''} listed, including {', '.join(titles)}{more}.",
            [item.claim_key for item in jobs],
            "jobs",
        )
    signal = _first(claims, "is_hiring")
    if signal is not None:
        return Sentence(
            "Its careers page says it is recruiting, but no individual openings could be read.",
            [signal.claim_key],
            "jobs",
        )
    checked = next((item for item in claims if _careers_checked(item)), None)
    if checked is not None:
        return Sentence(
            "No open roles were listed on its careers page when checked.", [checked.claim_key], "jobs"
        )
    return None


def _activity_sentence(claims: list[Claim]) -> Sentence | None:
    items = _by_field(claims, "activity_item")
    if not items:
        return None

    def when(item: Claim) -> str:
        value = item.value if isinstance(item.value, dict) else {}
        return str(value.get("published_at") or value.get("date") or item.effective_date or "")

    newest = max(items, key=when)
    value = newest.value if isinstance(newest.value, dict) else {"title": newest.value}
    date = when(newest)
    stamp = f" on {date}" if date else ""
    count = f"{len(items)} dated items; the latest" if len(items) > 1 else "One dated item"
    return Sentence(
        f"Recent public activity: {count}{stamp} was \"{_clip(str(value.get('title')), 140)}\".",
        [item.claim_key for item in items],
        "activity",
    )


def _changes_sentence(refresh: dict[str, Any] | None, changes: list[dict[str, Any]] | None) -> Sentence | None:
    """What changed since the previous run, or that there is no baseline yet."""
    if not refresh:
        return None
    if not refresh.get("compared_against"):
        return Sentence("This is the first observation of the company, so no changes are reported yet.", [], "changes")
    material = [item for item in (changes or []) if item.get("material")]
    if not material:
        return Sentence(
            f"No material changes since the previous run ({refresh.get('compared_against')}).", [], "changes"
        )
    described = []
    for item in material[:3]:
        verb = {"added": "new", "removed": "removed", "value_changed": "changed"}.get(str(item.get("change_type")), str(item.get("change_type")))
        described.append(f"{verb} {str(item.get('field')).replace('_', ' ')}")
    more = f" and {len(material) - 3} more" if len(material) > 3 else ""
    return Sentence(
        f"Since the previous run: {len(material)} material change{'s' if len(material) != 1 else ''} ({', '.join(described)}{more}).",
        [str(item.get("claim_key")) for item in material if item.get("claim_key")],
        "changes",
    )


def _unknowns_sentence(claims: list[Claim]) -> Sentence | None:
    """The questions this profile cannot answer, said once in plain words."""
    have = {item.field for item in claims if item.availability == "available"}
    missing: list[str] = []
    if "website" not in have:
        missing.append("an official website")
    elif "social_profile" not in have:
        missing.append("company-owned social profiles")
    if "employees" not in have:
        missing.append("an employee count")
    if "revenue" not in have:
        missing.append("filed revenue")
    hiring_checked = any(_careers_checked(item) for item in claims)
    if not ({"job_posting", "is_hiring"} & have) and not hiring_checked:
        missing.append("whether it is hiring")
    if "activity_item" not in have:
        missing.append("dated public activity")
    if not missing:
        return None
    website = next((item for item in claims if item.field == "website" and item.availability != "available"), None)
    why = ""
    if website is not None and website.reason and "website" not in have:
        why = f". Website: {website.availability} ({website.reason.rstrip('.')})"
    return Sentence(f"Not established from permitted sources: {_join(missing)}{why}.", [], "unknowns")


def _gaps(claims: list[Claim]) -> list[str]:
    """What is missing, and why — stated rather than papered over.

    A registry field that is empty is not a gap when the same fact was found on the
    verified website: "phone: not_available" beside a published phone number reads as a
    contradiction.
    """
    available_fields = {item.field for item in claims if item.availability == "available"}
    gaps: list[str] = []
    seen: set[str] = set()
    for claim in claims:
        if claim.availability in ("available", "not_applicable"):
            continue
        if claim.field in available_fields:
            continue
        if claim.field == "site_contact" and {"email", "phone"} & available_fields:
            continue
        if claim.field == "job_postings" and "job_posting" in available_fields:
            continue
        label = f"{claim.field}: {claim.availability}"
        if claim.reason:
            label += f" ({claim.reason})"
        if label not in seen:
            seen.add(label)
            gaps.append(label)
    return gaps[:12]


def _confidence(claims: list[Claim], available: list[Claim]) -> str:
    if not available:
        return "none"
    evidenced = sum(1 for item in available if item.evidence_ids)
    ratio = evidenced / len(available)
    families = {item.family for item in available}
    if ratio >= 0.95 and len(families) >= 5:
        return "high"
    if ratio >= 0.8 and len(families) >= 3:
        return "medium"
    return "low"


def build_brief(
    organisation_number: str,
    claims: list[Claim],
    *,
    refresh: dict[str, Any] | None = None,
    changes: list[dict[str, Any]] | None = None,
) -> Brief:
    """Assemble the brief. Sentences appear only when their claims exist.

    The order follows the questions a reader brings to a company before applying,
    selling, partnering or investing: what it is and does, who runs it, how big it is,
    how its filed numbers look, where to find it, whether it is hiring, what it has
    published lately, what changed, and what could not be established.
    """
    available = _available(claims)
    name = _first(claims, "legal_name")
    headline = str(name.value) if name else f"Organisation {organisation_number}"

    builders = (
        _identity_sentence(claims, organisation_number),
        _status_sentence(claims),
        _description_sentence(claims),
        _people_sentence(claims),
        _scale_sentence(claims),
        _financial_sentence(claims),
        _presence_sentence(claims),
        _contact_sentence(claims),
        _jobs_sentence(claims),
        _activity_sentence(claims),
        _changes_sentence(refresh, changes),
        _unknowns_sentence(claims),
    )
    sentences = [item for item in builders if item is not None]

    highlights: list[dict[str, Any]] = []
    for topic, label in (
        ("description", "What it does"),
        ("people", "Who runs it"),
        ("financials", "Latest accounts"),
        ("scale", "Scale"),
        ("web_presence", "Online"),
        ("jobs", "Hiring"),
        ("activity", "Recent activity"),
        ("changes", "Changes"),
    ):
        match = next((item for item in sentences if item.topic == topic), None)
        if match:
            highlights.append({"label": label, "text": match.text, "claim_keys": match.claim_keys})

    return Brief(
        organisation_number=organisation_number,
        headline=headline,
        sentences=sentences,
        highlights=highlights,
        gaps=_gaps(claims),
        confidence=_confidence(claims, available),
    )


def render_llm_context(brief: Brief, claims: list[Claim], *, max_claims: int = 60) -> dict[str, Any]:
    """Payload for the optional paid rephrasing path.

    The model receives only claim values that already passed validation, and is asked
    to rephrase — never to add. Any sentence it returns that is not covered by these
    claim keys is discarded by the caller, so the audit trail cannot be broken by the
    model's output.
    """
    facts = [
        {
            "claim_key": item.claim_key,
            "field": item.field,
            "family": item.family,
            "value": item.value,
            "period": item.reporting_period,
        }
        for item in _available(claims)[:max_claims]
    ]
    return {
        "instruction": (
            "Rephrase the supplied facts into a concise company brief. Use only these "
            "facts. Do not add figures, names, dates or claims that are not listed. "
            "Return one sentence per fact group with the claim_keys it uses."
        ),
        "organisation_number": brief.organisation_number,
        "deterministic_brief": brief.summary,
        "facts": facts,
        "allowed_claim_keys": [item["claim_key"] for item in facts],
    }
