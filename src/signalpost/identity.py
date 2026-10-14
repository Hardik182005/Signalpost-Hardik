"""Exact-entity resolution: the gate that decides what may be published.

External sources are only worth points if they are attached to the *right* company.
A single confident-but-wrong attribution is a material wrong-company publication, so
this module is deliberately conservative: a candidate is published only on strong
evidence, and anything short of that is recorded as `ambiguous` with its reasoning
kept, rather than guessed at.

The evidence hierarchy:

* **Decisive** — the organisation number appears on the page, or the page is the
  domain the registry itself lists. Either one identifies the company uniquely.
* **Supporting** — exact legal name, a registered address or postal code, a
  registered phone number, or the company name embedded in the domain.

Publication requires one decisive signal, or two independent supporting signals of
different kinds. Norwegian company names cluster heavily ("Nordic Consulting AS"),
so a name match alone never publishes.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable

from .net import canonical_url, registrable_domain, same_site

# Legal-form suffixes stripped when comparing names. "Bergen Bygg AS" and
# "Bergen Bygg" are the same company; the suffix carries no distinguishing power.
LEGAL_SUFFIXES = (
    "asa",
    "as",
    "ans",
    "da",
    "ba",
    "sa",
    "nuf",
    "enk",
    "kf",
    "if",
    "ks",
    "bbl",
    "brl",
    "fkf",
    "iks",
    "sf",
    "stiftelse",
    "avd",
)

# Generic words that make a name match weak on their own.
GENERIC_TOKENS = frozenset(
    {
        "as",
        "asa",
        "norge",
        "norway",
        "norsk",
        "norske",
        "group",
        "gruppen",
        "holding",
        "invest",
        "eiendom",
        "consulting",
        "consult",
        "service",
        "services",
        "solutions",
        "partner",
        "partners",
        "company",
        "the",
        "and",
        "og",
    }
)

LEADING_LEGAL_FORMS = frozenset({"as", "asa"})
AS_SLASH_RE = re.compile(r"(?<![\w])a\s*/\s*s(?![\w])")

DECISIVE = "decisive"
SUPPORTING = "supporting"

ORG_NUMBER_RE = re.compile(r"\b(\d{3})[\s. -]?(\d{3})[\s. -]?(\d{3})\b")


# Letters NFKD does not decompose, so they need an explicit mapping. "Å" decomposes
# to A + combining ring and loses it for free; "Ø" and "Æ" are indivisible letters and
# do not. The registry publishes names in upper case while websites write them in
# mixed case, so this has to run *after* casefolding or "SCHRØDER" and "Schrøder"
# normalise differently and the identity gate rejects a company's real website.
TRANSLITERATE = str.maketrans(
    {"ø": "o", "æ": "ae", "å": "a", "ð": "d", "þ": "th", "đ": "d", "ł": "l"}
)


def normalise_name(value: str) -> str:
    """Casefold, transliterate, strip accents, punctuation and legal-form suffixes."""
    text = str(value or "").casefold().translate(TRANSLITERATE)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    # "A/S" is the older spelling of "AS" and would otherwise split into two stray
    # single letters that survive suffix stripping.
    text = AS_SLASH_RE.sub(" as ", text)
    text = re.sub(r"[^\w\s]", " ", text)
    tokens = [token for token in text.split() if token]
    while tokens and tokens[-1] in LEGAL_SUFFIXES:
        tokens.pop()
    # Norwegian names also carry the form up front ("AS TROMSØ AUTOMOBILCOMPANI").
    # Only "as"/"asa" are stripped here, and only one: "da", "ba" and "sa" are real
    # words that begin real company names, and dropping them would change identity.
    if len(tokens) > 1 and tokens[0] in LEADING_LEGAL_FORMS:
        tokens.pop(0)
    return " ".join(tokens)


def name_tokens(value: str) -> set[str]:
    return {token for token in normalise_name(value).split() if len(token) > 2}


def distinctive_tokens(value: str) -> set[str]:
    """Tokens that actually distinguish this company from others."""
    return {token for token in name_tokens(value) if token not in GENERIC_TOKENS}


def valid_org_number(value: str) -> bool:
    """Validate a Norwegian organisation number by its MOD-11 check digit.

    This matters because a nine-digit run in page text is often a phone number, an
    invoice reference or a postal code pair. Checking the control digit removes most
    of those false positives before they can cause a wrong-company match.
    """
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) != 9:
        return False
    weights = (3, 2, 7, 6, 5, 4, 3, 2)
    total = sum(int(digit) * weight for digit, weight in zip(digits[:8], weights))
    remainder = total % 11
    check = 0 if remainder == 0 else 11 - remainder
    return check < 10 and check == int(digits[8])


def find_org_numbers(text: str) -> set[str]:
    """Extract valid organisation numbers from free text, in any common spacing."""
    found: set[str] = set()
    for match in ORG_NUMBER_RE.finditer(text or ""):
        candidate = "".join(match.groups())
        if valid_org_number(candidate):
            found.add(candidate)
    return found


def normalise_phone(value: str) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if digits.startswith("0047"):
        digits = digits[4:]
    elif digits.startswith("47") and len(digits) == 10:
        digits = digits[2:]
    return digits


@dataclass(slots=True)
class IdentitySignal:
    """One reason to believe (or doubt) that a source belongs to a company."""

    name: str
    strength: str
    detail: str
    weight: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal": self.name,
            "strength": self.strength,
            "detail": self.detail[:300],
            "weight": self.weight,
        }


@dataclass(slots=True)
class IdentityVerdict:
    """Publication decision for one candidate source."""

    candidate: str
    decision: str  # published | ambiguous | rejected
    confidence: float
    signals: list[IdentitySignal] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    reason: str = ""

    @property
    def publishable(self) -> bool:
        return self.decision == "published"

    @property
    def availability(self) -> str:
        """Availability state to use for claims derived from this candidate."""
        if self.decision == "published":
            return "available"
        if self.decision == "ambiguous":
            return "ambiguous"
        return "not_available"

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate,
            "decision": self.decision,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "signals": [signal.to_dict() for signal in self.signals],
            "conflicts": self.conflicts[:10],
        }


@dataclass(slots=True)
class CompanyIdentity:
    """The registry-anchored facts an external source is checked against."""

    organisation_number: str
    legal_name: str
    former_names: list[str] = field(default_factory=list)
    registry_website: str | None = None
    postal_codes: set[str] = field(default_factory=set)
    cities: set[str] = field(default_factory=set)
    street_addresses: set[str] = field(default_factory=set)
    phones: set[str] = field(default_factory=set)
    emails: set[str] = field(default_factory=set)
    subunit_numbers: set[str] = field(default_factory=set)

    @property
    def all_names(self) -> list[str]:
        return [self.legal_name, *self.former_names]

    @property
    def own_numbers(self) -> set[str]:
        return {self.organisation_number, *self.subunit_numbers}

    @classmethod
    def from_claims(cls, organisation_number: str, claims: Iterable[Any]) -> "CompanyIdentity":
        identity = cls(organisation_number=organisation_number, legal_name="")
        for claim in claims:
            if claim.availability != "available":
                continue
            value = claim.value
            if claim.field == "legal_name":
                identity.legal_name = str(value)
            elif claim.field == "former_name" and isinstance(value, dict):
                name = value.get("name")
                if name:
                    identity.former_names.append(str(name))
            elif claim.field == "registry_website":
                identity.registry_website = str(value)
            elif claim.field == "phone":
                identity.phones.add(normalise_phone(value))
            elif claim.field == "email" and value:
                identity.emails.add(str(value).strip().lower())
            elif claim.field == "address" and isinstance(value, dict):
                if value.get("postal_code"):
                    identity.postal_codes.add(str(value["postal_code"]).strip())
                if value.get("city"):
                    identity.cities.add(str(value["city"]).strip().casefold())
                if value.get("street"):
                    identity.street_addresses.add(normalise_name(value["street"]))
            elif claim.field == "subunit" and isinstance(value, dict):
                number = value.get("organisation_number")
                if number:
                    identity.subunit_numbers.add(str(number))
        identity.phones.discard("")
        return identity


class IdentityResolver:
    """Scores candidate sources against a registry-anchored identity."""

    # A decisive signal alone publishes; supporting signals must pair up.
    PUBLISH_THRESHOLD = 1.0
    AMBIGUOUS_THRESHOLD = 0.45

    SUPPORTING_WEIGHTS = {
        "exact_legal_name": 0.45,
        "former_name": 0.35,
        "distinctive_name_in_domain": 0.40,
        "postal_code": 0.30,
        "city": 0.15,
        "street_address": 0.35,
        "phone": 0.40,
        "email_domain": 0.35,
        "name_in_page_title": 0.25,
    }

    def __init__(self, identity: CompanyIdentity) -> None:
        self.identity = identity
        self._distinctive = distinctive_tokens(identity.legal_name)

    # -- website --------------------------------------------------------------

    def assess_website(
        self,
        url: str,
        *,
        page_text: str = "",
        page_title: str = "",
        emails: Iterable[str] = (),
        phones: Iterable[str] = (),
        from_registry: bool = False,
    ) -> IdentityVerdict:
        """Decide whether a website belongs to this company."""
        signals: list[IdentitySignal] = []
        conflicts: list[str] = []
        score = 0.0
        domain = registrable_domain(url)
        text = page_text or ""

        if from_registry or (
            self.identity.registry_website and same_site(url, self._registry_url())
        ):
            signals.append(
                IdentitySignal(
                    "registry_website",
                    DECISIVE,
                    f"Registry lists {domain} as this company's website",
                    1.0,
                )
            )
            score += 1.0

        found_numbers = find_org_numbers(text)
        if self.identity.organisation_number in found_numbers:
            signals.append(
                IdentitySignal(
                    "organisation_number_on_page",
                    DECISIVE,
                    f"Page states organisation number {self.identity.organisation_number}",
                    1.0,
                )
            )
            score += 1.0
        else:
            foreign = found_numbers - self.identity.own_numbers
            if foreign and not found_numbers & self.identity.own_numbers:
                # The page identifies itself as a *different* legal entity. That is a
                # strong reason not to publish, not merely a missing signal.
                conflicts.append(
                    f"page states other organisation number(s): {', '.join(sorted(foreign)[:3])}"
                )
                score -= 0.6

        lowered_text = text.casefold()
        normalised_text = normalise_name(text[:200_000])
        for name in self.identity.all_names:
            normalised = normalise_name(name)
            if not normalised:
                continue
            if normalised and normalised in normalised_text:
                is_current = name == self.identity.legal_name
                key = "exact_legal_name" if is_current else "former_name"
                weight = self.SUPPORTING_WEIGHTS[key]
                signals.append(
                    IdentitySignal(key, SUPPORTING, f"Page text contains {name!r}", weight)
                )
                score += weight
                break

        if page_title and self._distinctive:
            title_tokens = distinctive_tokens(page_title)
            if self._distinctive & title_tokens:
                weight = self.SUPPORTING_WEIGHTS["name_in_page_title"]
                signals.append(
                    IdentitySignal(
                        "name_in_page_title", SUPPORTING, f"Title matches: {page_title[:120]}", weight
                    )
                )
                score += weight

        if domain and self._distinctive:
            domain_stem = re.sub(r"[^a-z0-9]", "", domain.split(".")[0])
            if any(token in domain_stem for token in self._distinctive if len(token) > 3):
                weight = self.SUPPORTING_WEIGHTS["distinctive_name_in_domain"]
                signals.append(
                    IdentitySignal(
                        "distinctive_name_in_domain",
                        SUPPORTING,
                        f"Domain {domain} contains a distinctive name token",
                        weight,
                    )
                )
                score += weight

        for code in self.identity.postal_codes:
            if code and re.search(rf"\b{re.escape(code)}\b", text):
                weight = self.SUPPORTING_WEIGHTS["postal_code"]
                signals.append(
                    IdentitySignal("postal_code", SUPPORTING, f"Page contains postal code {code}", weight)
                )
                score += weight
                break

        for street in self.identity.street_addresses:
            if street and len(street) > 6 and street in normalised_text:
                weight = self.SUPPORTING_WEIGHTS["street_address"]
                signals.append(
                    IdentitySignal(
                        "street_address", SUPPORTING, f"Page contains registered address {street!r}", weight
                    )
                )
                score += weight
                break

        page_phones = {normalise_phone(item) for item in phones}
        page_phones |= {normalise_phone(item) for item in re.findall(r"\+?[\d][\d\s.()-]{6,}", text[:50_000])}
        if self.identity.phones & {phone for phone in page_phones if len(phone) >= 8}:
            weight = self.SUPPORTING_WEIGHTS["phone"]
            signals.append(
                IdentitySignal("phone", SUPPORTING, "Page lists the registered phone number", weight)
            )
            score += weight

        for address in emails:
            host = str(address).split("@")[-1].strip().lower()
            if host and domain and registrable_domain(host) == domain:
                weight = self.SUPPORTING_WEIGHTS["email_domain"]
                signals.append(
                    IdentitySignal(
                        "email_domain", SUPPORTING, f"Contact email is on {domain}", weight
                    )
                )
                score += weight
                break

        if not lowered_text.strip():
            conflicts.append("no readable page text to verify identity")

        return self._decide(canonical_url(url), score, signals, conflicts)

    # -- social and directory profiles ---------------------------------------

    def assess_profile(
        self,
        url: str,
        *,
        linked_from_site: bool = False,
        profile_name: str = "",
        profile_text: str = "",
        website_domain: str | None = None,
    ) -> IdentityVerdict:
        """Decide whether an owned profile (social, directory) belongs to the company.

        A profile linked from the company's own verified website is decisive: the
        company itself asserted ownership.
        """
        signals: list[IdentitySignal] = []
        conflicts: list[str] = []
        score = 0.0

        if linked_from_site:
            signals.append(
                IdentitySignal(
                    "linked_from_verified_site",
                    DECISIVE,
                    "Linked from the company's verified website",
                    1.0,
                )
            )
            score += 1.0

        combined = f"{profile_name}\n{profile_text}"
        if self.identity.organisation_number in find_org_numbers(combined):
            signals.append(
                IdentitySignal(
                    "organisation_number_on_profile",
                    DECISIVE,
                    "Profile states the organisation number",
                    1.0,
                )
            )
            score += 1.0

        if profile_name:
            normalised_profile = normalise_name(profile_name)
            if normalised_profile and normalised_profile == normalise_name(self.identity.legal_name):
                weight = self.SUPPORTING_WEIGHTS["exact_legal_name"]
                signals.append(
                    IdentitySignal(
                        "exact_legal_name", SUPPORTING, f"Profile name is {profile_name!r}", weight
                    )
                )
                score += weight
            elif self._distinctive and self._distinctive <= distinctive_tokens(profile_name):
                weight = 0.30
                signals.append(
                    IdentitySignal(
                        "name_tokens_match", SUPPORTING, f"Profile name {profile_name!r}", weight
                    )
                )
                score += weight

        if website_domain and profile_text:
            if website_domain.lower() in profile_text.lower():
                weight = 0.40
                signals.append(
                    IdentitySignal(
                        "profile_links_website",
                        SUPPORTING,
                        f"Profile references {website_domain}",
                        weight,
                    )
                )
                score += weight

        return self._decide(canonical_url(url), score, signals, conflicts)

    # -- generic mention (news, job boards) -----------------------------------

    def assess_mention(
        self,
        url: str,
        *,
        text: str,
        title: str = "",
        source_domain: str | None = None,
        company_domain: str | None = None,
    ) -> IdentityVerdict:
        """Decide whether a third-party mention is about this company.

        Third-party pages are the highest-risk attribution: many Norwegian companies
        share a name. Only an organisation number, or a same-domain link plus an exact
        name, is enough.
        """
        signals: list[IdentitySignal] = []
        conflicts: list[str] = []
        score = 0.0
        combined = f"{title}\n{text}"

        numbers = find_org_numbers(combined)
        if self.identity.organisation_number in numbers:
            signals.append(
                IdentitySignal(
                    "organisation_number_in_text", DECISIVE, "Text states the organisation number", 1.0
                )
            )
            score += 1.0
        elif numbers - self.identity.own_numbers:
            conflicts.append("text names a different organisation number")
            score -= 0.6

        normalised = normalise_name(self.identity.legal_name)
        if normalised and normalised in normalise_name(combined):
            weight = self.SUPPORTING_WEIGHTS["exact_legal_name"]
            signals.append(
                IdentitySignal("exact_legal_name", SUPPORTING, "Exact legal name appears", weight)
            )
            score += weight

        if company_domain and company_domain.lower() in combined.lower():
            weight = 0.45
            signals.append(
                IdentitySignal(
                    "links_company_domain", SUPPORTING, f"Mentions {company_domain}", weight
                )
            )
            score += weight

        for code in self.identity.postal_codes:
            if code and re.search(rf"\b{re.escape(code)}\b", combined):
                weight = self.SUPPORTING_WEIGHTS["postal_code"]
                signals.append(IdentitySignal("postal_code", SUPPORTING, f"Postal code {code}", weight))
                score += weight
                break

        if not self._distinctive:
            conflicts.append("company name has no distinctive tokens; name matching is unreliable")

        return self._decide(canonical_url(url), score, signals, conflicts)

    # -- internals ------------------------------------------------------------

    def _registry_url(self) -> str:
        raw = (self.identity.registry_website or "").strip()
        if raw and "://" not in raw:
            raw = f"https://{raw}"
        return raw

    def _decide(
        self,
        candidate: str,
        score: float,
        signals: list[IdentitySignal],
        conflicts: list[str],
    ) -> IdentityVerdict:
        decisive = [item for item in signals if item.strength == DECISIVE]
        supporting = {item.name for item in signals if item.strength == SUPPORTING}

        if conflicts and any("other organisation number" in item or "different organisation" in item for item in conflicts):
            # The source self-identifies as another entity: never publish.
            return IdentityVerdict(
                candidate=candidate,
                decision="rejected",
                confidence=max(0.0, min(score, 0.99)),
                signals=signals,
                conflicts=conflicts,
                reason="source identifies a different legal entity",
            )

        if decisive:
            return IdentityVerdict(
                candidate=candidate,
                decision="published",
                confidence=min(0.99, 0.90 + 0.03 * len(signals)),
                signals=signals,
                conflicts=conflicts,
                reason=f"decisive signal: {decisive[0].name}",
            )

        if len(supporting) >= 2 and score >= self.PUBLISH_THRESHOLD:
            return IdentityVerdict(
                candidate=candidate,
                decision="published",
                confidence=min(0.95, 0.55 + score / 4),
                signals=signals,
                conflicts=conflicts,
                reason=f"{len(supporting)} independent supporting signals ({score:.2f})",
            )

        if score >= self.AMBIGUOUS_THRESHOLD:
            return IdentityVerdict(
                candidate=candidate,
                decision="ambiguous",
                confidence=min(0.6, score / 2),
                signals=signals,
                conflicts=conflicts,
                reason="evidence suggests a match but is not sufficient to publish",
            )

        return IdentityVerdict(
            candidate=candidate,
            decision="rejected",
            confidence=max(0.0, score / 2),
            signals=signals,
            conflicts=conflicts,
            reason="insufficient identity evidence",
        )


def choose_best(verdicts: Iterable[IdentityVerdict]) -> IdentityVerdict | None:
    """Pick the single best candidate, refusing to choose between equals.

    If two candidates are published with near-identical confidence they are treated
    as ambiguous rather than resolved by arbitrary ordering.
    """
    published = sorted(
        (item for item in verdicts if item.publishable), key=lambda item: -item.confidence
    )
    if not published:
        return None
    if len(published) > 1 and abs(published[0].confidence - published[1].confidence) < 0.02:
        contested = published[0]
        return IdentityVerdict(
            candidate=contested.candidate,
            decision="ambiguous",
            confidence=contested.confidence,
            signals=contested.signals,
            conflicts=[f"tied with {published[1].candidate}"],
            reason="multiple candidates matched with indistinguishable confidence",
        )
    return published[0]
