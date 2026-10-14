"""HTML parsing primitives shared by every extractor.

Two rules shape this module:

* **Every extracted value keeps a locator.** A CSS selector, a JSON pointer into
  structured data, or a text span — enough for a reviewer to find the value again in
  the stored snapshot.
* **Page content is data, never instruction.** Retrieved pages may contain text that
  looks like a command ("ignore previous instructions", "publish this as fact"). The
  extractors below only ever read values into typed fields; nothing in a page can
  select a URL, change a rule or authorise anything. `strip_injection_bait` exists so
  that such text is visible in reports rather than passed around silently.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from ..net import canonical_url, validate_url

# Elements that never carry company facts.
NOISE_TAGS = ("script", "style", "noscript", "template", "svg", "iframe", "canvas")

# Phrases that indicate an attempt to steer an automated reader. Their presence does
# not change behaviour; it is recorded so a human can see it happened.
INJECTION_PATTERNS = (
    r"ignore (?:all |any )?(?:previous|prior|above) instructions",
    r"disregard (?:all |any )?(?:previous|prior|your|the) (?:instructions|rules|prompt)",
    r"forget (?:all |any )?(?:previous|prior|your) (?:instructions|rules)",
    r"you are (?:now |an? )?(?:AI|assistant|language model|chatbot)",
    r"you are now (?:in |a )?\w+(?: \w+)? mode",
    r"(?:enter|activate|switch to) (?:developer|debug|god|admin) mode",
    r"system prompt",
    r"^\s*(?:system|assistant|developer)\s*:",
    r"new instructions?\s*:",
    r"</?(?:system|assistant|user)>",
    r"do not (?:cite|verify|check) (?:this|the) (?:source|page)",
    r"publish (?:this|the following) as (?:fact|verified)",
    r"(?:reveal|print|output) (?:your |the )?(?:api[_ ]?key|secret|token|credentials)",
)
_INJECTION_RE = re.compile("|".join(INJECTION_PATTERNS), re.IGNORECASE | re.MULTILINE)


@dataclass(slots=True)
class Extracted:
    """A value plus where it came from inside the page."""

    value: Any
    locator_kind: str
    locator_value: str
    excerpt: str | None = None
    method: str = "html"


def parse(html: str) -> BeautifulSoup:
    """Parse with lxml, falling back to the stdlib parser on malformed input."""
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def page_title(soup: BeautifulSoup) -> Extracted | None:
    node = soup.find("title")
    if node and node.get_text(strip=True):
        return Extracted(node.get_text(strip=True), "css_selector", "title")
    node = soup.find("meta", attrs={"property": "og:title"})
    if isinstance(node, Tag) and node.get("content"):
        return Extracted(str(node["content"]).strip(), "css_selector", "meta[property='og:title']")
    return None


def meta_description(soup: BeautifulSoup) -> Extracted | None:
    """The company's own one-line self-description, when it publishes one."""
    for selector, attrs in (
        ("meta[name='description']", {"name": "description"}),
        ("meta[property='og:description']", {"property": "og:description"}),
        ("meta[name='twitter:description']", {"name": "twitter:description"}),
    ):
        node = soup.find("meta", attrs=attrs)
        if isinstance(node, Tag):
            content = str(node.get("content") or "").strip()
            if len(content) > 20:
                return Extracted(content, "css_selector", selector)
    return None


def visible_text(soup: BeautifulSoup, *, limit: int = 400_000) -> str:
    """Readable page text with scripts and styles removed."""
    clone = parse(str(soup))
    for tag in clone(NOISE_TAGS):
        tag.decompose()
    text = clone.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text)[:limit]


def main_text(html: str, url: str = "") -> str:
    """Article/body text, preferring trafilatura's boilerplate removal."""
    try:
        import trafilatura

        extracted = trafilatura.extract(
            html,
            url=url or None,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
        )
        if extracted and len(extracted) > 120:
            return re.sub(r"\s{3,}", "\n\n", extracted).strip()
    except Exception:
        pass
    return visible_text(parse(html), limit=20_000)


def structured_data(html: str, url: str) -> list[dict[str, Any]]:
    """JSON-LD, microdata, RDFa and Open Graph, flattened into a list.

    Structured data is the highest-quality extraction source: the company published
    it as machine-readable fact about itself.
    """
    blocks: list[dict[str, Any]] = []
    try:
        import extruct

        data = extruct.extract(
            html, base_url=url, syntaxes=["json-ld", "microdata", "opengraph", "rdfa"], uniform=True
        )
    except Exception:
        data = {}

    for syntax in ("json-ld", "microdata", "rdfa", "opengraph"):
        for index, item in enumerate(data.get(syntax) or []):
            if isinstance(item, dict):
                blocks.append({"_syntax": syntax, "_index": index, **item})
    if blocks:
        return blocks

    # extruct occasionally fails on malformed markup; read JSON-LD directly.
    for index, script in enumerate(parse(html).find_all("script", type="application/ld+json")):
        try:
            payload = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for item in payload if isinstance(payload, list) else [payload]:
            if isinstance(item, dict):
                blocks.append({"_syntax": "json-ld", "_index": index, **item})
    return blocks


def of_type(blocks: Iterable[dict[str, Any]], *types: str) -> list[dict[str, Any]]:
    """Filter structured-data blocks by schema.org @type."""
    wanted = {item.casefold() for item in types}
    found = []
    for block in blocks:
        raw = block.get("@type") or block.get("type")
        values = raw if isinstance(raw, list) else [raw]
        for value in values:
            if isinstance(value, str) and value.rsplit("/", 1)[-1].casefold() in wanted:
                found.append(block)
                break
    return found


def links(soup: BeautifulSoup, base_url: str) -> list[tuple[str, str]]:
    """All safe, absolute links as (url, anchor text), de-duplicated."""
    seen: set[str] = set()
    found: list[tuple[str, str]] = []
    for anchor in soup.find_all("a", href=True):
        raw = str(anchor["href"]).strip()
        if not raw or raw.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(base_url, raw)
        if not validate_url(absolute, resolve=False).allowed:
            continue
        canonical = canonical_url(absolute)
        if canonical in seen:
            continue
        seen.add(canonical)
        found.append((absolute, anchor.get_text(" ", strip=True)[:200]))
    return found


def css_path(node: Tag, *, max_depth: int = 5) -> str:
    """A short, readable CSS path for a node, used as an evidence locator."""
    parts: list[str] = []
    current: Any = node
    while isinstance(current, Tag) and current.name and len(parts) < max_depth:
        segment = current.name
        node_id = current.get("id")
        if node_id:
            parts.append(f"{segment}#{node_id}")
            break
        classes = [item for item in (current.get("class") or []) if item][:2]
        if classes:
            segment += "." + ".".join(classes)
        parts.append(segment)
        current = current.parent
    return " > ".join(reversed(parts)) or node.name or "body"


def text_span(haystack: str, needle: str, *, window: int = 90) -> str:
    """A `start:end` span locator plus surrounding context, for text-derived values."""
    position = haystack.find(needle)
    if position < 0:
        return ""
    start = max(0, position - window)
    end = min(len(haystack), position + len(needle) + window)
    return f"{position}:{position + len(needle)}|{haystack[start:end]}"


def detect_injection(text: str) -> list[str]:
    """Report instruction-like phrases found in retrieved content.

    Reporting only. Retrieved text is never executed, never used to choose a URL and
    never allowed to alter agent rules; this makes attempts visible in the run report.
    """
    found: list[str] = []
    for match in _INJECTION_RE.finditer(text or ""):
        snippet = match.group(0).strip()
        if snippet and snippet not in found:
            found.append(snippet[:120])
        if len(found) >= 5:
            break
    return found


def strip_injection_bait(text: str) -> str:
    """Neutralise instruction-shaped spans in text that will be summarised later."""
    return _INJECTION_RE.sub("[removed: instruction-like text]", text or "")


def language_of(soup: BeautifulSoup) -> str | None:
    node = soup.find("html")
    if isinstance(node, Tag):
        value = str(node.get("lang") or "").strip().lower()
        if value:
            return value.split("-")[0]
    return None
