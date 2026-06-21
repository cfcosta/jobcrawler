"""Ingest-rate governor (§4.7) — pace ``POST /news`` to news-API LLM capacity.

Each ``POST /news`` is **not free**: it drives 2–3 server-side LLM calls
(summarize + classify, with one classify retry) and ``bizup-news`` has no
server-side idempotency. A burst of ingests — a first crawl of a large feed, or
several sites finishing at once — would overrun the news API's enrichment
capacity. So ingestion is paced to ``AUTOCRAWLER_INGEST_RATE`` (e.g. ``"2/s"``),
a throughput cap **distinct** from the §4.5 per-host fetch-politeness cap (that
one protects the *crawled* site; this one protects the *news* service).

The governor is a minimum-interval pacer: ``wait()`` blocks until the next event
is allowed. Clock and sleep are injected so the timing is deterministic in tests.
"""

from __future__ import annotations

import re
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
        self._interval = interval_for(rate)
        self._clock = clock
        self._sleep = sleep
        self._next: float | None = None  # earliest time the next event may fire

    def wait(self) -> None:
        """Block until at least ``interval`` has elapsed since the prior event."""
        if self._interval <= 0:
            return
        now = self._clock()
        if self._next is not None and now < self._next:
            self._sleep(self._next - now)
            now = self._next  # logical time; robust to a sleep that under-advances
        self._next = now + self._interval
