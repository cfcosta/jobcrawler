"""Tests for the §4.5 politeness layer: per-host pacing and robots.txt.

The pacer throttles fetches *per crawled host* (distinct from the §4.7 ingest
governor, which protects the news API); robots.txt compliance gates which paths
may be fetched and can raise a host's pacing floor via ``Crawl-delay``. Clock and
sleep are injected so the pacing is asserted deterministically, and robots.txt is
fed through an injected fetcher so no real network is touched.
"""

from __future__ import annotations

from bizup_autocrawler.runtime.politeness import PerHostPacer, host_rate_interval


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


def _pacer(rate: str, clock: _Clock) -> PerHostPacer:
    return PerHostPacer(rate, clock=clock.now, sleep=clock.sleep)


# -- rate parsing ------------------------------------------------------------


def test_host_rate_interval_strips_the_host_scope() -> None:
    assert host_rate_interval("1/s/host") == 1.0
    assert host_rate_interval("2/s/host") == 0.5
    assert host_rate_interval("30/m/host") == 2.0


def test_host_rate_interval_tolerates_a_bare_rate() -> None:
    # An operator may drop the "/host" suffix; treat "2/s" the same.
    assert host_rate_interval("2/s") == 0.5


# -- per-host pacing ---------------------------------------------------------


def test_first_fetch_of_a_host_never_sleeps() -> None:
    clock = _Clock()
    _pacer("1/s/host", clock).wait("a.com")
    assert clock.sleeps == []


def test_back_to_back_fetches_of_one_host_pace_to_the_interval() -> None:
    clock = _Clock()
    pacer = _pacer("1/s/host", clock)  # 1.0s between fetches of a host
    pacer.wait("a.com")  # t=0, free
    pacer.wait("a.com")  # nothing elapsed → sleep the full 1.0
    assert clock.sleeps == [1.0]


def test_hosts_are_paced_independently() -> None:
    # One slow host must not throttle a different one — each host has its own pacer.
    clock = _Clock()
    pacer = _pacer("1/s/host", clock)
    pacer.wait("a.com")  # first for a → free
    pacer.wait("b.com")  # first for b → free (independent)
    assert clock.sleeps == []


def test_crawl_delay_raises_a_hosts_floor() -> None:
    # robots Crawl-delay (5s) is slower than the base 1/s; the host paces to 5s.
    clock = _Clock()
    pacer = _pacer("1/s/host", clock)
    pacer.wait("a.com", min_interval=5.0)  # first → free, but pins the host to 5s
    pacer.wait("a.com", min_interval=5.0)
    assert clock.sleeps == [5.0]


def test_base_rate_wins_when_crawl_delay_is_smaller() -> None:
    # A Crawl-delay below our own base rate never speeds us up past the base.
    clock = _Clock()
    pacer = _pacer("1/s/host", clock)
    pacer.wait("a.com", min_interval=0.1)
    pacer.wait("a.com", min_interval=0.1)
    assert clock.sleeps == [1.0]  # base 1.0s, not the 0.1s hint


def test_unlimited_rate_never_sleeps() -> None:
    clock = _Clock()
    pacer = _pacer("0/s/host", clock)
    for _ in range(5):
        pacer.wait("a.com")
    assert clock.sleeps == []
