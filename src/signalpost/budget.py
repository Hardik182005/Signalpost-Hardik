"""Hard budget enforcement for the locked evaluator envelope.

The evaluator run is capped at 2,000 outbound requests (redirects and retries
included), 45 minutes of wall clock and $10 of third-party spend. Those are hard
gates, not targets, so this module reserves capacity *before* work happens rather
than counting it afterwards. Every reservation is taken under a single lock, which
makes the accounting correct when the pipeline runs many companies concurrently.

A reservation that is never consumed must be released, so `reserve_request()` is a
context manager that returns capacity on failure and books it on success.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

# Locked evaluator envelope (docs/provenance/eval-harness.md).
DEFAULT_MAX_REQUESTS = 2000
DEFAULT_MAX_SECONDS = 45 * 60
DEFAULT_MAX_SPEND_USD = 10.0

# Stop issuing new discretionary work once this much of a budget is gone, so that
# in-flight companies can still finish and every input reaches a terminal state.
DEFAULT_SOFT_RATIO = 0.90


class BudgetExceeded(RuntimeError):
    """Raised when a reservation cannot be satisfied within the locked envelope."""

    def __init__(self, resource: str, detail: str) -> None:
        super().__init__(f"budget exhausted: {resource} ({detail})")
        self.resource = resource
        self.detail = detail


@dataclass
class BudgetSnapshot:
    """Immutable view of budget state, safe to serialise into an envelope."""

    requests_used: int
    requests_reserved: int
    max_requests: int
    seconds_elapsed: float
    max_seconds: float
    spend_usd: float
    max_spend_usd: float
    denials: dict[str, int] = field(default_factory=dict)
    by_host: dict[str, int] = field(default_factory=dict)
    by_purpose: dict[str, int] = field(default_factory=dict)

    @property
    def requests_remaining(self) -> int:
        return max(0, self.max_requests - self.requests_used - self.requests_reserved)

    @property
    def seconds_remaining(self) -> float:
        return max(0.0, self.max_seconds - self.seconds_elapsed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "requests_used": self.requests_used,
            "requests_reserved": self.requests_reserved,
            "requests_remaining": self.requests_remaining,
            "max_requests": self.max_requests,
            "seconds_elapsed": round(self.seconds_elapsed, 3),
            "seconds_remaining": round(self.seconds_remaining, 3),
            "max_seconds": self.max_seconds,
            "spend_usd": round(self.spend_usd, 6),
            "max_spend_usd": self.max_spend_usd,
            "denials": dict(self.denials),
            "requests_by_host": dict(sorted(self.by_host.items(), key=lambda kv: -kv[1])),
            "requests_by_purpose": dict(
                sorted(self.by_purpose.items(), key=lambda kv: -kv[1])
            ),
        }


class Budget:
    """Thread-safe request, time and spend governor."""

    def __init__(
        self,
        *,
        max_requests: int = DEFAULT_MAX_REQUESTS,
        max_seconds: float = DEFAULT_MAX_SECONDS,
        max_spend_usd: float = DEFAULT_MAX_SPEND_USD,
        soft_ratio: float = DEFAULT_SOFT_RATIO,
        clock: Any = time.monotonic,
    ) -> None:
        if max_requests < 0 or max_seconds <= 0 or max_spend_usd < 0:
            raise ValueError("budget limits must be non-negative, with positive time")
        self.max_requests = int(max_requests)
        self.max_seconds = float(max_seconds)
        self.max_spend_usd = float(max_spend_usd)
        self.soft_ratio = float(soft_ratio)
        self._clock = clock
        self._started = clock()
        self._lock = threading.Lock()
        self._used = 0
        self._reserved = 0
        self._spend = 0.0
        self._denials: dict[str, int] = {}
        self._by_host: dict[str, int] = {}
        self._by_purpose: dict[str, int] = {}

    # -- time -----------------------------------------------------------------

    def elapsed(self) -> float:
        return max(0.0, self._clock() - self._started)

    def seconds_remaining(self) -> float:
        return max(0.0, self.max_seconds - self.elapsed())

    def time_exhausted(self) -> bool:
        return self.elapsed() >= self.max_seconds

    # -- soft stop ------------------------------------------------------------

    def should_stop_discretionary(self) -> bool:
        """True once optional enrichment must stop so in-flight work can finish.

        Mandatory work (finishing a company that has already started, writing its
        terminal envelope) is still allowed until the hard limits.
        """
        with self._lock:
            requests_spent = self._used + self._reserved
        if self.max_requests and requests_spent >= self.max_requests * self.soft_ratio:
            return True
        if self.elapsed() >= self.max_seconds * self.soft_ratio:
            return True
        if self.max_spend_usd and self._spend >= self.max_spend_usd * self.soft_ratio:
            return True
        return False

    # -- requests -------------------------------------------------------------

    @contextmanager
    def reserve_request(
        self,
        *,
        host: str = "unknown",
        purpose: str = "unspecified",
        count: int = 1,
    ) -> Iterator[None]:
        """Reserve request capacity for the duration of the block.

        On a clean exit the reservation is converted to usage. On an exception the
        capacity is returned, because a request that never left the process must not
        consume the evaluator's allowance.
        """
        self._acquire(count, purpose=purpose)
        booked = False
        try:
            yield
            self._commit(count, host=host, purpose=purpose)
            booked = True
        finally:
            if not booked:
                self._release(count)

    def _acquire(self, count: int, *, purpose: str) -> None:
        if count <= 0:
            raise ValueError("reservation count must be positive")
        if self.time_exhausted():
            self._record_denial("time")
            raise BudgetExceeded(
                "time", f"{self.elapsed():.1f}s elapsed of {self.max_seconds:.0f}s"
            )
        with self._lock:
            if self._used + self._reserved + count > self.max_requests:
                self._denials["requests"] = self._denials.get("requests", 0) + 1
                raise BudgetExceeded(
                    "requests",
                    f"{self._used + self._reserved}/{self.max_requests} already committed,"
                    f" need {count} for {purpose}",
                )
            self._reserved += count

    def _commit(self, count: int, *, host: str, purpose: str) -> None:
        with self._lock:
            self._reserved -= count
            self._used += count
            self._by_host[host] = self._by_host.get(host, 0) + count
            self._by_purpose[purpose] = self._by_purpose.get(purpose, 0) + count

    def _release(self, count: int) -> None:
        with self._lock:
            self._reserved = max(0, self._reserved - count)

    def _record_denial(self, resource: str) -> None:
        with self._lock:
            self._denials[resource] = self._denials.get(resource, 0) + 1

    # -- spend ----------------------------------------------------------------

    def charge(self, amount_usd: float, *, provider: str = "unknown") -> None:
        """Book third-party spend. Refuses rather than silently overrunning $10."""
        if amount_usd < 0:
            raise ValueError("spend must be non-negative")
        with self._lock:
            if self._spend + amount_usd > self.max_spend_usd + 1e-9:
                self._denials["spend"] = self._denials.get("spend", 0) + 1
                raise BudgetExceeded(
                    "spend",
                    f"${self._spend:.4f} used, ${amount_usd:.4f} requested by {provider},"
                    f" cap ${self.max_spend_usd:.2f}",
                )
            self._spend += amount_usd

    def can_afford(self, amount_usd: float) -> bool:
        with self._lock:
            return self._spend + amount_usd <= self.max_spend_usd + 1e-9

    # -- reporting ------------------------------------------------------------

    def snapshot(self) -> BudgetSnapshot:
        with self._lock:
            return BudgetSnapshot(
                requests_used=self._used,
                requests_reserved=self._reserved,
                max_requests=self.max_requests,
                seconds_elapsed=self.elapsed(),
                max_seconds=self.max_seconds,
                spend_usd=self._spend,
                max_spend_usd=self.max_spend_usd,
                denials=dict(self._denials),
                by_host=dict(self._by_host),
                by_purpose=dict(self._by_purpose),
            )

    def per_company_allowance(self, remaining_companies: int) -> int:
        """Fair share of the remaining request budget for one company.

        Used to stop an early company with a large website from starving the tail of
        the batch, which would fail the "exactly 100 terminal envelopes" gate.
        """
        if remaining_companies <= 0:
            return 0
        snapshot = self.snapshot()
        return max(1, snapshot.requests_remaining // remaining_companies)
