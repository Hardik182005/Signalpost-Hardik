"""Website candidate discovery.

The registry's `hjemmeside` field is populated for only 10.9% of the eligible
universe, so for most companies a website has to be inferred. That inference is the
largest available lever on external coverage and simultaneously the largest risk to
entity precision, so the rule here is: **generate candidates broadly, spend HTTP
budget narrowly, and let the identity gate decide.**

Three mechanisms make that affordable and safe:

* **DNS pre-filtering.** A guessed domain that does not exist costs a DNS lookup, not
  an HTTP request. In the first live batch four of ten companies each spent a fetch
  only to learn the guessed host did not resolve; that spend is now avoided.
* **IDNA encoding.** Norwegian names carry æ/ø/å, and a company may have registered
  either the transliterated domain or the accented one. Both forms are generated, and
  the accented form is punycode-encoded before it ever reaches the network.
* **Ranked, capped attempts.** Candidates carry a prior describing how much
  independent evidence backs them; only the strongest few become HTTP requests.

Nothing in this module publishes anything, and the priors are **ordering hints
only** — they never feed the identity score. Discovery proposes; `IdentityResolver`
disposes. The addresses resolved here are likewise not passed on as trusted: the
fetcher re-validates and pins its own, so a DNS answer that changes between the two
cannot steer a request.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable

from .identity import (
    AS_SLASH_RE,
    TRANSLITERATE,
    GENERIC_TOKENS,
    LEADING_LEGAL_FORMS,
    LEGAL_SUFFIXES,
    CompanyIdentity,
)
from .net import canonical_url, registrable_domain, validate_url

# Free webmail and ISP domains: a company using one of these has told us nothing about
# its own web presence, and treating `gmail.com` as a candidate site would attach every
# such company to Google.
PUBLIC_MAILBOX_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "hotmail.com",
        "hotmail.no",
        "outlook.com",
        "outlook.no",
        "live.no",
        "live.com",
        "msn.com",
        "yahoo.com",
        "yahoo.no",
        "icloud.com",
        "me.com",
        "online.no",
        "broadpark.no",
        "start.no",
        "c2i.net",
        "getmail.no",
        "lyse.net",
        "frisurf.no",
        "protonmail.com",
        "proton.me",
    }
)

# Characters permitted in a hostname label before punycode encoding. Norwegian .no
# accepts the Nordic letters; anything outside this set means the name will not form a
# registrable domain and the candidate is dropped rather than mangled.
_LABEL_ALLOWED = re.compile(r"^[a-z0-9æøåäöüéèêàáâç](?:[a-z0-9æøåäöüéèêàáâç-]*[a-z0-9æøåäöüéèêàáâç])?$")

MIN_STEM_LENGTH = 4
MAX_STEM_LENGTH = 63

DEFAULT_MAX_CANDIDATES = 4


@dataclass(slots=True)
class Candidate:
    """One proposed website, with why it was proposed."""

    url: str
    source: str
    rationale: str
    prior: float = 0.0
    host: str = ""
    addresses: list[str] = field(default_factory=list)

    @property
    def from_registry(self) -> bool:
        return self.source == "registry"

    def to_dict(self) -> dict[str, object]:
        return {
            "url": self.url,
            "source": self.source,
            "rationale": self.rationale,
            "host": self.host,
        }


@dataclass(slots=True)
class DiscoveryResult:
    """Candidates worth fetching, plus the ones ruled out without spending a request."""

    candidates: list[Candidate] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    dns_lookups: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "considered": len(self.candidates) + len(self.skipped),
            "candidates": [item.to_dict() for item in self.candidates],
            "skipped": self.skipped[:12],
            "dns_lookups": self.dns_lookups,
        }


def name_tokens_for_domain(value: str, *, ascii_only: bool) -> list[str]:
    """Company-name tokens in the form they would appear in a domain.

    Two variants are needed. The ASCII variant transliterates æ/ø/å the way
    `normalise_name` does, because most Norwegian companies register the plain-letter
    domain. The accented variant keeps them, because a sizeable minority register the
    IDN instead. NFKD is applied only to the ASCII variant: it would strip the ring
    from "å" and silently collapse the accented variant into the ASCII one.
    """
    text = str(value or "").casefold()
    if ascii_only:
        text = text.translate(TRANSLITERATE)
        text = unicodedata.normalize("NFKD", text)
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = AS_SLASH_RE.sub(" as ", text)
    text = re.sub(r"[^\w\s]", " ", text)
    tokens = [token for token in text.split() if token]
    while tokens and tokens[-1] in LEGAL_SUFFIXES:
        tokens.pop()
    if len(tokens) > 1 and tokens[0] in LEADING_LEGAL_FORMS:
        tokens.pop(0)
    return tokens


def to_ascii_host(host: str) -> str | None:
    """Punycode-encode a hostname, or return None if it cannot be encoded.

    A host is only ever sent to the network in this form. `https://schrødereiendom.no`
    failed DNS resolution in the first live batch because the raw Unicode host reached
    `getaddrinfo`; the correct request is for `xn--schrdereiendom-9ib.no`.
    """
    cleaned = (host or "").strip().strip(".").casefold()
    if not cleaned:
        return None
    if cleaned.isascii():
        return cleaned
    labels: list[str] = []
    for label in cleaned.split("."):
        if not label:
            return None
        try:
            labels.append(label.encode("idna").decode("ascii"))
        except (UnicodeError, UnicodeDecodeError):
            return None
    return ".".join(labels)


def _valid_stem(stem: str) -> bool:
    if not (MIN_STEM_LENGTH <= len(stem) <= MAX_STEM_LENGTH):
        return False
    if stem in GENERIC_TOKENS:
        return False
    if stem.isdigit():
        return False
    return bool(_LABEL_ALLOWED.match(stem))


def domain_stems(identity: CompanyIdentity) -> list[tuple[str, str, float]]:
    """Plausible domain stems as (stem, rationale, prior), strongest first.

    Ordering reflects how specific the stem is to this company. The full name is the
    safest guess; a single distinctive token is the loosest and is only offered when
    the name has a token that is genuinely distinguishing, because "eiendom.no" or
    "norge.no" belong to whoever registered them, not to this company.
    """
    proposals: list[tuple[str, str, float]] = []
    seen: set[str] = set()

    def offer(stem: str, rationale: str, prior: float) -> None:
        if not _valid_stem(stem) or stem in seen:
            return
        seen.add(stem)
        proposals.append((stem, rationale, prior))

    for ascii_only in (True, False):
        tokens = name_tokens_for_domain(identity.legal_name, ascii_only=ascii_only)
        if not tokens:
            continue
        label = "transliterated" if ascii_only else "accented"
        joined = "".join(tokens)
        offer(joined, f"{label} company name as a single label", 0.60 if ascii_only else 0.55)
        if len(tokens) > 1:
            offer(
                "-".join(tokens),
                f"{label} company name, hyphen-separated",
                0.50 if ascii_only else 0.45,
            )
        distinctive = [token for token in tokens if token not in GENERIC_TOKENS]
        if distinctive and distinctive != tokens:
            offer(
                "".join(distinctive),
                f"{label} name with generic words removed",
                0.45 if ascii_only else 0.40,
            )
        if len(distinctive) == 1 and len(distinctive[0]) >= 6:
            # A single token only earns a guess when it is long enough to be a real
            # name rather than a common word.
            offer(distinctive[0], f"{label} distinctive name token", 0.35)

    return proposals


def _mailbox_domain(address: str) -> str | None:
    host = (address or "").split("@")[-1].strip().casefold()
    if not host or "." not in host:
        return None
    if host in PUBLIC_MAILBOX_DOMAINS:
        return None
    return host


def propose(identity: CompanyIdentity) -> list[Candidate]:
    """Every candidate worth considering, ranked, before any network contact."""
    candidates: list[Candidate] = []
    seen_domains: set[str] = set()

    def add(url: str, source: str, rationale: str, prior: float) -> None:
        normalised = (url or "").strip()
        if not normalised:
            return
        if "://" not in normalised:
            normalised = f"https://{normalised}"
        # Structural safety only here; DNS is checked later and by the fetcher.
        verdict = validate_url(normalised, resolve=False)
        if not verdict.allowed:
            return
        ascii_host = to_ascii_host(verdict.host)
        if not ascii_host:
            return
        if ascii_host != verdict.host:
            normalised = normalised.replace(verdict.host, ascii_host, 1)
        domain = registrable_domain(normalised)
        if not domain or domain in seen_domains:
            return
        seen_domains.add(domain)
        candidates.append(
            Candidate(
                url=normalised,
                source=source,
                rationale=rationale,
                prior=prior,
                host=ascii_host,
            )
        )

    if identity.registry_website:
        add(
            identity.registry_website,
            "registry",
            "Enhetsregisteret lists this address as the company's website",
            1.0,
        )

    for address in sorted(identity.emails):
        host = _mailbox_domain(address)
        if host:
            add(
                f"https://{host}",
                "email_domain",
                "registered contact email uses this domain",
                0.80,
            )

    for stem, rationale, prior in domain_stems(identity):
        add(f"https://{stem}.no", "name_guess", f".no domain from the {rationale}", prior)

    # `.com` is offered only for the most specific stem: a loose token on a global TLD
    # is far more likely to reach an unrelated company.
    stems = domain_stems(identity)
    if stems:
        top_stem, rationale, _ = stems[0]
        add(f"https://{top_stem}.com", "name_guess", f".com domain from the {rationale}", 0.30)

    candidates.sort(key=lambda item: -item.prior)
    return candidates


def discover(
    identity: CompanyIdentity,
    *,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    resolve: bool = True,
) -> DiscoveryResult:
    """Rank candidates and drop the ones that cannot resolve, before spending a fetch.

    A registry-listed website is kept even when DNS fails, because the registry is an
    authoritative statement that the site exists; the failure is then reported against
    that URL rather than hiding it. Guessed domains that do not resolve are simply
    never tried.
    """
    result = DiscoveryResult()
    for candidate in propose(identity):
        if len(result.candidates) >= max_candidates:
            result.skipped.append(
                {
                    "url": candidate.url,
                    "reason": "candidate limit reached before this one was reached",
                }
            )
            continue

        if not resolve:
            result.candidates.append(candidate)
            continue

        verdict = validate_url(candidate.url, resolve=True)
        result.dns_lookups += 1
        if verdict.allowed:
            candidate.addresses = list(verdict.addresses)
            result.candidates.append(candidate)
            continue

        if candidate.from_registry:
            # Keep it: an unreachable registry-declared site is a finding, not a
            # candidate to discard silently.
            result.candidates.append(candidate)
            continue

        www = _with_www(candidate)
        if www is not None:
            www_verdict = validate_url(www.url, resolve=True)
            result.dns_lookups += 1
            if www_verdict.allowed:
                www.addresses = list(www_verdict.addresses)
                result.candidates.append(www)
                continue

        result.skipped.append(
            {
                "url": candidate.url,
                "reason": verdict.reason or "did not resolve",
            }
        )
    return result


def _with_www(candidate: Candidate) -> Candidate | None:
    """The `www.` form of a candidate, when the bare domain did not resolve."""
    if candidate.host.startswith("www."):
        return None
    host = f"www.{candidate.host}"
    return Candidate(
        url=candidate.url.replace(candidate.host, host, 1),
        source=candidate.source,
        rationale=f"{candidate.rationale} (www host)",
        prior=candidate.prior,
        host=host,
    )


def candidate_urls(identity: CompanyIdentity, **kwargs: object) -> list[tuple[str, bool]]:
    """Backwards-compatible view used by the website stage: (url, came_from_registry)."""
    outcome = discover(identity, **kwargs)  # type: ignore[arg-type]
    return [(item.url, item.from_registry) for item in outcome.candidates]


def describe(identity: CompanyIdentity, urls: Iterable[str]) -> list[str]:
    """Canonical forms of the given URLs, for logging and evidence."""
    return [canonical_url(url) for url in urls]
