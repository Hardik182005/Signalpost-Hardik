"""URL safety: SSRF defence, redaction and host policy.

Retrieved pages are untrusted input. They can contain links, redirects and
meta-refresh targets chosen by whoever controls the page, so every URL the agent is
about to open is validated here first — including each redirect hop.

The defence is two-stage because name resolution is attacker-influenced:

1. Validate the URL shape (scheme, credentials, port, hostname).
2. Resolve the hostname and require *every* returned address to be publicly
   routable, then hand the caller the exact addresses that were validated.

The caller connects to one of those pinned addresses rather than re-resolving, which
is what closes the DNS-rebinding window between check and connect.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})
ALLOWED_PORTS = frozenset({80, 443})

# Hostname suffixes that never denote a public internet host.
BLOCKED_SUFFIXES = (
    ".local",
    ".localhost",
    ".internal",
    ".intranet",
    ".corp",
    ".home",
    ".lan",
    ".home.arpa",
    ".in-addr.arpa",
    ".ip6.arpa",
    ".onion",
    ".test",
    ".invalid",
    ".example",
)

BLOCKED_HOSTNAMES = frozenset({"localhost", "metadata", "metadata.google.internal"})

# Query parameters and headers that may carry credentials or session state and must
# never reach a log, a snapshot record or an evidence URL.
SENSITIVE_QUERY_KEYS = frozenset(
    {
        "key",
        "apikey",
        "api_key",
        "access_token",
        "token",
        "auth",
        "authorization",
        "password",
        "passwd",
        "pwd",
        "secret",
        "client_secret",
        "signature",
        "sig",
        "sessionid",
        "session",
        "sid",
        "subscription-key",
    }
)

SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "ocp-apim-subscription-key",
        "x-auth-token",
    }
)

# Tracking parameters stripped before a URL becomes part of a stable claim key, so
# that a marketing tag change is not mistaken for a real content change.
TRACKING_PREFIXES = ("utm_", "mc_", "pk_", "hsa_", "_hs")
TRACKING_KEYS = frozenset(
    {
        "gclid",
        "fbclid",
        "msclkid",
        "dclid",
        "igshid",
        "mkt_tok",
        "ref",
        "referrer",
        "source",
        "yclid",
        "twclid",
        "_ga",
        "_gl",
    }
)

# Cache-busters and session identifiers. These are a different problem from marketing
# tags and are stripped for two reasons rather than one.
#
# `doing_wp_cron` is the reason this set exists: WordPress appends it, with a fresh
# float timestamp, to a redirect it issues while triggering its own scheduled tasks. A
# measured refresh run published a company website as
# `https://mastermat.no/?doing_wp_cron=1789309421.0442390441894531250000` and then
# reported a material `value_changed` event on the next fetch, because the number is
# different every time. Nothing about the company had changed. That is precisely the
# false change the refresh hard gate forbids, and it is also simply the wrong answer to
# "what is this company's website".
#
# The session keys are stripped for a second reason: a session identifier captured from
# a redirect must never be written into a published claim or a snapshot filename.
VOLATILE_KEYS = frozenset(
    {
        "doing_wp_cron",
        "cachebuster",
        "cache_buster",
        "nocache",
        "phpsessid",
        "jsessionid",
        "aspsessionid",
        "sessionid",
        "session_id",
        "cfid",
        "cftoken",
    }
)


class UnsafeUrl(ValueError):
    """Raised when a URL must not be opened."""


@dataclass(slots=True)
class UrlVerdict:
    """Outcome of validating one URL."""

    url: str
    allowed: bool
    reason: str | None = None
    scheme: str = ""
    host: str = ""
    port: int = 0
    addresses: list[str] = field(default_factory=list)

    def require(self) -> "UrlVerdict":
        if not self.allowed:
            raise UnsafeUrl(f"{redact_url(self.url)}: {self.reason}")
        return self


def _unwrap(address: ipaddress._BaseAddress) -> ipaddress._BaseAddress:
    """Unwrap IPv6 forms that embed an IPv4 address.

    ::ffff:169.254.169.254 and 64:ff9b::169.254.169.254 are globally-scoped as IPv6
    objects but reach an IPv4 destination, so they are checked as IPv4.
    """
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped:
            return address.ipv4_mapped
        if address.sixtofour:
            return address.sixtofour
        if getattr(address, "teredo", None):
            return address.teredo[1]
        nat64 = ipaddress.ip_network("64:ff9b::/96")
        if address in nat64:
            return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return address


def address_is_public(raw: str) -> tuple[bool, str | None]:
    """True when an address is safe to connect to from the agent."""
    try:
        address = _unwrap(ipaddress.ip_address(raw))
    except ValueError:
        return False, "unparseable address"
    if address.is_unspecified:
        return False, "unspecified address"
    if address.is_loopback:
        return False, "loopback address"
    if address.is_link_local:
        return False, "link-local address (includes cloud metadata)"
    if address.is_private:
        return False, "private address"
    if address.is_reserved:
        return False, "reserved address"
    if address.is_multicast:
        return False, "multicast address"
    if not address.is_global:
        return False, "non-global address"
    return True, None


def hostname_is_blocked(host: str) -> str | None:
    lowered = host.strip().rstrip(".").lower()
    if not lowered:
        return "missing hostname"
    if lowered in BLOCKED_HOSTNAMES:
        return f"blocked hostname {lowered!r}"
    if any(lowered.endswith(suffix) for suffix in BLOCKED_SUFFIXES):
        return f"blocked hostname suffix on {lowered!r}"
    if "." not in lowered:
        # A single label cannot be a public DNS name and may resolve via local search
        # domains to an internal host.
        return f"single-label hostname {lowered!r}"
    return None


def resolve_public_addresses(host: str, port: int) -> tuple[list[str], str | None]:
    """Resolve a hostname and require every address to be publicly routable.

    Rejecting on *any* private answer (rather than filtering to the public ones)
    avoids a split-horizon record steering the agent at an internal host.
    """
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        return [], f"DNS resolution failed ({exc.strerror or exc})"
    except UnicodeError as exc:
        return [], f"invalid hostname ({exc})"
    if not infos:
        return [], "DNS returned no addresses"

    addresses: list[str] = []
    for info in infos:
        candidate = info[4][0]
        allowed, reason = address_is_public(candidate)
        if not allowed:
            return [], f"{host} resolves to a disallowed address ({reason})"
        if candidate not in addresses:
            addresses.append(candidate)
    return addresses, None


def validate_url(url: str, *, resolve: bool = True) -> UrlVerdict:
    """Full safety verdict for one URL."""
    raw = (url or "").strip()
    if not raw:
        return UrlVerdict(url=raw, allowed=False, reason="empty URL")
    if any(ch in raw for ch in ("\n", "\r", "\t", " ")):
        return UrlVerdict(url=raw, allowed=False, reason="URL contains whitespace or control characters")

    try:
        parts = urlsplit(raw)
    except ValueError as exc:
        return UrlVerdict(url=raw, allowed=False, reason=f"unparseable URL ({exc})")

    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        return UrlVerdict(url=raw, allowed=False, reason=f"scheme {scheme or '(none)'!r} is not http(s)")
    if parts.username or parts.password or "@" in (parts.netloc.split("/")[0]):
        return UrlVerdict(url=raw, allowed=False, reason="URL embeds credentials")

    try:
        host = parts.hostname or ""
        # `or` would treat an explicit ":0" as "no port given" and silently rewrite it
        # to the scheme default, so an invalid port has to be distinguished from None.
        explicit = parts.port
        port = explicit if explicit is not None else (443 if scheme == "https" else 80)
    except ValueError as exc:
        return UrlVerdict(url=raw, allowed=False, reason=f"invalid host or port ({exc})")

    if port not in ALLOWED_PORTS:
        return UrlVerdict(url=raw, allowed=False, reason=f"port {port} is not a standard web port")

    verdict = UrlVerdict(url=raw, allowed=False, scheme=scheme, host=host, port=port)

    # A literal IP skips DNS entirely and is checked directly.
    try:
        ipaddress.ip_address(host)
    except ValueError:
        blocked = hostname_is_blocked(host)
        if blocked:
            verdict.reason = blocked
            return verdict
        if not resolve:
            verdict.allowed = True
            return verdict
        addresses, reason = resolve_public_addresses(host, port)
        if reason:
            verdict.reason = reason
            return verdict
        verdict.addresses = addresses
        verdict.allowed = True
        return verdict

    allowed, reason = address_is_public(host)
    if not allowed:
        verdict.reason = reason
        return verdict
    verdict.addresses = [host]
    verdict.allowed = True
    return verdict


def redact_url(url: str) -> str:
    """Strip credentials and secret-bearing query values for logs and evidence."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "(unparseable url)"
    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    if parts.username or parts.password:
        netloc = f"[redacted]@{netloc}"
    query = urlencode(
        [
            (key, "[redacted]" if key.lower() in SENSITIVE_QUERY_KEYS else value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
        ]
    )
    return urlunsplit((parts.scheme, netloc, parts.path, query, ""))


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        key: ("[redacted]" if key.lower() in SENSITIVE_HEADERS else value)
        for key, value in headers.items()
    }


_PATH_SESSION_RE = re.compile(
    r";(?:jsessionid|cfid|cftoken|phpsessid)=[^/;?]*", re.IGNORECASE
)


def canonical_url(url: str) -> str:
    """Canonical form used inside stable claim keys and identity comparison.

    Drops the fragment, tracking parameters, a default port and a trailing slash, so
    that cosmetic link changes do not register as content changes.
    """
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    scheme = parts.scheme.lower() or "https"
    host = (parts.hostname or "").lower().rstrip(".")
    port = parts.port
    netloc = host
    if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        netloc = f"{host}:{port}"
    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_KEYS
        and key.lower() not in VOLATILE_KEYS
        and not key.lower().startswith(TRACKING_PREFIXES)
    ]
    path = parts.path or "/"
    # Java and ColdFusion servers put the session in a path parameter rather than the
    # query string, so the filter above never sees it.
    path = _PATH_SESSION_RE.sub("", path)
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, urlencode(kept), ""))


def registrable_domain(url_or_host: str) -> str:
    """Best-effort registrable domain, used for same-site checks.

    Falls back to the last two labels when tldextract's suffix list is unavailable,
    which is acceptable because the result is only ever used as a heuristic signal
    alongside stronger identity evidence.
    """
    host = url_or_host
    if "://" in url_or_host:
        host = urlsplit(url_or_host).hostname or ""
    host = host.strip().rstrip(".").lower()
    if not host:
        return ""
    try:
        import tldextract

        extracted = tldextract.extract(host)
        if extracted.domain and extracted.suffix:
            return f"{extracted.domain}.{extracted.suffix}"
    except Exception:
        pass
    labels = host.split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else host


def same_site(left: str, right: str) -> bool:
    a, b = registrable_domain(left), registrable_domain(right)
    return bool(a) and a == b


def iter_safe(urls: Iterable[str], *, resolve: bool = False) -> list[str]:
    """Filter a list of candidate URLs down to the ones that pass shape validation."""
    return [url for url in urls if validate_url(url, resolve=resolve).allowed]
