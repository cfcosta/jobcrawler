"""Tests for the ingest-rate governor (§4.7).

Each ``POST /news`` drives 2–3 server-side LLM calls and ``bizup-news`` has no
server-side idempotency, so ingestion is paced to ``AUTOCRAWLER_INGEST_RATE``
(distinct from the §4.5 fetch cap). The governor is a minimum-interval pacer with
an injected clock + sleep, so the timing is asserted deterministically.
"""

from __future__ import annotations

import threading
import time

import pytest

from bizup_autocrawler.governor import RateGovernor, interval_for


class _Clock:
    """A fake monotonic clock whose ``sleep`` advances it (records durations)."""

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, d: float) -> None:
        self.sleeps.append(d)
        self.t += d


def _gov(rate: str, clock: _Clock) -> RateGovernor:
    return RateGovernor(rate, clock=clock.now, sleep=clock.sleep)


def test_interval_for_parses_per_second_minute_hour() -> None:
    assert interval_for("2/s") == 0.5
    assert interval_for("30/m") == 2.0
    assert interval_for("1/h") == 3600.0
    assert interval_for("4/s") == 0.25


def test_interval_for_zero_is_unlimited() -> None:
    assert interval_for("0/s") == 0.0


def test_interval_for_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        interval_for("fast")
    with pytest.raises(ValueError):
        interval_for("2/day")


def test_first_wait_never_sleeps() -> None:
    clock = _Clock()
    _gov("2/s", clock).wait()
    assert clock.sleeps == []


def test_back_to_back_waits_pace_to_the_interval() -> None:
    clock = _Clock()
    gov = _gov("2/s", clock)  # 0.5s between events
    gov.wait()  # t=0, no sleep
    gov.wait()  # nothing elapsed → sleep the full 0.5
    gov.wait()  # again 0.5
    assert clock.sleeps == [0.5, 0.5]
    assert clock.t == 1.0


def test_elapsed_work_shortens_the_sleep() -> None:
    clock = _Clock()
    gov = _gov("2/s", clock)
    gov.wait()  # t=0
    clock.t += 0.3  # the POST itself took 0.3s
    gov.wait()  # only 0.2 remains of the 0.5 interval
    assert clock.sleeps == [pytest.approx(0.2)]


def test_elapsed_past_the_interval_never_sleeps() -> None:
    clock = _Clock()
    gov = _gov("2/s", clock)
    gov.wait()
    clock.t += 5.0  # slow POST; we are already overdue
    gov.wait()
    assert clock.sleeps == []


def test_zero_rate_is_a_no_op() -> None:
    clock = _Clock()
    gov = _gov("0/s", clock)
    for _ in range(10):
        gov.wait()
    assert clock.sleeps == []


def test_wait_serializes_concurrent_callers() -> None:
    # cmd_run shares ONE governor across crawls that run on asyncio.to_thread worker
    # threads (§4.7). wait() must admit one caller at a time: an unguarded
    # read-modify-write of the next-fire time lets callers overlap and overrun the
    # news-API cap. The clock is pinned so every call past the first takes the
    # pacing branch; a small real sleep widens the window a missing lock would expose.
    active = 0
    peak = 0
    guard = threading.Lock()

    def sleep(_: float) -> None:
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        time.sleep(0.001)
        with guard:
            active -= 1

    gov = RateGovernor("100/s", clock=lambda: 0.0, sleep=sleep)  # 0.01s interval

    def hammer() -> None:
        for _ in range(10):
            gov.wait()

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert (
        peak == 1
    )  # the governor admitted exactly one caller into the pacer at a time
