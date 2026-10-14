"""Budget enforcement against the locked evaluator envelope.

2,000 outbound requests, 45 minutes and $10 are hard gates. Exceeding any of them
invalidates the run, so capacity is reserved before work happens rather than counted
after it — and the reservation has to be correct while many companies run at once.

Time is driven by an injected clock so these tests are deterministic and instant.
"""

from __future__ import annotations

import threading

import pytest

from signalpost.budget import (
    DEFAULT_MAX_REQUESTS,
    DEFAULT_MAX_SECONDS,
    DEFAULT_MAX_SPEND_USD,
    Budget,
    BudgetExceeded,
)


class FakeClock:
    """A manually advanced monotonic clock."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestLockedEnvelope:
    def test_defaults_match_the_published_limits(self) -> None:
        """These are quoted in the submission docs; drift would make them untrue."""
        assert DEFAULT_MAX_REQUESTS == 2000
        assert DEFAULT_MAX_SECONDS == 45 * 60
        assert DEFAULT_MAX_SPEND_USD == 10.0


class TestRequestAccounting:
    def test_capacity_is_consumed_on_success(self) -> None:
        budget = Budget(max_requests=3)
        for _ in range(3):
            with budget.reserve_request(host="data.brreg.no", purpose="registry"):
                pass
        assert budget.snapshot().requests_used == 3
        assert budget.snapshot().requests_remaining == 0

    def test_the_limit_is_refused_rather_than_overrun(self) -> None:
        budget = Budget(max_requests=1)
        with budget.reserve_request():
            pass
        with pytest.raises(BudgetExceeded) as excinfo:
            with budget.reserve_request():
                pass
        assert excinfo.value.resource == "requests"
        assert budget.snapshot().denials["requests"] == 1

    def test_a_failed_request_returns_its_reservation(self) -> None:
        """A request that never left the process must not consume the allowance."""
        budget = Budget(max_requests=2)
        with pytest.raises(RuntimeError, match="connection reset"):
            with budget.reserve_request():
                raise RuntimeError("connection reset")
        snapshot = budget.snapshot()
        assert snapshot.requests_used == 0
        assert snapshot.requests_reserved == 0
        assert snapshot.requests_remaining == 2

    def test_redirects_and_retries_are_charged_by_count(self) -> None:
        """The evaluator counts redirects and retries, so a multi-hop fetch reserves
        the hops it will make rather than one."""
        budget = Budget(max_requests=10)
        with budget.reserve_request(host="example.no", purpose="landing", count=3):
            pass
        assert budget.snapshot().requests_used == 3

    def test_usage_is_attributed_to_hosts_and_purposes(self) -> None:
        budget = Budget(max_requests=10)
        with budget.reserve_request(host="data.brreg.no", purpose="registry"):
            pass
        with budget.reserve_request(host="jactus.no", purpose="landing"):
            pass
        with budget.reserve_request(host="jactus.no", purpose="contact"):
            pass
        report = budget.snapshot().to_dict()
        assert report["requests_by_host"]["jactus.no"] == 2
        assert report["requests_by_purpose"]["registry"] == 1

    def test_a_zero_or_negative_reservation_is_rejected(self) -> None:
        budget = Budget(max_requests=10)
        with pytest.raises(ValueError):
            with budget.reserve_request(count=0):
                pass


class TestConcurrency:
    def test_the_hard_limit_holds_under_concurrent_workers(self) -> None:
        """Counting after the fact would let N workers race past the cap together."""
        limit = 50
        budget = Budget(max_requests=limit)
        granted = 0
        refused = 0
        counter_lock = threading.Lock()
        barrier = threading.Barrier(16)

        def worker() -> None:
            nonlocal granted, refused
            barrier.wait()
            for _ in range(10):
                try:
                    with budget.reserve_request(host="example.no", purpose="landing"):
                        pass
                except BudgetExceeded:
                    with counter_lock:
                        refused += 1
                else:
                    with counter_lock:
                        granted += 1

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert granted == limit
        assert refused == 160 - limit
        assert budget.snapshot().requests_used == limit

    def test_no_reservation_is_leaked_under_concurrent_failures(self) -> None:
        budget = Budget(max_requests=100)
        barrier = threading.Barrier(8)

        def worker() -> None:
            barrier.wait()
            for _ in range(5):
                try:
                    with budget.reserve_request():
                        raise RuntimeError("timeout")
                except RuntimeError:
                    pass

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        snapshot = budget.snapshot()
        assert snapshot.requests_reserved == 0
        assert snapshot.requests_used == 0


class TestWallClock:
    def test_requests_stop_when_the_run_is_out_of_time(self) -> None:
        clock = FakeClock()
        budget = Budget(max_requests=1000, max_seconds=60, clock=clock)
        with budget.reserve_request():
            pass

        clock.advance(61)
        with pytest.raises(BudgetExceeded) as excinfo:
            with budget.reserve_request():
                pass
        assert excinfo.value.resource == "time"
        assert budget.seconds_remaining() == 0.0


class TestSpend:
    def test_spend_is_refused_rather_than_overrunning_the_cap(self) -> None:
        budget = Budget(max_spend_usd=1.0)
        budget.charge(0.75, provider="search-api")
        assert not budget.can_afford(0.30)
        with pytest.raises(BudgetExceeded) as excinfo:
            budget.charge(0.30, provider="search-api")
        assert excinfo.value.resource == "spend"
        assert budget.snapshot().spend_usd == pytest.approx(0.75)

    def test_spending_exactly_the_cap_is_allowed(self) -> None:
        budget = Budget(max_spend_usd=10.0)
        budget.charge(10.0, provider="search-api")
        assert budget.snapshot().spend_usd == pytest.approx(10.0)

    def test_the_free_path_reports_zero(self) -> None:
        """The default configuration makes no chargeable calls at all."""
        assert Budget().snapshot().to_dict()["spend_usd"] == 0.0


class TestSoftStop:
    def test_discretionary_work_stops_before_the_hard_request_limit(self) -> None:
        """In-flight companies must still be able to reach a terminal envelope."""
        budget = Budget(max_requests=100, soft_ratio=0.90)
        for _ in range(89):
            with budget.reserve_request():
                pass
        assert not budget.should_stop_discretionary()
        with budget.reserve_request():
            pass
        assert budget.should_stop_discretionary()

    def test_discretionary_work_stops_before_the_wall_clock_limit(self) -> None:
        clock = FakeClock()
        budget = Budget(max_seconds=100, soft_ratio=0.90, clock=clock)
        clock.advance(89)
        assert not budget.should_stop_discretionary()
        clock.advance(2)
        assert budget.should_stop_discretionary()


class TestFairShare:
    def test_the_tail_of_the_batch_is_not_starved_by_the_head(self) -> None:
        """One company with a large site must not consume the whole allowance."""
        budget = Budget(max_requests=2000)
        assert budget.per_company_allowance(100) == 20

        for _ in range(1000):
            with budget.reserve_request():
                pass
        assert budget.per_company_allowance(50) == 20

    def test_an_exhausted_budget_still_allows_a_floor_of_one(self) -> None:
        budget = Budget(max_requests=1)
        with budget.reserve_request():
            pass
        assert budget.per_company_allowance(10) == 1
        assert budget.per_company_allowance(0) == 0


class TestRetryAttempts:
    """Retries are per call: the registry may ask more times, and every ask is charged."""

    @staticmethod
    def fetcher(outcomes: list[str], monkeypatch: pytest.MonkeyPatch) -> tuple[object, list[float]]:
        import signalpost.fetch as fetch_mod
        from signalpost.fetch import Fetcher, Response
        from signalpost.net import UrlVerdict

        monkeypatch.setattr(fetch_mod.time, "sleep", lambda _seconds: None)
        fetcher = Fetcher(Budget(max_requests=20), host_delay=0.0)
        timeouts: list[float] = []

        def perform(url, verdict, *, accept, limit, allow_non_textual, timeout=None):
            timeouts.append(timeout or fetcher.timeout)
            kind = outcomes.pop(0)
            if kind == "ok":
                return Response(ok=True, url=url, final_url=url, status=200, body=b"{}")
            return Response(ok=False, url=url, final_url=url, status=0, error=kind, error_kind=kind)

        monkeypatch.setattr(fetcher, "_perform", perform)
        verdict = UrlVerdict(url="https://data.brreg.no/x", allowed=True, scheme="https", host="data.brreg.no", port=443)
        fetcher.verdict = verdict  # type: ignore[attr-defined]
        return fetcher, timeouts

    def test_the_default_gives_up_after_two_attempts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fetcher, _ = self.fetcher(["timeout", "network", "ok"], monkeypatch)
        response = fetcher._request_with_retries(
            "https://data.brreg.no/x", fetcher.verdict, purpose="registry", accept="*/*",
            limit=1000, allow_non_textual=False,
        )
        assert not response.ok
        assert fetcher.budget.snapshot().requests_used == 2

    def test_a_call_may_ask_more_times_and_each_ask_is_charged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fetcher, timeouts = self.fetcher(["timeout", "network", "ok"], monkeypatch)
        response = fetcher._request_with_retries(
            "https://data.brreg.no/x", fetcher.verdict, purpose="registry", accept="*/*",
            limit=1000, allow_non_textual=False, attempts=4, timeout=20.0,
        )
        assert response.ok
        assert fetcher.budget.snapshot().requests_used == 3
        assert timeouts == [20.0, 20.0, 20.0]

    def test_a_terminal_failure_is_not_retried_even_with_attempts_to_spare(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fetcher, _ = self.fetcher(["refused", "ok"], monkeypatch)
        response = fetcher._request_with_retries(
            "https://data.brreg.no/x", fetcher.verdict, purpose="registry", accept="*/*",
            limit=1000, allow_non_textual=False, attempts=4,
        )
        assert response.error_kind == "refused"
        assert fetcher.budget.snapshot().requests_used == 1

    def test_the_registry_client_asks_with_its_own_allowance(self) -> None:
        from signalpost.registry import REGISTRY_ATTEMPTS, RegistryClient

        seen: dict[str, object] = {}

        class Recorder:
            def get_json(self, url: str, **kwargs: object) -> tuple[None, object]:
                seen.update(kwargs)
                from signalpost.fetch import Response

                return None, Response(ok=False, url=url, final_url=url, status=0)

        RegistryClient(Recorder())._json("https://data.brreg.no/x", "registry_entity", "official_registry")  # type: ignore[arg-type]
        assert seen["attempts"] == REGISTRY_ATTEMPTS > 2
