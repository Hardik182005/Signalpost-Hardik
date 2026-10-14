"""The agent's single outbound egress point.

Every byte the agent retrieves passes through `Fetcher.get()`. Concentrating egress
in one place is what makes the safety and budget guarantees checkable: there is
exactly one function to audit for SSRF, robots compliance, request accounting,
throttling, size caps and snapshot capture.

Design notes worth keeping in mind when editing:

* Redirects are followed manually, one hop at a time, because each hop is a new URL
  chosen by a remote server and must be revalidated. `urllib`'s automatic redirect
  handling would bypass that check.
* Connections go to an IP address that was validated moments earlier and is pinned
  for the connection, while TLS SNI and certificate verification still use the real
  hostname. That closes the DNS-rebinding gap between validation and connect.
* Every hop reserves request budget before it is issued, including redirects and
  retries, because the evaluator counts those too.
* Responses are read with a hard byte ceiling so a hostile or broken server cannot
  exhaust the 10 GB temp disk.
"""

from __future__ import annotations

import gzip
import hashlib
import http.client
import io
import json
import socket
import ssl
import threading
import time
import zlib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from .budget import Budget, BudgetExceeded
from .models import Snapshot, stable_id, utc_now
from .net import UrlVerdict, redact_url, validate_url

USER_AGENT = (
    "SignalpostResearchBot/1.0 (+https://github.com/Hardik182005/signalpost; "
    "company research for the Signalpost challenge)"
)

DEFAULT_TIMEOUT = 12.0
DEFAULT_MAX_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_REDIRECTS = 4
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_HOST_DELAY = 0.6

# Hosts that are public machine APIs rather than someone's web server. The 0.6 s
# politeness gap is right for a small company site and wrong for the Brønnøysund
# open-data API: every company costs four registry calls, all to one host, so at 0.6 s
# a 1,000-company batch spent 4,016 x 0.6 = 2,410 s of its 2,539 s waiting on that
# single throttle (measured, submission-1000). 0.1 s is ten requests a second.
API_HOST_DELAYS = {
    "data.brreg.no": 0.1,
}

RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

# Failures whose answer will not change on a second attempt. A closed port stays closed
# and a certificate stays invalid, so a retry is a request spent for no information.
# `network` is deliberately absent: it covers resets, DNS hiccups and — on Windows — an
# ephemeral source port landing in an administered exclusion range, all of which are
# genuinely worth one more try.
TERMINAL_ERROR_KINDS = frozenset({"refused", "tls", "unsupported_media"})

# Content we can actually extract from. Anything else is recorded and skipped rather
# than downloaded in full.
TEXTUAL_TYPES = (
    "text/html",
    "application/xhtml",
    "text/plain",
    "application/json",
    "application/ld+json",
    "application/xml",
    "text/xml",
    "application/rss",
    "application/atom",
    "application/pdf",
)


class FetchBlocked(RuntimeError):
    """Raised when a fetch is refused by policy (safety, robots or budget)."""

    def __init__(self, reason: str, *, kind: str = "blocked") -> None:
        super().__init__(reason)
        self.reason = reason
        self.kind = kind


@dataclass(slots=True)
class Response:
    """One completed retrieval, with everything needed to cite it later."""

    ok: bool
    url: str
    final_url: str
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    text: str = ""
    content_type: str | None = None
    content_sha256: str = ""
    retrieved_at: str = ""
    elapsed_ms: int = 0
    redirect_chain: list[str] = field(default_factory=list)
    from_cache: bool = False
    truncated: bool = False
    error: str | None = None
    error_kind: str | None = None
    snapshot: Snapshot | None = None

    def json(self) -> Any:
        if not self.body:
            return None
        try:
            return json.loads(self.body.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            return None


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTP connection that dials a pre-validated IP but keeps the real Host header."""

    def __init__(self, host: str, port: int, *, pinned_ip: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._pinned_ip = pinned_ip

    def connect(self) -> None:  # pragma: no cover - exercised over the network
        self.sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        if self._tunnel_host:
            self._tunnel()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS variant: pinned address for the socket, hostname for SNI and cert check."""

    def __init__(
        self, host: str, port: int, *, pinned_ip: str, timeout: float, context: ssl.SSLContext
    ) -> None:
        super().__init__(host, port, timeout=timeout, context=context)
        self._pinned_ip = pinned_ip

    def connect(self) -> None:  # pragma: no cover - exercised over the network
        sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        if self._tunnel_host:
            self.sock = sock
            self._tunnel()
            sock = self.sock
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class RobotsPolicy:
    """robots.txt cache and decision point.

    A robots fetch is itself an outbound request and is charged to the budget. When
    robots.txt cannot be retrieved we allow the fetch, matching the convention that an
    unreachable robots file is not a directive; when it is retrieved and disallows the
    path, the fetch is refused and the claim is recorded as `blocked`.
    """

    def __init__(self, fetcher: "Fetcher") -> None:
        self._fetcher = fetcher
        self._parsers: dict[str, RobotFileParser | None] = {}
        self._crawl_delay: dict[str, float] = {}
        self._lock = threading.Lock()

    def _load(self, origin: str) -> RobotFileParser | None:
        with self._lock:
            if origin in self._parsers:
                return self._parsers[origin]
        parser: RobotFileParser | None = None
        response = self._fetcher.get(
            urljoin(origin, "/robots.txt"),
            purpose="robots",
            respect_robots=False,
            max_bytes=512 * 1024,
            capture_snapshot=False,
            allow_non_textual=True,
        )
        if response.ok and response.status == 200 and response.text:
            parser = RobotFileParser()
            try:
                parser.parse(response.text.splitlines())
            except Exception:
                parser = None
        with self._lock:
            self._parsers[origin] = parser
            if parser is not None:
                try:
                    delay = parser.crawl_delay(USER_AGENT)
                except Exception:
                    delay = None
                if delay:
                    self._crawl_delay[origin] = min(float(delay), 10.0)
        return parser

    def allows(self, url: str) -> tuple[bool, str | None]:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        try:
            parser = self._load(origin)
        except BudgetExceeded:
            # Out of budget to check: do not fetch on an unverified assumption.
            return False, "robots.txt could not be checked within the request budget"
        if parser is None:
            return True, None
        try:
            if parser.can_fetch(USER_AGENT, url):
                return True, None
        except Exception:
            return True, None
        return False, "disallowed by robots.txt"

    def crawl_delay(self, url: str) -> float:
        parts = urlsplit(url)
        with self._lock:
            return self._crawl_delay.get(f"{parts.scheme}://{parts.netloc}", 0.0)


class _HostThrottle:
    """Per-host politeness spacing, honoured across concurrent workers."""

    def __init__(self, default_delay: float, host_delays: dict[str, float] | None = None) -> None:
        self._default = default_delay
        self._host_delays = dict(host_delays or {})
        self._next_allowed: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str, delay: float | None = None) -> float:
        base = self._host_delays.get(host, self._default)
        gap = base if delay is None else max(base, delay)
        with self._lock:
            now = time.monotonic()
            earliest = self._next_allowed.get(host, 0.0)
            sleep_for = max(0.0, earliest - now)
            self._next_allowed[host] = max(now, earliest) + gap
        if sleep_for > 0:
            time.sleep(min(sleep_for, 10.0))
        return sleep_for


class SnapshotStore:
    """Content-addressed store of retrieved bytes.

    Storing by content hash means a re-run over unchanged sources reuses the same
    files and produces the same snapshot ids, which is the basis of false-change-free
    refresh.
    """

    def __init__(self, root: Path | None) -> None:
        self.root = Path(root) if root else None
        if self.root:
            self.root.mkdir(parents=True, exist_ok=True)

    def store(self, digest: str, payload: bytes) -> str | None:
        if not self.root:
            return None
        target = self.root / digest[:2] / f"{digest}.bin"
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".tmp")
            temporary.write_bytes(payload)
            temporary.replace(target)
        return str(target.relative_to(self.root.parent)) if self.root.parent else str(target)

    def load(self, digest: str) -> bytes | None:
        if not self.root:
            return None
        target = self.root / digest[:2] / f"{digest}.bin"
        return target.read_bytes() if target.exists() else None


class Fetcher:
    """Safe, budgeted, polite HTTP client."""

    def __init__(
        self,
        budget: Budget,
        *,
        snapshot_root: Path | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        host_delay: float = DEFAULT_HOST_DELAY,
        offline: bool = False,
    ) -> None:
        self.budget = budget
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.max_attempts = max_attempts
        self.offline = offline
        self.snapshots = SnapshotStore(snapshot_root)
        self._throttle = _HostThrottle(host_delay, API_HOST_DELAYS if host_delay == DEFAULT_HOST_DELAY else None)
        self._robots = RobotsPolicy(self)
        self._ssl_context = ssl.create_default_context()
        self._cache: dict[str, Response] = {}
        self._cache_lock = threading.Lock()
        self._log: list[dict[str, Any]] = []
        self._log_lock = threading.Lock()

    # -- public API -----------------------------------------------------------

    def get(
        self,
        url: str,
        *,
        purpose: str = "unspecified",
        accept: str = "*/*",
        respect_robots: bool = True,
        max_bytes: int | None = None,
        capture_snapshot: bool = True,
        source_class: str = "permitted_public",
        allow_non_textual: bool = False,
        use_cache: bool = True,
        attempts: int | None = None,
        timeout: float | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        """Retrieve one URL, following validated redirects.

        `attempts` and `timeout` override the fetcher defaults for one call. They exist
        for the registry, whose answers the whole profile depends on: every attempt is
        still a request charged to the same budget.

        `headers` are extra request headers, such as an API bearer token. They are sent
        only to the host first requested, never across a redirect to another host, and
        are not recorded in snapshots or the request log.
        """
        started = time.monotonic()
        cache_key = f"{purpose}\x1f{url}"
        if use_cache:
            with self._cache_lock:
                cached = self._cache.get(cache_key)
            if cached is not None:
                return replace(cached, from_cache=True)

        if self.offline:
            return self._failure(url, "offline mode: no outbound requests permitted", "offline")

        chain: list[str] = []
        current = url
        limit = max_bytes or self.max_bytes
        origin_host = urlsplit(url).hostname

        for hop in range(self.max_redirects + 1):
            verdict = validate_url(current)
            if not verdict.allowed:
                return self._failure(
                    url, f"unsafe URL refused: {verdict.reason}", "unsafe_url", chain=chain
                )

            if respect_robots:
                allowed, reason = self._robots.allows(current)
                if not allowed:
                    return self._failure(url, reason or "blocked by robots.txt", "robots", chain=chain)

            response = self._request_with_retries(
                current,
                verdict,
                purpose=purpose,
                accept=accept,
                limit=limit,
                allow_non_textual=allow_non_textual,
                attempts=attempts,
                timeout=timeout,
                headers=headers if urlsplit(current).hostname == origin_host else None,
            )
            if response.error and response.status == 0:
                response.url = url
                response.redirect_chain = chain
                return response

            if response.status in {301, 302, 303, 307, 308}:
                location = response.headers.get("location", "")
                if not location:
                    response.error = f"HTTP {response.status} without Location header"
                    response.error_kind = "bad_redirect"
                    response.ok = False
                    break
                chain.append(current)
                current = urljoin(current, location.strip())
                if hop == self.max_redirects:
                    return self._failure(
                        url, f"redirect limit ({self.max_redirects}) exceeded", "redirect_limit", chain=chain
                    )
                continue

            response.url = url
            response.final_url = current
            response.redirect_chain = chain
            response.elapsed_ms = int((time.monotonic() - started) * 1000)
            if capture_snapshot:
                response.snapshot = self._make_snapshot(response, source_class)
            if use_cache:
                with self._cache_lock:
                    self._cache[cache_key] = response
            return response

        response.url = url
        response.redirect_chain = chain
        response.elapsed_ms = int((time.monotonic() - started) * 1000)
        return response

    def get_json(self, url: str, **kwargs: Any) -> tuple[Any, Response]:
        response = self.get(url, accept="application/json", **kwargs)
        return (response.json() if response.ok else None), response

    def request_log(self) -> list[dict[str, Any]]:
        with self._log_lock:
            return list(self._log)

    # -- internals ------------------------------------------------------------

    def _request_with_retries(
        self,
        url: str,
        verdict: UrlVerdict,
        *,
        purpose: str,
        accept: str,
        limit: int,
        allow_non_textual: bool,
        attempts: int | None = None,
        timeout: float | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        last: Response | None = None
        max_attempts = max(1, attempts or self.max_attempts)
        for attempt in range(max_attempts):
            if self.budget.time_exhausted():
                return self._failure(url, "wall-clock budget exhausted", "budget")
            try:
                # Reserve before the request leaves the process; redirects and retries
                # are separate hops and are each charged.
                with self.budget.reserve_request(host=verdict.host, purpose=purpose):
                    self._throttle.wait(verdict.host, self._robots.crawl_delay(url))
                    response = self._perform(
                        url,
                        verdict,
                        accept=accept,
                        limit=limit,
                        allow_non_textual=allow_non_textual,
                        timeout=timeout,
                        **({"extra_headers": headers} if headers else {}),
                    )
            except BudgetExceeded as exc:
                return self._failure(url, str(exc), "budget")

            self._record(url, verdict.host, purpose, response)
            if response.status and response.status not in RETRYABLE_STATUS:
                return response
            if response.error_kind in TERMINAL_ERROR_KINDS:
                return response
            last = response
            if attempt + 1 < max_attempts:
                backoff = 0.5 * (2**attempt)
                retry_after = response.headers.get("retry-after", "")
                if retry_after.isdigit():
                    backoff = max(backoff, min(float(retry_after), 5.0))
                time.sleep(backoff)
        return last or self._failure(url, "request failed", "network")

    def _perform(
        self,
        url: str,
        verdict: UrlVerdict,
        *,
        accept: str,
        limit: int,
        allow_non_textual: bool,
        timeout: float | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> Response:
        timeout = timeout or self.timeout
        parts = urlsplit(url)
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"

        pinned = verdict.addresses[0] if verdict.addresses else verdict.host
        connection: http.client.HTTPConnection
        if verdict.scheme == "https":
            connection = _PinnedHTTPSConnection(
                verdict.host,
                verdict.port,
                pinned_ip=pinned,
                timeout=timeout,
                context=self._ssl_context,
            )
        else:
            connection = _PinnedHTTPConnection(
                verdict.host, verdict.port, pinned_ip=pinned, timeout=timeout
            )

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": accept,
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "nb-NO,no;q=0.9,en;q=0.8",
            "Connection": "close",
            **(extra_headers or {}),
        }
        try:
            connection.request("GET", path, headers=headers)
            raw = connection.getresponse()
            status = raw.status
            response_headers = {key.lower(): value for key, value in raw.getheaders()}
            content_type = (response_headers.get("content-type") or "").split(";")[0].strip().lower()

            if status in {301, 302, 303, 307, 308}:
                raw.read(4096)
                return Response(
                    ok=True,
                    url=url,
                    final_url=url,
                    status=status,
                    headers=response_headers,
                    retrieved_at=utc_now(),
                )

            skip_body = bool(
                content_type
                and not allow_non_textual
                and not any(content_type.startswith(prefix) for prefix in TEXTUAL_TYPES)
            )
            if skip_body:
                raw.read(2048)
                return Response(
                    ok=False,
                    url=url,
                    final_url=url,
                    status=status,
                    headers=response_headers,
                    content_type=content_type,
                    retrieved_at=utc_now(),
                    error=f"unsupported content type {content_type}",
                    error_kind="unsupported_media",
                )

            payload, truncated = self._read_capped(raw, limit)
            payload = self._decode_body(payload, response_headers.get("content-encoding", ""))
            digest = hashlib.sha256(payload).hexdigest()
            text = ""
            if not content_type.startswith("application/pdf"):
                text = payload.decode(self._charset(response_headers), "replace")

            return Response(
                ok=200 <= status < 300,
                url=url,
                final_url=url,
                status=status,
                headers=response_headers,
                body=payload,
                text=text,
                content_type=content_type or None,
                content_sha256=digest,
                retrieved_at=utc_now(),
                truncated=truncated,
                error=None if 200 <= status < 300 else f"HTTP {status}",
                error_kind=None if 200 <= status < 300 else "http_status",
            )
        except (socket.timeout, TimeoutError):
            return self._failure(url, "request timed out", "timeout")
        except ssl.SSLError as exc:
            return self._failure(url, f"TLS error: {type(exc).__name__}", "tls")
        except ConnectionRefusedError as exc:
            # Separated from the general network case because it is a *settled* answer:
            # the port is closed and will still be closed in half a second. Retrying it
            # spends a request from the locked 2,000 to learn nothing. The caller may
            # still try a different port on the same host, which is a new question.
            return self._failure(url, f"{type(exc).__name__}: {exc}", "refused")
        except (http.client.HTTPException, OSError) as exc:
            return self._failure(url, f"{type(exc).__name__}: {exc}", "network")
        finally:
            try:
                connection.close()
            except Exception:
                pass

    @staticmethod
    def _charset(headers: Mapping[str, str]) -> str:
        content_type = headers.get("content-type", "")
        if "charset=" in content_type:
            candidate = content_type.split("charset=")[-1].split(";")[0].strip().strip('"')
            if candidate:
                try:
                    "probe".encode(candidate)
                    return candidate
                except LookupError:
                    return "utf-8"
        return "utf-8"

    @staticmethod
    def _read_capped(raw: Any, limit: int) -> tuple[bytes, bool]:
        """Read at most `limit` bytes, so a huge response cannot exhaust the disk."""
        buffer = io.BytesIO()
        remaining = limit
        while remaining > 0:
            chunk = raw.read(min(65536, remaining))
            if not chunk:
                return buffer.getvalue(), False
            buffer.write(chunk)
            remaining -= len(chunk)
        return buffer.getvalue(), bool(raw.read(1))

    @staticmethod
    def _decode_body(payload: bytes, encoding: str) -> bytes:
        encoding = (encoding or "").lower().strip()
        try:
            if encoding == "gzip":
                return gzip.decompress(payload)
            if encoding == "deflate":
                try:
                    return zlib.decompress(payload)
                except zlib.error:
                    return zlib.decompress(payload, -zlib.MAX_WBITS)
        except (OSError, zlib.error, EOFError):
            # A truncated body cannot be decompressed; the raw bytes are still hashed
            # and recorded so the failure is visible rather than silent.
            return payload
        return payload

    def _make_snapshot(self, response: Response, source_class: str) -> Snapshot:
        stored = (
            self.snapshots.store(response.content_sha256, response.body)
            if response.body and response.content_sha256
            else None
        )
        return Snapshot(
            id=stable_id("snap", response.final_url, response.content_sha256, response.status),
            requested_url=redact_url(response.url),
            final_url=redact_url(response.final_url),
            status=response.status,
            content_type=response.content_type,
            bytes_received=len(response.body),
            content_sha256=response.content_sha256,
            retrieved_at=response.retrieved_at or utc_now(),
            source_class=source_class,
            redirect_chain=[redact_url(item) for item in response.redirect_chain],
            from_cache=response.from_cache,
            stored_path=stored,
            note="truncated at byte cap" if response.truncated else None,
        )

    def _failure(
        self, url: str, reason: str, kind: str, *, chain: list[str] | None = None
    ) -> Response:
        return Response(
            ok=False,
            url=url,
            final_url=url,
            status=0,
            retrieved_at=utc_now(),
            redirect_chain=chain or [],
            error=reason,
            error_kind=kind,
        )

    def _record(self, url: str, host: str, purpose: str, response: Response) -> None:
        with self._log_lock:
            self._log.append(
                {
                    "url": redact_url(url),
                    "host": host,
                    "purpose": purpose,
                    "status": response.status,
                    "bytes": len(response.body),
                    "error": response.error,
                    "at": response.retrieved_at,
                }
            )
