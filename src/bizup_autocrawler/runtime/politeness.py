"""The §4.5 politeness layer: per-host fetch pacing and robots.txt compliance.

A generated crawler declares *what* to fetch; ``ctx`` decides *how*, and part of
"how" is being a good citizen of the crawled site — distinct from the §4.7 ingest
governor, which protects the *news* API. Two mechanisms live here:

  * **Per-host pacing** (``PerHostPacer``) — a minimum interval between fetches of
    the *same* host (``AUTOCRAWLER_RATE_LIMIT``, e.g. ``"1/s/host"``). Each host
    gets its own pacer so a slow host never throttles a fast one.
  * **robots.txt** (``RobotsPolicy``, added with §4.5 compliance) — Disallow gating
    and a ``Crawl-delay`` that raises a host's pacing floor.

Both are injected into ``Ctx`` behind small Protocols so the fetch boundary stays
decoupled and the bulk of the runtime tests construct a bare ``Ctx`` unaffected.
The production assembly (solver + clearance + pacer + robots + proxy) is the
live-Ctx factory's job.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Protocol

from bizup_autocrawler.governor import RateGovernor, interval_for


def host_rate_interval(rate: str) -> float:
    """Seconds between fetches of one host for an ``"<N>/<unit>/host"`` rate.

    The per-host politeness rate carries a ``/host`` scope suffix the §4.7 rate
    parser doesn't know; strip it (tolerating a bare ``"2/s"``) and reuse the same
    grammar. ``0`` disables pacing.
    """
    core = rate.strip()
    if core.lower().endswith("/host"):
        core = core[: -len("/host")].rstrip().rstrip("/")
    return interval_for(core)


class Pacer(Protocol):
    """The per-host throttle seam ``Ctx`` calls before each network fetch."""

    def wait(self, host: str, *, min_interval: float | None = None) -> None: ...


class Robots(Protocol):
    """The robots.txt seam ``Ctx`` consults before fetching a URL (§4.5)."""

    def allowed(self, url: str) -> bool: ...

    def crawl_delay(self, url: str) -> float | None: ...


class PerHostPacer:
    """Throttle fetches to a minimum interval *per host* (§4.5).

    One :class:`RateGovernor` per host, created on first sight, so hosts pace
    independently. A host's interval is ``max(base rate, Crawl-delay)``: robots can
    *slow* us below our configured rate but never speed us past it. The first
    per-host interval seen wins (a site's Crawl-delay is stable within a run).
    """

    def __init__(
        self,
        rate: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base = host_rate_interval(rate)
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._governors: dict[str, RateGovernor] = {}

    def wait(self, host: str, *, min_interval: float | None = None) -> None:
        """Block until this host's interval has elapsed since its prior fetch."""
        with self._lock:
            gov = self._governors.get(host)
            if gov is None:
                interval = max(self._base, min_interval or 0.0)
                gov = RateGovernor.from_interval(
                    interval, clock=self._clock, sleep=self._sleep
                )
                self._governors[host] = gov
        gov.wait()  # the governor holds its own lock across the sleep
