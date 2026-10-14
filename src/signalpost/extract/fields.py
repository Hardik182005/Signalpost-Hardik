"""Field extractors: turn a retrieved page into candidate values.

These return raw candidates with locators. Nothing here decides what gets published —
that is the identity gate's job — so every function is free to be generous, and the
pipeline filters afterwards.

Norwegian specifics handled here:

* Phone numbers are 8 digits, often written "51 99 00 00" or "+47 51 99 00 00".
* Numbers use "." for thousands and "," for decimals, and figures are often stated in
  thousands ("Tall i hele 1 000"), which silently changes a value by 1000x if missed.
* Dates appear as "12.03.2025", "12. mars 2025" and ISO form.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable
import unicodedata
from urllib.parse import quote, unquote, urlsplit

from bs4 import BeautifulSoup, Tag

from ..net import canonical_url, registrable_domain
from . import html as H

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
NO_PHONE_RE = re.compile(r"(?:\+47[\s.-]?|0047[\s.-]?)?(?:\d[\s.-]?){7}\d")
POSTAL_RE = re.compile(r"\b(\d{4})\s+([A-ZÆØÅ][A-ZÆØÅa-zæøå\- ]{2,30})\b")

# Postcode -> post town, derived from the Brønnøysund bulk register (every business and
# postal address it holds). The regex alone published "0181 Oslo Norge Maksimer AS" and
# "6016 Powered by Salgsbetingelser" as addresses in the 1,000-company run: whatever
# capitalised words followed four digits became a "city". An address is now published
# only when the words after the code are that code's own post town.
_POSTAL_TABLE_PATH = Path(__file__).resolve().parents[1] / "data_postal_codes.json"
_POSTAL_TOWNS: dict[str, str] | None = None


def postal_town(code: str) -> str | None:
    """The registered post town for a Norwegian postcode, or None if the code is unknown."""
    global _POSTAL_TOWNS
    if _POSTAL_TOWNS is None:
        try:
            _POSTAL_TOWNS = json.loads(_POSTAL_TABLE_PATH.read_text(encoding="utf-8"))["postal_codes"]
        except (OSError, ValueError, KeyError):
            _POSTAL_TOWNS = {}
    return _POSTAL_TOWNS.get(code)

# Addresses that are shared infrastructure rather than a company contact.
GENERIC_EMAIL_HOSTS = frozenset(
    {"example.com", "sentry.io", "wixpress.com", "squarespace.com", "godaddy.com"}
)
GENERIC_LOCAL_PARTS = frozenset({"noreply", "no-reply", "donotreply", "postmaster", "webmaster"})

SOCIAL_HOSTS = {
    "linkedin.com": "linkedin",
    "facebook.com": "facebook",
    "instagram.com": "instagram",
    "x.com": "x",
    "twitter.com": "x",
    "youtube.com": "youtube",
    "tiktok.com": "tiktok",
    "github.com": "github",
    "vimeo.com": "vimeo",
    "pinterest.com": "pinterest",
    "snapchat.com": "snapchat",
}

# Link hints used to find the pages worth visiting, in Norwegian (bokmål/nynorsk) and
# English. Ordered by how reliably each page type carries the facts we need.
PAGE_HINTS: dict[str, tuple[str, ...]] = {
    "about": ("om oss", "om selskapet", "om bedriften", "about us", "about", "hvem vi er", "vår historie", "who we are"),
    "contact": ("kontakt", "kontakt oss", "contact", "contact us", "finn oss", "hvor er vi"),
    "careers": ("karriere", "jobb", "ledige stillinger", "stillinger", "bli med oss", "careers", "jobs", "vacancies", "work with us", "join us", "rekruttering"),
    "news": ("nyheter", "aktuelt", "nytt", "presse", "pressemeldinger", "blogg", "artikler", "news", "press", "blog", "insights", "updates"),
    "team": ("ansatte", "vårt team", "ledelse", "styret", "medarbeidere", "team", "people", "management", "leadership"),
    "services": ("tjenester", "produkter", "hva vi gjør", "løsninger", "services", "products", "solutions", "what we do"),
}

NO_MONTHS = {
    "januar": 1, "februar": 2, "mars": 3, "april": 4, "mai": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11, "desember": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "okt": 10, "nov": 11, "des": 12,
}

DATE_PATTERNS = (
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
    re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b"),
    re.compile(r"\b(\d{1,2})\.?\s+([a-zæøå]+)\s+(\d{4})\b", re.IGNORECASE),
)

THOUSANDS_HINTS = ("i hele 1 000", "i hele 1000", "tall i 1 000", "beløp i 1 000", "(1 000 kr)", "i tusen kroner", "nok 1 000")
MILLIONS_HINTS = ("i mill", "i millioner", "mill. kr", "millioner kroner", "nok mill")


@dataclass(slots=True)
class Candidate:
    """One extracted value with provenance inside the page."""

    field: str
    value: Any
    locator_kind: str
    locator_value: str
    excerpt: str | None = None
    method: str = "html"
    confidence: float = 0.6


def parse_norwegian_number(text: str, *, context: str = "") -> tuple[float | None, str | None]:
    """Parse a Norwegian-formatted figure and its stated scale.

    "1.234.567,89" is 1234567.89, not 1.234. A table headed "Tall i hele 1 000"
    multiplies every figure by 1,000. Getting either wrong publishes a number that is
    off by three orders of magnitude, so the detected scale is returned alongside the
    value rather than being applied silently.
    """
    raw = str(text or "").strip()
    if not raw:
        return None, None
    negative = raw.startswith("(") and raw.endswith(")") or raw.startswith("-")
    cleaned = re.sub(r"[^\d,.\-]", "", raw).lstrip("-")
    if not cleaned or not any(ch.isdigit() for ch in cleaned):
        return None, None

    if "," in cleaned and "." in cleaned:
        # Whichever separator appears last is the decimal mark.
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        parts = cleaned.split(",")
        cleaned = cleaned.replace(",", ".") if len(parts[-1]) != 3 else cleaned.replace(",", "")
    elif "." in cleaned:
        parts = cleaned.split(".")
        # "1.234" with a 3-digit tail is thousands, not a decimal.
        if len(parts) > 2 or (len(parts) == 2 and len(parts[-1]) == 3):
            cleaned = cleaned.replace(".", "")

    try:
        value = float(cleaned)
    except ValueError:
        return None, None
    if negative:
        value = -value

    lowered = f"{context} {raw}".casefold()
    scale = None
    if any(hint in lowered for hint in THOUSANDS_HINTS):
        scale = "thousands"
    elif any(hint in lowered for hint in MILLIONS_HINTS):
        scale = "millions"
    return value, scale


def parse_date(text: str, *, not_after: date | None = None) -> str | None:
    """Parse a Norwegian or ISO date into ISO form, rejecting implausible ones."""
    raw = str(text or "").strip()
    for pattern in DATE_PATTERNS:
        match = pattern.search(raw)
        if not match:
            continue
        groups = match.groups()
        try:
            if pattern.pattern.startswith(r"\b(\d{4})"):
                year, month, day = int(groups[0]), int(groups[1]), int(groups[2])
            elif groups[1].isdigit():
                day, month, year = int(groups[0]), int(groups[1]), int(groups[2])
            else:
                month = NO_MONTHS.get(groups[1].casefold())
                if not month:
                    continue
                day, year = int(groups[0]), int(groups[2])
            parsed = date(year, month, day)
        except (ValueError, IndexError):
            continue
        if parsed.year < 1900 or (not_after and parsed > not_after):
            continue
        return parsed.isoformat()
    return None


def extract_emails(soup: BeautifulSoup, text: str) -> list[Candidate]:
    """Contact emails, preferring explicit mailto: links."""
    found: dict[str, Candidate] = {}

    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"]).strip()
        if not href.lower().startswith("mailto:"):
            continue
        address = href[7:].split("?")[0].strip().lower()
        if not EMAIL_RE.fullmatch(address) or _generic_email(address):
            continue
        found.setdefault(
            address,
            Candidate(
                "email",
                address,
                "css_selector",
                H.css_path(anchor),
                excerpt=anchor.get_text(" ", strip=True)[:120] or address,
                confidence=0.85,
            ),
        )

    for match in EMAIL_RE.finditer(text[:120_000]):
        address = match.group(0).lower().rstrip(".")
        if _generic_email(address) or address in found:
            continue
        found[address] = Candidate(
            "email",
            address,
            "text_span",
            H.text_span(text, match.group(0)),
            excerpt=match.group(0),
            confidence=0.6,
        )
    return sorted(found.values(), key=_contact_rank)[:10]


# A company's main contact address is the one a person should actually use. Sites list
# many departmental mailboxes; ranking makes the general one the first published claim
# rather than whichever happened to appear first in the markup.
_PREFERRED_MAILBOXES = (
    "post", "kontakt", "firmapost", "contact", "info", "hello", "hei", "mail",
    "office", "salg", "sales", "support", "kundeservice", "service", "enquiries",
)


def _contact_rank(candidate: Candidate) -> tuple[int, int, str]:
    """Sort key: preferred mailbox name first, then higher confidence."""
    local = str(candidate.value).partition("@")[0].casefold()
    try:
        position = _PREFERRED_MAILBOXES.index(local)
    except ValueError:
        # Not a recognised general mailbox: rank after all of them, shortest first,
        # since a short local part is more likely to be a main address than a long one.
        position = len(_PREFERRED_MAILBOXES) + min(len(local), 40)
    return (position, -int(candidate.confidence * 100), local)


def _generic_email(address: str) -> bool:
    local, _, host = address.partition("@")
    if local in GENERIC_LOCAL_PARTS:
        return True
    return registrable_domain(host) in GENERIC_EMAIL_HOSTS


def extract_phones(soup: BeautifulSoup, text: str) -> list[Candidate]:
    """Norwegian phone numbers, preferring explicit tel: links."""
    found: dict[str, Candidate] = {}

    def add(raw: str, locator_kind: str, locator_value: str, excerpt: str, confidence: float) -> None:
        digits = re.sub(r"\D", "", raw)
        if digits.startswith("0047"):
            digits = digits[4:]
        elif digits.startswith("47") and len(digits) == 10:
            digits = digits[2:]
        if len(digits) != 8 or digits[0] not in "234589":
            return
        formatted = f"+47 {digits}"
        found.setdefault(
            digits,
            Candidate("phone", formatted, locator_kind, locator_value, excerpt=excerpt, confidence=confidence),
        )

    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"]).strip()
        if href.lower().startswith("tel:"):
            add(href[4:], "css_selector", H.css_path(anchor), anchor.get_text(" ", strip=True)[:80] or href, 0.85)

    for match in NO_PHONE_RE.finditer(text[:80_000]):
        add(match.group(0), "text_span", H.text_span(text, match.group(0)), match.group(0), 0.55)
    return list(found.values())[:6]


# Legal-form tokens that reveal a "city" match is really a company name, as in the
# copyright line "© 2026 Equinor ASA" which otherwise reads as postal code 2026.
_COMPANY_TAIL_RE = re.compile(
    r"(?:as|asa|ans|da|ba|sa|nuf|enk|ks|iks|kf|sf|bbl|brl|inc|ltd|gmbh|ab|oy|plc|llc)",
    re.IGNORECASE,
)
_COPYRIGHT_RE = re.compile(r"(?:©|&copy;|copyright|all rights reserved)", re.IGNORECASE)


def _phrase_starts_with(text: str, town: str) -> bool:
    """True when `text` begins with the words of `town`, ignoring case."""
    words = text.casefold().replace("-", " ").split()
    wanted = town.casefold().replace("-", " ").split()
    return bool(wanted) and words[: len(wanted)] == wanted


def extract_addresses(text: str) -> list[Candidate]:
    """Norwegian postal code + city pairs appearing in page text.

    Four-digit runs are ambiguous in Norwegian pages: a year in a copyright footer
    looks exactly like a postal code, and the company name that follows looks exactly
    like a city. Both are rejected below, because publishing "2026 Equinor ASA" as a
    postal address would be a fabricated location.
    """
    found: dict[str, Candidate] = {}
    body = text[:80_000]
    for match in POSTAL_RE.finditer(body):
        code, city = match.group(1), match.group(2).strip()
        if code in found:
            continue
        # Norwegian postal codes run 0001-9999; 0000 is not assigned.
        if not code.isdigit() or code == "0000":
            continue
        if _COMPANY_TAIL_RE.search(city):
            continue
        if _COPYRIGHT_RE.search(body[max(0, match.start() - 24) : match.start()]):
            continue
        # A plausible year immediately preceded by nothing address-like is far more
        # often a date than a postal code, so require supporting address context.
        if 1900 <= int(code) <= 2100:
            context = body[max(0, match.start() - 80) : match.start()].casefold()
            if not any(
                hint in context
                for hint in ("adresse", "besøksadresse", "postadresse", "postboks", "veien", "gate", "gata", "vei ", "address")
            ):
                continue
        if len(city) < 3 or any(ch.isdigit() for ch in city):
            continue
        town = postal_town(code)
        if town is None or not _phrase_starts_with(city, town):
            continue
        found[code] = Candidate(
            "postal_address",
            {"postal_code": code, "city": town},
            "text_span",
            H.text_span(body, match.group(0)),
            excerpt=match.group(0)[:120],
            confidence=0.6,
        )
    return list(found.values())[:6]


# Path segments that are features of a platform, never an account. Measured: a site
# linked `facebook.com/search/top?q=vinterbro+ishall`, which is a search results page and
# was published as the company's Facebook profile.
_NON_PROFILE_SEGMENTS = {
    "facebook": {"search", "sharer", "sharer.php", "share", "share.php", "dialog", "plugins",
                 "hashtag", "watch", "events", "photo", "photo.php", "story.php",
                 "permalink.php", "profile.php", "groups", "login", "help", "policies", "tr"},
    "instagram": {"p", "reel", "reels", "tv", "explore", "stories", "accounts", "direct"},
    "x": {"intent", "share", "home", "search", "hashtag", "i", "login"},
    "tiktok": {"tag", "discover", "music", "video"},
    "pinterest": {"pin", "search", "ideas"},
    "github": {"orgs", "features", "topics", "search", "login"},
    "vimeo": {"channels", "groups", "categories", "search"},
    "snapchat": {"spotlight", "discover"},
}


def social_handle(url: str) -> tuple[str, str] | None:
    """The account a social URL names, as (profile URL, handle), or None if it names none.

    The profile URL is rebuilt from the handle alone, so query strings and admin
    suffixes never reach a published value. Measured: a site linked its LinkedIn page as
    `linkedin.com/company/gl%C3%B8r-iks/mycompany?viewAsMember=true`, which is the
    administrator view of the page, not its public address.

    A URL without a readable handle, such as `youtube.com/channel/UCBjqhuwKj3MfE3B6Hg2oA8Q`,
    is refused rather than guessed at: nothing in it can be checked against the company.
    """
    domain = registrable_domain(url)
    platform = SOCIAL_HOSTS.get(domain)
    if not platform:
        return None
    parts = urlsplit(url)
    segments = [unquote(item) for item in parts.path.split("/") if item]
    if not segments:
        return None
    first = segments[0].casefold()
    prefix: list[str] = []
    if platform == "linkedin":
        if first not in ("company", "school", "showcase") or len(segments) < 2:
            return None
        prefix, handle = [first], segments[1]
    elif platform == "youtube":
        if first.startswith("@"):
            handle = segments[0]
        elif first in ("c", "user") and len(segments) >= 2:
            prefix, handle = [first], segments[1]
        else:
            return None
    elif platform == "facebook" and first in ("pages", "p") and len(segments) >= 2:
        prefix, handle = [first], segments[1]
    else:
        if first in _NON_PROFILE_SEGMENTS.get(platform, ()):
            return None
        handle = segments[0]
    if not handle.strip("@_.-"):
        return None
    path = "/".join([*prefix, quote(handle, safe="@._-")])
    profile = canonical_url(f"https://{parts.hostname}/{path}")
    return profile, handle


def handle_text(handle: str) -> str:
    """A handle reduced to comparable letters: `gullbring_trening` becomes `gullbringtrening`."""
    from ..identity import TRANSLITERATE

    text = unquote(handle).casefold().translate(TRANSLITERATE)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", text)


def extract_social_profiles(soup: BeautifulSoup, base_url: str) -> list[Candidate]:
    """Owned social profiles linked from the page.

    Only profile-shaped URLs count: a share button pointing at a post is not a company
    profile, and publishing one as the company's account would be wrong.
    """
    found: dict[str, Candidate] = {}
    for anchor in soup.find_all("a", href=True):
        url = str(anchor["href"]).strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        domain = registrable_domain(url)
        platform = SOCIAL_HOSTS.get(domain)
        if not platform:
            continue
        if urlsplit(url).query.startswith(("u=", "url=", "text=")):
            continue
        account = social_handle(url)
        if account is None:
            continue
        canonical, handle = account
        if canonical in found:
            continue
        found[canonical] = Candidate(
            "social_profile",
            {"platform": platform, "url": canonical, "handle": handle},
            "css_selector",
            H.css_path(anchor),
            excerpt=f"{platform}: {canonical}",
            confidence=0.75,
        )
    return list(found.values())[:12]


def classify_link(url: str, anchor_text: str) -> str | None:
    """Classify a link into the page type it most likely leads to."""
    haystack = f"{anchor_text} {urlsplit(url).path}".casefold()
    best: tuple[int, str] | None = None
    for kind, hints in PAGE_HINTS.items():
        for hint in hints:
            if hint in haystack:
                score = len(hint)
                if best is None or score > best[0]:
                    best = (score, kind)
    return best[1] if best else None


# Words that establish a page is genuinely about recruitment. Without one of these,
# a listing scan is reading navigation, not vacancies.
_CAREERS_CONTEXT_RE = re.compile(
    r"ledige stillinger|ledig stilling|vi søker|vi ansetter|jobb hos oss|"
    r"karriere|bli med i teamet|søk på stilling|rekruttering|"
    r"open position|job opening|vacanc|we are hiring|join our team|"
    r"career opportunit|apply now|current opening",
    re.IGNORECASE,
)

# Unicode private-use area: icon fonts (Material Icons and friends) render ligatures
# there, so scraped labels can carry characters that are meaningless as text.
_PRIVATE_USE_RE = re.compile(r"[\ue000-\uf8ff\U000f0000-\U000ffffd]")

# Chrome that appears in navigation, cookie banners and shop furniture. Text built
# mostly from these is site boilerplate, not a description of the business.
_BOILERPLATE_WORDS = (
    "handlekurv", "logg inn", "logg ut", "min side", "meny", "hjem",
    "personvern", "informasjonskapsler", "cookies", "vilkår", "nyhetsbrev",
    "kontakt oss", "les mer", "se alle", "til kassen", "kasse", "betaling",
    "shopping_cart", "add to cart", "checkout", "sign in", "log in", "my account",
    "privacy policy", "newsletter", "read more", "view all", "menu", "search",
    "accept all", "godta alle", "skip to content", "hopp til innhold",
)


# Registrar parking pages and default server pages. A guessed domain that resolves is
# very often one of these, and calling that "insufficient identity evidence" is true
# but uninformative: the useful finding is that the domain exists and hosts nothing.
# Strong markers name the host or parking product and are conclusive on their own.
_PARKED_STRONG = (
    "parkert hos",
    "domenet er parkert",
    "dette domenet er til salgs",
    "domain is parked",
    "this domain is parked",
    "domain for sale",
    "this domain is for sale",
    "buy this domain",
    "webhosting made simple",
    "welcome to nginx",
    "apache2 ubuntu default page",
    "apache2 debian default page",
    "default web site page",
    "iis windows server",
    "future home of something quite cool",
    "domeneshop",
    "sedoparking",
    "godaddy standard parking",
)

# Weak markers are things a live site may legitimately say, so they only count when
# the page has almost no other content.
_PARKED_WEAK = (
    "under construction",
    "under konstruksjon",
    "under oppbygging",
    "coming soon",
    "kommer snart",
    "siden er under arbeid",
    "website is under construction",
    "no website configured",
    "site not published",
)

_PARKED_WEAK_MAX_CHARS = 600


def looks_parked(title: str, text: str) -> str | None:
    """The marker proving this page is a parking or placeholder page, else None.

    Returning the matched marker rather than a bare boolean keeps the rejection
    auditable: a reviewer can see exactly which phrase decided it.
    """
    haystack = f"{clean_text(title)} {clean_text(text)}".casefold()
    for marker in _PARKED_STRONG:
        if marker in haystack:
            return marker
    body = clean_text(text)
    if len(body) <= _PARKED_WEAK_MAX_CHARS:
        for marker in _PARKED_WEAK:
            if marker in haystack:
                return marker
    return None


def clean_text(value: str) -> str:
    """Strip icon-font artefacts and collapse whitespace in scraped text."""
    return re.sub(r"\s+", " ", _PRIVATE_USE_RE.sub(" ", value or "")).strip()


def looks_like_boilerplate(text: str) -> bool:
    """True when a block reads as site chrome rather than prose about the company.

    Webshops in particular expose their whole navigation menu as the first substantial
    text block on the page. Publishing that as the company's self-description would be
    a fabricated claim, so it is rejected here rather than filtered downstream.
    """
    cleaned = clean_text(text)
    if len(cleaned) < 40:
        return True
    lowered = cleaned.casefold()
    if sum(1 for word in _BOILERPLATE_WORDS if word in lowered) >= 2:
        return True
    words = cleaned.split()
    if not words:
        return True
    # Prose has sentences; a menu is a long run of fragments with no sentence marks.
    sentence_marks = sum(lowered.count(mark) for mark in (". ", "! ", "? "))
    if len(words) > 25 and sentence_marks == 0:
        return True
    short_caps = sum(1 for word in words if word[:1].isupper() and len(word) <= 12)
    if len(words) > 20 and short_caps / len(words) > 0.55 and sentence_marks <= 1:
        return True
    return False


def extract_description(
    soup: BeautifulSoup, blocks: Iterable[dict[str, Any]], html_text: str, url: str
) -> list[Candidate]:
    """The company's description of its own business.

    Ordered by trustworthiness: schema.org Organization description, then the meta
    description, then the first substantial paragraph of the about/landing page.
    """
    candidates: list[Candidate] = []

    for block in H.of_type(blocks, "Organization", "Corporation", "LocalBusiness", "NGO"):
        description = clean_text(str(block.get("description") or ""))
        if len(description) > 40 and not looks_like_boilerplate(description):
            candidates.append(
                Candidate(
                    "business_description",
                    H.strip_injection_bait(description.strip())[:1200],
                    "json_pointer",
                    f"/{block.get('_syntax', 'json-ld')}/{block.get('_index', 0)}/description",
                    excerpt=description.strip()[:200],
                    method="structured_data",
                    confidence=0.9,
                )
            )
            break

    meta = H.meta_description(soup)
    if meta and not looks_like_boilerplate(str(meta.value)):
        candidates.append(
            Candidate(
                "business_description",
                H.strip_injection_bait(str(meta.value))[:1200],
                meta.locator_kind,
                meta.locator_value,
                excerpt=str(meta.value)[:200],
                method="meta",
                confidence=0.7,
            )
        )

    body = H.main_text(html_text, url)
    paragraphs = [
        clean_text(item)
        for item in body.split("\n")
        if len(item.strip()) > 120 and not looks_like_boilerplate(item)
    ]
    for paragraph in paragraphs[:1]:
        candidates.append(
            Candidate(
                "business_description",
                H.strip_injection_bait(paragraph)[:1200],
                "text_span",
                H.text_span(body, paragraph[:60]) or "0:0",
                excerpt=paragraph[:200],
                method="main_text",
                confidence=0.55,
            )
        )
    return candidates


def extract_jobs(
    soup: BeautifulSoup,
    blocks: Iterable[dict[str, Any]],
    base_url: str,
    text: str,
    *,
    careers_page: bool = True,
) -> list[Candidate]:
    """Open positions, from JobPosting structured data or a careers listing.

    Structured `JobPosting` data is read on any page. Listing headings and the bare
    hiring signal are read only on a careers page: on a landing page a "Ledige
    stillinger" menu link and a staff carousel both read as vacancies, and in the
    1,000-company run they made up most of the published postings.
    """
    found: dict[str, Candidate] = {}

    for block in H.of_type(blocks, "JobPosting"):
        title = block.get("title") or block.get("name")
        if not isinstance(title, str) or not title.strip():
            continue
        title = title.strip()[:200]
        location = block.get("jobLocation")
        place = None
        if isinstance(location, dict):
            address = location.get("address")
            if isinstance(address, dict):
                place = address.get("addressLocality") or address.get("addressRegion")
            elif isinstance(address, str):
                place = address
        posted = block.get("datePosted")
        found.setdefault(
            title.casefold(),
            Candidate(
                "job_posting",
                {
                    "title": title,
                    "location": str(place)[:120] if place else None,
                    "posted_at": parse_date(str(posted)) if posted else None,
                    "employment_type": str(block.get("employmentType"))[:60]
                    if block.get("employmentType")
                    else None,
                    "url": canonical_url(str(block.get("url") or base_url)),
                },
                "json_pointer",
                f"/{block.get('_syntax', 'json-ld')}/{block.get('_index', 0)}",
                excerpt=title,
                method="structured_data",
                confidence=0.9,
            ),
        )

    # The listing fallback is only trustworthy where a careers context exists. On a
    # landing page it reads product navigation as vacancies, so it is gated.
    careers_context = careers_page and bool(_CAREERS_CONTEXT_RE.search(text or ""))
    if not found and careers_context:
        # Fall back to listing markup: headings and links inside a careers page.
        for node in soup.find_all(["h1", "h2", "h3", "h4", "a", "li"], limit=400):
            if not isinstance(node, Tag):
                continue
            label = clean_text(node.get_text(" ", strip=True))
            if not (6 < len(label) < 120):
                continue
            if not _looks_like_job_title(label):
                continue
            key = label.casefold()
            if key in found:
                continue
            href = node.get("href") if node.name == "a" else None
            found[key] = Candidate(
                "job_posting",
                {
                    "title": label,
                    "location": None,
                    "posted_at": None,
                    "employment_type": None,
                    "url": canonical_url(str(href)) if href and str(href).startswith("http") else canonical_url(base_url),
                },
                "css_selector",
                H.css_path(node),
                excerpt=label,
                method="listing_heading",
                confidence=0.5,
            )
            if len(found) >= 25:
                break

    # Statements that the company is recruiting now. "Ledige stillinger" and "open
    # positions" are not among them: on a careers page they are the section heading,
    # present whether or not anything is open.
    hiring_words = ("vi søker", "vi ansetter", "we are hiring", "we're hiring")
    lowered = text.casefold()
    negated = any(
        phrase in lowered
        for phrase in ("ingen ledige stillinger", "ingen ledige", "ingen stillinger", "no open positions", "no vacancies", "no current openings")
    )
    if not found and careers_page and not negated and any(word in lowered for word in hiring_words):
        phrase = next(word for word in hiring_words if word in lowered)
        found["__hiring__"] = Candidate(
            "hiring_signal",
            True,
            "text_span",
            H.text_span(text, phrase),
            excerpt=phrase,
            method="text_signal",
            confidence=0.5,
        )
    return list(found.values())


# Role words are matched on word boundaries, never as substrings. "Bestselgere"
# (bestsellers) contains "selger"; a substring match turns a webshop's product
# navigation into fabricated job postings.
_ROLE_WORDS = (
    "utvikler", "utviklere", "ingeniør", "ingeniører", "konsulent", "konsulenter",
    "rådgiver", "rådgivere", "leder", "ledere", "sjef", "medarbeider", "medarbeidere",
    "tekniker", "teknikere", "selger", "selgere", "prosjektleder", "controller",
    "analytiker", "designer", "montør", "montører", "elektriker", "elektrikere",
    "snekker", "sykepleier", "lærer", "kokk", "sjåfør", "sjåfører", "stilling",
    "stillinger", "developer", "engineer", "manager", "director", "specialist",
    "consultant", "analyst", "advisor", "coordinator", "officer", "architect",
    "technician", "intern", "internship", "apprentice", "lærling",
    "tømrer", "tømrere", "rørlegger", "rørleggere", "maler", "frisør", "helsefagarbeider",
    "helsefagarbeidar", "sjukepleiar", "vernepleier", "vernepleiar", "rådgjevar",
    "assistent", "regnskapsfører", "økonom", "saksbehandler", "servitør", "mekaniker",
    "operatør", "renholder", "lagermedarbeider", "butikkmedarbeider", "fagarbeider",
    "bilmekaniker", "anleggsleder", "driftsleder", "daglig leder", "nurse", "accountant",
)
_ROLE_RE = re.compile(
    r"(?<![\wÀ-ɏ])(?:" + "|".join(_ROLE_WORDS) + r")(?![\wÀ-ɏ])",
    re.IGNORECASE,
)

# Navigation, commerce and section labels that can otherwise pass as a job title.
_NOT_A_JOB = (
    "les mer", "les hva", "les om", "se alle", "read more", "all ", "our ", "alle ",
    "vis alle", "flere", "bestselger", "tilbud", "handlekurv", "logg inn", "kontakt",
    "om oss", "meny", "nyhetsbrev", "personvern", "cookies", "informasjonskapsler",
    "vilkår", "sign in", "log in", "cart", "checkout", "shop", "newsletter", "privacy",
)

# The heading that introduces a vacancy list is not itself a vacancy. Matched whole,
# so "Lærling i Elvia" survives while the section label "Ledige stillinger" does not.
_SECTION_HEADINGS = frozenset(
    {
        "ledige stillinger", "ledig stilling", "våre ledige stillinger", "alle stillinger",
        "stillinger", "karriere", "jobb hos oss", "jobbmuligheter", "rekruttering",
        "open positions", "current openings", "vacancies", "job openings", "careers",
        "our vacancies", "all positions", "join us", "work with us", "jobs",
    }
)


# Calls to action, benefit lines and marketing copy that carry a role word. Every entry
# here was published as a vacancy in the 1,000-company run: "Se ledige stillinger",
# "Abonner på ledige stillinger", "Ingen ledige stillinger", "Fast månedslønn i 100%
# stilling + provisjon på salg", "Landets ledende rådgiver innen næringseiendom".
_NOT_A_JOB_PHRASES = (
    "ledige stillinger", "ledig stilling", "abonner", "ingen ", "se ", "bli ", "søk her",
    "åpen søknad", "open application", "lønn", "provisjon", "landets", "norges ",
    "ledende", "vi er ", "we are ", "coverage", "consensus", "interview", "->", "»",
)

# "Kevin Strandenes Chief Software Architect": a staff listing, not a vacancy. A label
# opening with two capitalised words that are not title vocabulary opens with a name.
_TITLE_VOCABULARY = frozenset(
    {
        "senior", "junior", "chief", "lead", "head", "software", "sales", "project",
        "product", "key", "account", "business", "service", "customer", "technical",
        "data", "it", "hr", "marketing", "finance", "financial", "regional", "assistant",
        "general", "field", "quality", "operations", "site", "store", "office", "team",
        "digital", "frontend", "backend", "fullstack", "full", "cloud", "principal",
        "staff", "graduate", "trainee", "deputy", "vice", "executive", "commercial",
        "daglig", "teknisk", "faglig", "avdelings", "assisterende", "kundeservice",
        "erfaren", "ny", "nye", "ledig", "fast", "vikar", "sommerjobb", "student",
    }
)
_LEADING_WORDS_RE = re.compile(r"^([A-ZÆØÅ][a-zæøåé]+)[ ]+([A-ZÆØÅ][a-zæøåé]+)\b")
_PHONE_IN_LABEL_RE = re.compile(r"(?:\+47|\b\d{2}\s?\d{2}\s?\d{2}\s?\d{2}\b)")


def _opens_with_person_name(label: str) -> bool:
    match = _LEADING_WORDS_RE.match(label.strip())
    if not match:
        return False
    first, second = match.group(1).casefold(), match.group(2).casefold()
    if first in _TITLE_VOCABULARY or second in _TITLE_VOCABULARY:
        return False
    return not (_ROLE_RE.fullmatch(first) or _ROLE_RE.fullmatch(second))


def _looks_like_job_title(label: str) -> bool:
    lowered = label.casefold().strip()
    if lowered.rstrip(" :–-") in _SECTION_HEADINGS:
        return False
    if not _ROLE_RE.search(lowered):
        return False
    if any(lowered.startswith(prefix) or prefix in lowered for prefix in _NOT_A_JOB):
        return False
    stripped = lowered.lstrip("-–•· ")
    # "Ledig stilling for tømrer i Bergen" names a role besides "stilling"; "Se ledige
    # stillinger ved vår skole" does not.
    roles = {item.casefold() for item in _ROLE_RE.findall(stripped)} - {"stilling", "stillinger"}
    for item in _NOT_A_JOB_PHRASES:
        if item in ("ledige stillinger", "ledig stilling") and roles:
            continue
        if stripped.startswith(item) or (len(item) > 4 and item in stripped):
            return False
    if re.match(r"^\s*[-–•]?\s*\d+[.)]\s", label) or "+" in label or "?" in label or "!" in label:
        return False
    if _PHONE_IN_LABEL_RE.search(label):
        return False
    if _opens_with_person_name(label):
        return False
    # A job title is a noun phrase, not a sentence or a breadcrumb trail.
    if len(label.split()) > 12 or label.count("/") > 1 or label.count("|") > 0:
        return False
    if lowered.endswith((".", "?", "!")):
        return False
    return True


def extract_activity(
    soup: BeautifulSoup, blocks: Iterable[dict[str, Any]], base_url: str, *, today: date | None = None
) -> list[Candidate]:
    """Dated public activity: news posts, press releases, articles.

    An undated item is not activity evidence, because recency is the whole point, so
    items without a usable date are dropped rather than published without one.
    """
    horizon = today or datetime.now(timezone.utc).date()
    found: dict[str, Candidate] = {}

    for block in H.of_type(blocks, "NewsArticle", "Article", "BlogPosting", "Event", "PressRelease"):
        headline = block.get("headline") or block.get("name")
        published = block.get("datePublished") or block.get("dateCreated") or block.get("startDate")
        if not isinstance(headline, str) or not published:
            continue
        iso = parse_date(str(published), not_after=horizon)
        if not iso:
            continue
        key = headline.strip().casefold()[:120]
        found.setdefault(
            key,
            Candidate(
                "activity_item",
                {
                    "title": H.strip_injection_bait(headline.strip())[:250],
                    "published_at": iso,
                    "url": canonical_url(str(block.get("url") or base_url)),
                    "kind": "article",
                },
                "json_pointer",
                f"/{block.get('_syntax', 'json-ld')}/{block.get('_index', 0)}",
                excerpt=f"{iso}: {headline.strip()[:120]}",
                method="structured_data",
                confidence=0.9,
            ),
        )

    if len(found) < 3:
        for node in soup.find_all(["time", "article", "li", "div"], limit=600):
            if not isinstance(node, Tag):
                continue
            stamp = node.get("datetime") if node.name == "time" else None
            block_text = node.get_text(" ", strip=True)
            if not stamp and len(block_text) > 400:
                continue
            iso = parse_date(str(stamp) if stamp else block_text[:80], not_after=horizon)
            if not iso:
                continue
            heading = node.find(["h1", "h2", "h3", "h4", "a"])
            title = (
                heading.get_text(" ", strip=True)
                if isinstance(heading, Tag)
                else block_text
            ).strip()
            title = re.sub(r"\s+", " ", title)[:250]
            if len(title) < 12:
                continue
            key = title.casefold()[:120]
            if key in found:
                continue
            href = heading.get("href") if isinstance(heading, Tag) and heading.name == "a" else None
            found[key] = Candidate(
                "activity_item",
                {
                    "title": H.strip_injection_bait(title),
                    "published_at": iso,
                    "url": canonical_url(str(href)) if href and str(href).startswith("http") else canonical_url(base_url),
                    "kind": "listing",
                },
                "css_selector",
                H.css_path(node),
                excerpt=f"{iso}: {title[:120]}",
                method="dated_listing",
                confidence=0.55,
            )
            if len(found) >= 20:
                break

    return sorted(
        found.values(), key=lambda item: str(item.value.get("published_at") or ""), reverse=True
    )[:20]


def extract_people(soup: BeautifulSoup, blocks: Iterable[dict[str, Any]]) -> list[Candidate]:
    """Named leadership published on the company's own team page."""
    found: dict[str, Candidate] = {}
    for block in H.of_type(blocks, "Person"):
        name = block.get("name")
        if not isinstance(name, str) or len(name.split()) < 2:
            continue
        found.setdefault(
            name.strip().casefold(),
            Candidate(
                "site_person",
                {
                    "name": name.strip()[:120],
                    "title": str(block.get("jobTitle"))[:120] if block.get("jobTitle") else None,
                },
                "json_pointer",
                f"/{block.get('_syntax', 'json-ld')}/{block.get('_index', 0)}",
                excerpt=name.strip(),
                method="structured_data",
                confidence=0.8,
            ),
        )
    return list(found.values())[:30]
