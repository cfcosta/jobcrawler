"""The async scheduler / orchestrator — owns the run loop (§4.9).

``bizup-autocrawler`` runs as a long-running service that operates the whole
crawl loop itself rather than relying on external cron. The scheduler:

  * **due-selection** — each site has a cadence (its ``schedule`` else the global
    default); a site is due when that cadence has elapsed since its last *finished*
    run (run history lives in the state store, so the loop is crash-safe and
    restart-idempotent);
  * **bounded concurrency** — a global semaphore caps how many sites crawl at once;
  * **no self-overlap** — a site already in flight (or with an unfinished last run)
    is not re-dispatched;
  * **backoff retry** — a crawl that fails before doing any work gets a small
    bounded backoff+jitter retry here; persistent failure is dropped (a §4.8
    health signal) rather than stalling the queue. Transient *fetch* errors belong
    to the politeness layer and *ingest* failures to §4.7's next-crawl re-attempt —
    neither is scheduler-retried.

Clock, the per-site ``crawl`` coroutine, and ``sleep`` are injected for
deterministic tests. The §4.8 regeneration and §4.11 re-optimization timers hang
off the same loop and land with those features; ``compile()`` never runs in-band.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

from bizup_autocrawler import config
from bizup_autocrawler.durations import parse_duration
from bizup_autocrawler.runtime.runner import CrawlSummary
from bizup_autocrawler.state import StateStore


class Site(BaseModel):
    """One entry of the §4.1 site registry."""

    slug: str
    url: str
    name: str = ""
    render: str = "auto"
    hints: str = ""
    schedule: str | None = None
    enabled: bool = True


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


CrawlFn = Callable[[Site], Awaitable[CrawlSummary]]
Sleep = Callable[[float], Awaitable[None]]


class Scheduler:
    def __init__(
        self,
        sites: Iterable[Site],
        *,
        store: StateStore,
        crawl: CrawlFn,
        concurrency: int | None = None,
        default_schedule: str | None = None,
        clock: Callable[[], datetime] = _utcnow,
        sleep: Sleep = asyncio.sleep,
        max_retries: int = 2,
        backoff_base: float = 1.0,
        jitter: Callable[[], float] | None = None,
    ) -> None:
        self._sites = list(sites)
        self._store = store
        self._crawl = crawl
        self._sem = asyncio.Semaphore(concurrency or config.concurrency())
        self._default = default_schedule or config.default_schedule()
        self._clock = clock
        self._sleep = sleep
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._jitter = jitter or (lambda: random.uniform(0, 0.5))
        self._inflight: set[str] = set()

    def due_sites(self) -> list[Site]:
        return [
            s
            for s in self._sites
            if s.enabled and s.slug not in self._inflight and self._is_due(s)
        ]

    def _is_due(self, site: Site) -> bool:
        last = self._store.last_run(site.slug)
        if last is None:
            return True
        if last.finished is None:
            return False  # in-flight or crashed mid-run; don't double-dispatch
        return self._clock() >= last.finished + self._cadence(site)

    def _cadence(self, site: Site) -> timedelta:
        raw = site.schedule or self._default
        try:
            return parse_duration(raw)
        except ValueError:
            return parse_duration(self._default)  # cron exprs deferred to a later v

    async def tick(self) -> list[CrawlSummary]:
        """Dispatch every currently-due site (bounded), returning the successes."""
        due = self.due_sites()
        results = await asyncio.gather(
            *(self._dispatch(s) for s in due), return_exceptions=True
        )
        return [r for r in results if isinstance(r, CrawlSummary)]

    async def run(self, *, stop: asyncio.Event, interval: float = 60.0) -> None:
        """Loop ticks until ``stop`` is set; each tick drains before the next."""
        while not stop.is_set():
            await self.tick()
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    async def _dispatch(self, site: Site) -> CrawlSummary:
        self._inflight.add(site.slug)
        try:
            async with self._sem:
                return await self._run_with_retry(site)
        finally:
            self._inflight.discard(site.slug)

    async def _run_with_retry(self, site: Site) -> CrawlSummary:
        for attempt in range(self._max_retries + 1):
            try:
                return await self._crawl(site)
            except Exception:
                if attempt == self._max_retries:
                    raise
                await self._sleep(self._backoff_base * (2**attempt) + self._jitter())
        raise RuntimeError("unreachable")  # pragma: no cover
