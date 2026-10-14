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

EMPLOYEE_BANDS = (
    (0, "no registered employees"),
    (1, "1-9 employees"),
    (10, "10-49 employees"),
    (50, "50-249 employees"),
    (250, "250-999 employees"),
    (1000, "1,000+ employees"),
)


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


def _employee_band(count: int) -> str:
    label = EMPLOYEE_BANDS[0][1]
    for floor, text in EMPLOYEE_BANDS:
        if count >= floor:
            label = text
    return label


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


def _identity_sentence(claims: list[Claim], org: str) -> Sentence | None:
    name = _first(claims, "legal_name")
    if name is None:
        return None
    keys = [name.claim_key]
    parts = [f"{name.value} (organisation number {org})"]

    form = _first(claims, "legal_form_code")
    if form is not None:
        keys.append(form.claim_key)
        code = str(form.value).upper()
        parts.append(f"is a {LEGAL_FORMS.get(code, f'{code} entity')}")
    else:
        parts.append("is a registered Norwegian entity")

    registered = _first(claims, "registration_date")
    if registered is not None:
        keys.append(registered.claim_key)
        parts.append(f"registered on {registered.value}")

    label = _first(claims, "industry_label")
    code = _first(claims, "industry_code")
    if label is not None:
        keys.append(label.claim_key)
        suffix = ""
        if code is not None:
            keys.append(code.claim_key)
            suffix = f" (NACE {code.value})"
        parts.append(f"operating in {str(label.value).lower()}{suffix}")
    elif code is not None:
        keys.append(code.claim_key)
        parts.append(f"registered under NACE industry code {code.value}")

    return Sentence(" ".join(parts).rstrip(".") + ".", keys, "identity")


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
        f"The registry records the company as {', and '.join(flags)}.", keys, "status"
    )


def _scale_sentence(claims: list[Claim]) -> Sentence | None:
    employees = _first(claims, "employees")
    if employees is None:
        return None
    try:
        count = int(employees.value)
    except (TypeError, ValueError):
        return None
    period = f" as at {employees.effective_date}" if employees.effective_date else ""
    return Sentence(
        f"It reports {count:,} registered employees{period} ({_employee_band(count)}).",
        [employees.claim_key],
        "scale",
    )


def _financial_sentence(claims: list[Claim]) -> Sentence | None:
    revenue = sorted(
        _by_field(claims, "revenue"), key=lambda item: item.reporting_period or "", reverse=True
    )
    if not revenue:
        return None
    latest = revenue[0]
    keys = [latest.claim_key]
    period = latest.reporting_period or "the latest filed year"
    text = f"Filed accounts for {period} report revenue of {_format_money(latest.value, latest.currency)}"

    profit = next(
        (
            item
            for item in sorted(
                _by_field(claims, "profit_before_tax") + _by_field(claims, "net_profit"),
                key=lambda item: item.reporting_period or "",
                reverse=True,
            )
            if item.reporting_period == latest.reporting_period
        ),
        None,
    )
    if profit is not None:
        keys.append(profit.claim_key)
        label = "pre-tax profit" if profit.field == "profit_before_tax" else "net profit"
        text += f" and {label} of {_format_money(profit.value, profit.currency)}"

    # A second year is only mentioned when it exists; no trend is invented from one point.
    if len(revenue) > 1 and revenue[1].value:
        older = revenue[1]
        try:
            change = (float(latest.value) - float(older.value)) / abs(float(older.value)) * 100
        except (TypeError, ValueError, ZeroDivisionError):
            change = None
        if change is not None and abs(change) >= 1:
            keys.append(older.claim_key)
            direction = "up" if change > 0 else "down"
            text += f", {direction} {abs(change):.0f}% on {older.reporting_period}"
    return Sentence(text + ".", keys, "financials")


def _description_sentence(claims: list[Claim]) -> Sentence | None:
    """The company's own words, quoted rather than paraphrased."""
    described = _by_field(claims, "business_description")
    if not described:
        return None
    best = max(described, key=lambda item: len(str(item.value or "")))
    text = " ".join(str(best.value).split())
    if len(text) > 320:
        text = text[:317].rsplit(" ", 1)[0] + "…"
    return Sentence(f'The company describes itself as: "{text}"', [best.claim_key], "description")


def _presence_sentence(claims: list[Claim]) -> Sentence | None:
    website = _first(claims, "website")
    socials = _by_field(claims, "social_profile")
    if website is None and not socials:
        return None
    keys: list[str] = []
    parts: list[str] = []
    if website is not None:
        keys.append(website.claim_key)
        parts.append(f"a website at {website.value}")
    if socials:
        keys.extend(item.claim_key for item in socials[:4])
        networks = sorted({str(item.value).split("/")[2].split(".")[-2] for item in socials if "/" in str(item.value)})
        if networks:
            parts.append(f"profiles on {', '.join(networks[:4])}")
        else:
            parts.append(f"{len(socials)} social profiles")
    return Sentence(f"Online presence: {' and '.join(parts)}.", keys, "web_presence")


def _contact_sentence(claims: list[Claim]) -> Sentence | None:
    emails = _by_field(claims, "email")
    phones = _by_field(claims, "phone")
    addresses = _by_field(claims, "address")
    if not (emails or phones or addresses):
        return None
    keys: list[str] = []
    parts: list[str] = []
    if addresses:
        keys.append(addresses[0].claim_key)
        value = addresses[0].value
        if isinstance(value, dict):
            line = ", ".join(
                str(value[part])
                for part in ("street", "postal_code", "city", "country")
                if value.get(part)
            )
        else:
            line = str(value)
        if line:
            parts.append(f"based at {line}")
    if emails:
        keys.append(emails[0].claim_key)
        parts.append(f"contactable at {emails[0].value}")
    if phones:
        keys.append(phones[0].claim_key)
        parts.append(f"on {phones[0].value}")
    return Sentence("It is " + " and ".join(parts) + ".", keys, "contact")


def _jobs_sentence(claims: list[Claim]) -> Sentence | None:
    jobs = _by_field(claims, "job_posting")
    if not jobs:
        return None
    titles = []
    for job in jobs[:3]:
        value = job.value
        title = value.get("title") if isinstance(value, dict) else value
        if title:
            titles.append(str(title))
    if not titles:
        return None
    listed = ", ".join(f'"{item}"' for item in titles)
    more = f" and {len(jobs) - len(titles)} more" if len(jobs) > len(titles) else ""
    return Sentence(
        f"{len(jobs)} open role{'s' if len(jobs) != 1 else ''} were listed, including {listed}{more}.",
        [item.claim_key for item in jobs[:3]],
        "jobs",
    )


def _activity_sentence(claims: list[Claim]) -> Sentence | None:
    items = _by_field(claims, "activity_item")
    if not items:
        return None
    newest = items[0]
    value = newest.value
    title = value.get("title") if isinstance(value, dict) else value
    date = value.get("date") if isinstance(value, dict) else None
    when = f" ({date})" if date else ""
    return Sentence(
        f"Most recent published activity: \"{str(title)[:160]}\"{when}.",
        [newest.claim_key],
        "activity",
    )


def _people_sentence(claims: list[Claim]) -> Sentence | None:
    roles = _by_field(claims, "role")
    if not roles:
        return None
    named: list[str] = []
    keys: list[str] = []
    for role in roles:
        value = role.value
        if not isinstance(value, dict):
            continue
        person = value.get("name")
        title = value.get("role_description") or value.get("role_code")
        if person and title:
            named.append(f"{person} ({str(title).lower()})")
            keys.append(role.claim_key)
        if len(named) >= 3:
            break
    if not named:
        return None
    return Sentence(f"Registered officers include {', '.join(named)}.", keys, "people")


def _gaps(claims: list[Claim]) -> list[str]:
    """What is missing, and why — stated rather than papered over."""
    gaps: list[str] = []
    seen: set[str] = set()
    for claim in claims:
        if claim.availability in ("available", "not_applicable"):
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


def build_brief(organisation_number: str, claims: list[Claim]) -> Brief:
    """Assemble the brief. Sentences appear only when their claims exist."""
    available = _available(claims)
    name = _first(claims, "legal_name")
    headline = str(name.value) if name else f"Organisation {organisation_number}"

    builders = (
        _identity_sentence(claims, organisation_number),
        _status_sentence(claims),
        _description_sentence(claims),
        _scale_sentence(claims),
        _financial_sentence(claims),
        _people_sentence(claims),
        _presence_sentence(claims),
        _contact_sentence(claims),
        _jobs_sentence(claims),
        _activity_sentence(claims),
    )
    sentences = [item for item in builders if item is not None]

    highlights: list[dict[str, Any]] = []
    for topic, label in (
        ("financials", "Latest revenue"),
        ("scale", "Employees"),
        ("web_presence", "Website"),
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
