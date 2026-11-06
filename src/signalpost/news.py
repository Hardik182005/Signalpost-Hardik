"""Independent news coverage that names the company.

Kept strictly apart from `activity`, which is only what the company publishes on its
own verified site. A news hit is a *candidate* until the article itself has been
fetched and read:

1. The GDELT DOC 2.0 API (documented, keyless, public) is asked for recent articles
   containing the company's registered name as an exact phrase. A search result is
   never evidence by itself.
2. Articles on the company's own domain are dropped (that is company activity).
3. Up to `max_articles` remaining articles are fetched, robots.txt permitting, and the
   visible text must name the company: the full registered name including its legal
   form, or the name stem plus an independent context signal (the organisation number,
   the registered city or the verified website domain). A different legal form written
   after the same stem ("GLØR IKS" for GLØR AS) rejects the article.

GDELT asks for at most one request every five seconds. A process-wide gate enforces
that across workers; a company whose turn would wait too long records the lookup as
skipped rather than stalling the batch. Everything optional stops when the run budget
reaches its discretionary limit.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlsplit

from .budget import BudgetExceeded
from .extract import html as H
from .fetch import Fetcher, Response
from .identity import (
    CONFLICTING_FORMS,
    CompanyIdentity,
    distinctive_tokens,
    find_org_numbers,
    legal_form,
    normalise_name,
    normalise_registered_name,
)
from .models import Claim, Evidence, Locator, Snapshot, claim_key, stable_id
from .net import registrable_domain

GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_MIN_INTERVAL = 5.5
DEFAULT_MAX_ARTICLES = 3
DEFAULT_MAX_WAIT = 90.0
# Never queue for the news API into the time the run keeps for finishing companies.
TIME_RESERVE_RATIO = 0.15
MIN_STEM_LENGTH = 4


class RateGate:
    """Serialises calls to one rate-limited API across every worker in the process."""

    def __init__(self, interval: float = GDELT_MIN_INTERVAL) -> None:
        self.interval = interval
        self._next = 0.0
        self._lock = threading.Lock()

    def acquire(self, max_wait: float) -> bool:
        with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._next - now)
            if wait > max_wait:
                return False
            self._next = max(now, self._next) + self.interval
        if wait:
            time.sleep(wait)
        return True


GDELT_GATE = RateGate()


@dataclass(slots=True)
class NewsResult:
    claims: list[Claim] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    snapshots: list[Snapshot] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)


def search_phrase(legal_name: str) -> str | None:
    """The registered name without its legal form, or None when it is too generic."""
    stem = normalise_name(legal_name)
    if len(stem.replace(" ", "")) < MIN_STEM_LENGTH or not distinctive_tokens(legal_name):
        return None
    words = re.findall(r"[\w&'-]+", legal_name, flags=re.UNICODE)
    while words and words[-1].casefold() in {"as", "asa", "ans", "da", "sa", "nuf", "ba", "enk"}:
        words.pop()
    if words and words[0].casefold() in {"as", "asa"} and len(words) > 1:
        words.pop(0)
    phrase = " ".join(words).strip()
    return phrase or None


def parse_seendate(value: str) -> str | None:
    try:
        moment = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    return moment.isoformat().replace("+00:00", "Z")


def mentions_company(text: str, identity: CompanyIdentity, verified_domain: str | None) -> tuple[bool, str]:
    """Whether article text names this exact company, and on what grounds."""
    body = normalise_registered_name(text)
    full = normalise_registered_name(identity.legal_name)
    stem = normalise_name(identity.legal_name)
    form = legal_form(identity.legal_name)
    padded = f" {body} "
    if not stem or f" {stem} " not in padded:
        return False, "name stem not in article text"
    for other in CONFLICTING_FORMS - {form or ""}:
        if f" {stem} {other} " in padded:
            return False, f"article names a different legal entity ({stem} {other})"
    exact = bool(form) and f" {full} " in padded
    context: list[str] = []
    if identity.organisation_number in find_org_numbers(text):
        context.append("organisation number")
    if any(city and f" {normalise_registered_name(city)} " in padded for city in identity.cities):
        context.append("registered city")
    if verified_domain and verified_domain in text.casefold():
        context.append("website domain")
    single_word = len(stem.split()) == 1
    if exact and (context or not single_word):
        return True, "registered name" + (f" + {', '.join(context)}" if context else "")
    if context and not single_word:
        return True, f"name + {', '.join(context)}"
    if exact:
        return False, "registered name found but a one-word name needs a context signal"
    return False, "name stem found without legal form or context signal"


def _excerpt(text: str, legal_name: str, width: int = 160) -> str:
    stem_words = normalise_name(legal_name).split()
    first = stem_words[0] if stem_words else ""
    index = text.casefold().find(first) if first else -1
    if index < 0:
        return text[:width]
    start = max(0, index - width // 3)
    return re.sub(r"\s+", " ", text[start:start + width]).strip()


class NewsResearcher:
    def __init__(
        self,
        fetcher: Fetcher,
        *,
        gate: RateGate | None = None,
        max_articles: int = DEFAULT_MAX_ARTICLES,
        max_wait: float = DEFAULT_MAX_WAIT,
    ) -> None:
        self.fetcher = fetcher
        self.gate = gate or GDELT_GATE
        self.max_articles = max_articles
        self.max_wait = max_wait

    def _empty(self, org: str, availability: str, reason: str, detail: dict[str, Any]) -> NewsResult:
        detail["reason"] = reason
        return NewsResult(
            claims=[Claim(claim_key=claim_key(org, "news"), field="news", family="news", value=None,
                          availability=availability, organisation_number=org, reason=reason)],
            detail=detail,
        )

    def research(self, org: str, identity: CompanyIdentity, verified_url: str | None = None) -> NewsResult:
        detail: dict[str, Any] = {"source": "gdelt_doc_api", "searched": False, "candidates": 0,
                                  "fetched": 0, "rejected": []}
        phrase = search_phrase(identity.legal_name)
        if not phrase:
            return self._empty(org, "ambiguous", "registered name too generic for an exact-phrase news search", detail)
        if self.fetcher.budget.should_stop_discretionary():
            return self._empty(org, "not_available", "news search skipped to protect the run budget", detail)
        budget = self.fetcher.budget
        reserve = getattr(budget, "max_seconds", 0.0) * TIME_RESERVE_RATIO
        remaining = budget.seconds_remaining() if hasattr(budget, "seconds_remaining") else self.max_wait
        if not self.gate.acquire(max(0.0, min(self.max_wait, remaining - reserve))):
            return self._empty(org, "not_available", "news search skipped: shared GDELT rate limit busy", detail)

        url = (f"{GDELT_DOC_URL}?query={quote(chr(34) + phrase + chr(34))}"
               "&mode=artlist&format=json&maxrecords=15&sort=datedesc")
        detail["searched"] = True
        detail["query"] = phrase
        try:
            response = self.fetcher.get(url, purpose="news_search", accept="application/json",
                                        capture_snapshot=False, attempts=1)
        except BudgetExceeded as exc:
            return self._empty(org, "not_available", f"news search stopped: {exc}", detail)
        if not response.ok:
            return self._empty(org, "failed", f"news search failed: {response.error or response.status}", detail)
        payload = response.json()
        if not isinstance(payload, dict):
            message = (response.text or "")[:60].strip()
            return self._empty(org, "failed", f"news search returned no JSON ({message or 'empty'})", detail)

        own_domain = registrable_domain(urlsplit(verified_url).hostname or "") if verified_url else None
        articles = [item for item in payload.get("articles") or [] if isinstance(item, dict) and item.get("url")]
        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        stem = normalise_name(identity.legal_name)
        for item in articles:
            host = urlsplit(str(item["url"])).hostname or ""
            domain = registrable_domain(host)
            if own_domain and domain == own_domain:
                detail["rejected"].append({"url": item["url"], "reason": "company's own domain (activity, not news)"})
                continue
            title_key = normalise_name(str(item.get("title") or ""))
            if title_key in seen:
                continue
            seen.add(title_key)
            item["_title_hit"] = f" {stem} " in f" {title_key} "
            candidates.append(item)
        candidates.sort(key=lambda item: not item["_title_hit"])
        detail["candidates"] = len(candidates)

        result = NewsResult(detail=detail)
        for item in candidates[: self.max_articles]:
            if self.fetcher.budget.should_stop_discretionary():
                break
            try:
                article = self.fetcher.get(str(item["url"]), purpose="news_article",
                                           source_class="permitted_public", attempts=1)
            except BudgetExceeded:
                break
            detail["fetched"] += 1
            verdict = self._publish(org, identity, item, article, own_domain, result)
            if verdict:
                detail["rejected"].append({"url": item["url"], "reason": verdict})

        published = [claim for claim in result.claims if claim.field == "news_mention"]
        detail["published"] = len(published)
        if not published:
            reason = ("no independent article naming the company was found"
                      if not candidates else
                      f"{len(candidates)} search hit(s), none confirmed to name this company")
            empty = self._empty(org, "not_available", reason, detail)
            empty.snapshots, empty.evidence = [], []
            return empty
        return result

    def _publish(self, org: str, identity: CompanyIdentity, item: dict[str, Any], article: Response,
                 own_domain: str | None, result: NewsResult) -> str | None:
        if not article.ok or not article.text:
            return f"article not retrievable ({article.error_kind or article.status})"
        if "html" not in (article.content_type or "html"):
            return "article is not an HTML page"
        text = H.visible_text(H.parse(article.text))
        ok, grounds = mentions_company(text, identity, own_domain)
        if not ok:
            return grounds
        snapshot = article.snapshot
        if snapshot is None:
            return "article snapshot not captured"
        final_url = article.final_url or str(item["url"])
        host = urlsplit(final_url).hostname or ""
        value = {
            "title": str(item.get("title") or "").strip()[:300],
            "url": final_url,
            "outlet": registrable_domain(host) or host,
            "published": parse_seendate(str(item.get("seendate") or "")),
            "language": item.get("language"),
            "match": grounds,
        }
        excerpt = _excerpt(text, identity.legal_name)
        evidence = Evidence(
            id=stable_id("ev", snapshot.id, "news", final_url),
            snapshot_id=snapshot.id,
            source_url=snapshot.final_url,
            source_class=snapshot.source_class,
            retrieved_at=snapshot.retrieved_at,
            content_sha256=article.content_sha256,
            locator=Locator(kind="text_span", value="article body", excerpt=excerpt),
            claim_span=excerpt,
            extraction_method="gdelt_candidate+article_name_check",
        )
        result.snapshots.append(snapshot)
        result.evidence.append(evidence)
        result.claims.append(Claim(
            claim_key=claim_key(org, "news_mention", final_url.split("?")[0].rstrip("/")),
            field="news_mention",
            family="news",
            value=value,
            availability="available",
            organisation_number=org,
            evidence_ids=[evidence.id],
            confidence=0.8 if "+" in grounds else 0.7,
            effective_date=(value["published"] or "")[:10] or None,
            extraction_method="gdelt_candidate+article_name_check",
        ))
        return None
