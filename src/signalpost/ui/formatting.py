"""Presentation helpers: value formatting and external-link safety.

Two rules are enforced here rather than in the templates, because a template is the
wrong place to be making security decisions:

* **Only ``http`` and ``https`` links are ever rendered as links.** Every other
  scheme — ``javascript:``, ``data:``, ``file:``, ``mailto:`` inside a scraped
  string — is rendered as escaped text instead, so a hostile value taken off a
  company website cannot become a clickable payload. Credentials embedded in the
  authority (``https://user:pass@host``) are refused for the same reason.
* **Nothing scraped is ever marked safe.** These functions return plain ``str``,
  never ``Markup``; Jinja's autoescaping then applies to all of it.
"""

from __future__ import annotations

import math
from typing import Any
from urllib.parse import urlsplit

from .data import Fact

ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})

# Characters that let a crafted string break out of an attribute or smuggle a
# second URL past a naive parser.
_FORBIDDEN_IN_URL = set('<>"\'`{}|\\^ \t\r\n')


def safe_url(value: Any) -> str | None:
    """Return ``value`` if it is a renderable http(s) URL, else ``None``.

    ``None`` is a deliberate signal to the caller: render the raw string as escaped
    text and say why it is not a link, rather than dropping it silently.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > 2048:
        return None
    if any(char in _FORBIDDEN_IN_URL for char in text):
        return None
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in text):
        return None
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        return None
    if not parts.netloc:
        return None
    # Embedded credentials: refused outright, per the safe-execution rules.
    if "@" in parts.netloc:
        return None
    return text


def url_host(value: Any) -> str | None:
    url = safe_url(value)
    if url is None:
        return None
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def format_int(value: Any) -> str:
    try:
        return f"{int(value):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(value)


def format_amount(value: Any, currency: str | None = None) -> str:
    """Money, in full. Rounded units are for the summary sentence, not the table."""
    number = value
    if isinstance(number, bool) or not isinstance(number, (int, float)):
        return str(value)
    if isinstance(number, float) and (math.isnan(number) or math.isinf(number)):
        return str(value)
    if float(number).is_integer():
        text = f"{int(number):,}".replace(",", " ")
    else:
        text = f"{number:,.2f}".replace(",", " ")
    return f"{text} {currency}" if currency else text


def format_timestamp(value: Any) -> str:
    """RFC3339 timestamps shown as ``YYYY-MM-DD HH:MM UTC``, unchanged otherwise."""
    if not isinstance(value, str) or len(value) < 10:
        return "" if value is None else str(value or "")
    if len(value) >= 17 and value[10] in ("T", " "):
        return f"{value[:10]} {value[11:16]} UTC"
    return value


def format_period(value: Any) -> str:
    if not isinstance(value, str) or "/" not in value:
        return str(value or "")
    start, _, end = value.partition("/")
    return f"{start} to {end}"


def format_scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        return format_amount(value)
    return str(value)


def format_fact(fact: Fact) -> str:
    """The display string for one fact's value. Never called for unknown facts."""
    value = fact.value
    if value is None:
        return ""
    if fact.field in {
        "revenue",
        "operating_costs",
        "operating_profit",
        "profit_before_tax",
        "net_profit",
        "total_assets",
        "total_equity",
        "total_equity_and_liabilities",
    }:
        return format_amount(value, fact.currency)
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        return format_int(value) if float(value).is_integer() else format_amount(value)
    if isinstance(value, dict):
        return summarise_mapping(value)
    if isinstance(value, list):
        return ", ".join(format_scalar(item) for item in value)
    return str(value)


def summarise_mapping(value: dict[str, Any]) -> str:
    """A one-line rendering of a structured claim value (address, role, subunit)."""
    if {"street", "city"} & value.keys():
        parts = [
            value.get("street"),
            value.get("postal_code"),
            value.get("city"),
            value.get("country"),
        ]
        return ", ".join(str(part) for part in parts if part)
    if "holder" in value:
        role = value.get("role") or value.get("role_code") or "Role"
        return f"{value.get('holder')} — {role}"
    if "platform" in value:
        return f"{value.get('platform')}: {value.get('url')}"
    if "title" in value:
        return str(value.get("title"))
    if "name" in value:
        return str(value.get("name"))
    return ", ".join(f"{key}: {format_scalar(item)}" for key, item in sorted(value.items()) if item is not None)


def format_seconds(value: Any) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return ""
    seconds = float(value)
    if seconds < 90:
        return f"{seconds:.1f} s"
    minutes, remainder = divmod(seconds, 60)
    return f"{int(minutes)} min {int(remainder)} s"


def format_bytes(value: Any) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return ""
    size = float(value)
    for unit in ("B", "kB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def short_hash(value: Any) -> str:
    text = str(value or "")
    return f"{text[:12]}…" if len(text) > 16 else text


def truncate(value: Any, limit: int = 240) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def percentage(part: Any, whole: Any) -> str:
    try:
        numerator, denominator = float(part), float(whole)
    except (TypeError, ValueError):
        return ""
    if denominator <= 0:
        return ""
    return f"{numerator / denominator * 100:.0f}%"
