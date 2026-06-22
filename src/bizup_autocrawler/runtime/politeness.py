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
from urllib import robotparser
from urllib.parse import urljoin

from bizup_autocrawler.governor import RateGovernor, interval_for
from bizup_autocrawler.urls import normalize_host


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


class RobotsDisallowedError(RuntimeError):
    """A fetch was refused because robots.txt disallows the path (§4.5)."""


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


class RobotsPolicy:
    """robots.txt compliance for the §4.5 fetch path: Disallow + Crawl-delay.

    A site's ``/robots.txt`` is fetched once per host (through the injected
    ``fetch_text``, so it rides the same SSRF-checked client) and parsed with the
    stdlib :mod:`urllib.robotparser`. The policy **fails open**: a site with no
    robots.txt, or one we can't fetch, is treated as fully crawlable — the web
    convention, and the safe choice for availability (a transient robots fetch
    error must not silently halt a crawl). The per-host parser is cached so a run
    over many pages of one site costs a single robots fetch.
    """

    def __init__(
        self, fetch_text: Callable[[str], str | None], *, user_agent: str
    ) -> None:
        self._fetch_text = fetch_text
        self._ua = user_agent
        self._lock = threading.Lock()
        self._cache: dict[str, robotparser.RobotFileParser] = {}

    def allowed(self, url: str) -> bool:
        """Whether our user-agent may fetch ``url`` per the host's robots.txt."""
        return self._parser_for(url).can_fetch(self._ua, url)

    def crawl_delay(self, url: str) -> float | None:
        """The host's ``Crawl-delay`` for our user-agent (seconds), or None."""
        delay = self._parser_for(url).crawl_delay(self._ua)
        return float(delay) if delay is not None else None

    def _parser_for(self, url: str) -> robotparser.RobotFileParser:
        host = normalize_host(url)
        with self._lock:
            cached = self._cache.get(host)
        if cached is not None:
            return cached
        parser = self._load(url)  # fetch + parse outside the lock (it may do network)
        with self._lock:
            # A concurrent thread may have loaded the same host first; first wins.
            return self._cache.setdefault(host, parser)

    def _load(self, url: str) -> robotparser.RobotFileParser:
        parser = robotparser.RobotFileParser()
        try:
            text = self._fetch_text(urljoin(url, "/robots.txt"))
        except Exception:
            text = None  # unreachable robots.txt → fail open (allow), per convention
        # No robots.txt (or unfetchable) parses as empty lines, which the parser
        # treats as "no rules" → everything allowed; a present file is parsed for
        # its Disallow / Crawl-delay rules. (Parsing empty avoids poking the parser's
        # private allow_all flag, which the type stubs don't expose.)
        parser.parse((text or "").splitlines())
        return parser
