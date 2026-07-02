"""Ingest-rate governor (§4.7) — pace position ingest to host-side enrichment capacity.

Admitting a scraped position into the pipeline is **not free**: each one flows through
host-side enrichment — normalize, dedup, and (for a newly-tracked opening) LLM profile
scoring — work ``jobcrawler`` keeps no cross-run idempotency for. A burst of ingests — a
first crawl of a large board, or several sites finishing at once — would overrun that
enrichment capacity (and, when it reaches the §8 matcher, spend the daily match budget in
one spike). So ingestion is paced to ``JOBCRAWLER_INGEST_RATE`` (e.g. ``"2/s"``), a
throughput cap **distinct** from the §4.5 per-host fetch-politeness cap: that one protects
the *crawled* board, this one protects ``jobcrawler``'s own downstream enrichment.

The governor is a minimum-interval pacer: ``wait()`` blocks until the next event
is allowed. Clock and sleep are injected so the timing is deterministic in tests.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable

_RATE = re.compile(r"^\s*(\d+)\s*/\s*([smh])\s*$", re.IGNORECASE)
_SECONDS_PER = {"s": 1.0, "m": 60.0, "h": 3600.0}


def interval_for(rate: str) -> float:
    """Seconds between events for an ``"<N>/<s|m|h>"`` rate (``0.0`` = unlimited)."""
    match = _RATE.match(rate)
    if match is None:
        raise ValueError(f"invalid rate: {rate!r} (use forms like 2/s, 30/m, 1/h)")
    count = int(match.group(1))
    if count <= 0:
        return 0.0  # an explicit "0/s" disables pacing
    return _SECONDS_PER[match.group(2).lower()] / count


class RateGovernor:
    """A minimum-interval pacer; ``wait()`` blocks until the next event is allowed."""

    def __init__(
        self,
        rate: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._init(interval_for(rate), clock, sleep)

    @classmethod
    def from_interval(
        cls,
        interval: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> RateGovernor:
        """Build a governor from an explicit seconds-between-events interval.

        The §4.5 per-host pacer derives each host's interval as ``max(base rate,
        robots Crawl-delay)`` — a float, not a rate string — so it constructs its
        governors this way instead of through the ``"<N>/<unit>"`` parser.
        """
        gov = cls.__new__(cls)
        gov._init(max(0.0, interval), clock, sleep)
        return gov

    def _init(
        self,
        interval: float,
        clock: Callable[[], float],
        sleep: Callable[[float], None],
    ) -> None:
        self._interval = interval
        self._clock = clock
        self._sleep = sleep
        self._next: float | None = None  # earliest time the next event may fire
        self._lock = (
            threading.Lock()
        )  # one shared governor is hit from many crawl threads

    def wait(self) -> None:
        """Block until at least ``interval`` has elapsed since the prior event.

        The lock is held across the sleep on purpose: the §4.9 scheduler shares one
        governor across crawls running on ``asyncio.to_thread`` workers, so the
        reservation must be atomic *and* callers must serialize — admitting two at
        once would push two ingests through inside one interval and overrun the
        enrichment cap.
        """
        if self._interval <= 0:
            return
        with self._lock:
            now = self._clock()
            if self._next is not None and now < self._next:
                self._sleep(self._next - now)
                now = self._next  # logical time; robust to a sleep that under-advances
            self._next = now + self._interval
