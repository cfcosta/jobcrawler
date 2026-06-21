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
from typing import Literal, Protocol

from pydantic import BaseModel, field_validator

from bizup_autocrawler import config
from bizup_autocrawler.durations import parse_duration
from bizup_autocrawler.health import HealthState
from bizup_autocrawler.runtime.runner import CrawlSummary
from bizup_autocrawler.state import StateStore


class Healer(Protocol):
    """The §4.8 self-healing seam the scheduler drives on its regen timer."""

    async def assess(self, slug: str) -> HealthState: ...


class Site(BaseModel):
    """One entry of the §4.1 site registry."""

    slug: str
    url: str
    name: str = ""
    render: Literal["auto", "static", "browser", "solver"] = "auto"
    hints: str = ""
    schedule: str | None = None
    enabled: bool = True

    @field_validator("slug")
    @classmethod
    def _import_safe_slug(cls, value: str) -> str:
        # The slug names crawlers/<slug>.py, which the runner imports as a module,
        # so it must be a valid Python identifier — underscores, never hyphens.
        if not value.isidentifier():
            raise ValueError(
                f"slug {value!r} must be a valid Python identifier "
                "(it names crawlers/<slug>.py); use underscores, not hyphens"
            )
        return value

    @field_validator("schedule")
    @classmethod
    def _duration_only(cls, value: str | None) -> str | None:
        # Reject a cron expr loudly rather than silently downgrading it to the
        # default cadence — cron is deferred until the §4.9 scheduler grows it.
        if value is None:
            return value
        try:
            parse_duration(value)
        except ValueError as exc:
            raise ValueError(
                f"schedule {value!r}: cron is not supported yet (§4.9); "
                "use a duration like 30m, 6h, 1d"
            ) from exc
        return value


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


CrawlFn = Callable[[Site], Awaitable[CrawlSummary]]
Sleep = Callable[[float], Awaitable[None]]


def _regen_interval(raw: str) -> timedelta | None:
    """Parse ``AUTOCRAWLER_REGEN_CHECK``; ``off`` (or unparseable) disables it."""
    if raw.strip().lower() == "off":
        return None
    try:
        return parse_duration(raw)
    except ValueError:
        return None


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
        healer: Healer | None = None,
        regen_check: str | None = None,
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
        self._healer = healer
        self._regen_interval = _regen_interval(
            config.regen_check() if regen_check is None else regen_check
        )
        self._next_regen: datetime | None = None

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

    def regen_due(self, now: datetime) -> bool:
        """True if a §4.8 staleness sweep is due (per ``AUTOCRAWLER_REGEN_CHECK``)."""
        if self._healer is None or self._regen_interval is None:
            return False
        return self._next_regen is None or now >= self._next_regen

    def mark_regen(self, now: datetime) -> None:
        """Record that a sweep ran at ``now``; the next is one interval later."""
        if self._regen_interval is not None:
            self._next_regen = now + self._regen_interval

    async def regen_tick(self) -> dict[str, HealthState]:
        """Assess every enabled site for staleness, regenerating the stale (§4.8).

        One orchestrator drives both crawling and regeneration; the healer persists
        each resulting state (so ``list`` can surface it and a generation-failed
        site is not re-enqueued into the same failing generation).
        """
        if self._healer is None:
            return {}
        states: dict[str, HealthState] = {}
        for site in self._sites:
            if site.enabled:
                states[site.slug] = await self._healer.assess(site.slug)
        return states

    async def run(self, *, stop: asyncio.Event, interval: float = 60.0) -> None:
        """Loop ticks until ``stop`` is set; each tick drains before the next.

        The slow §4.8 regeneration sweep hangs off the same loop on its own cadence
        — never ``.compile()`` in-band (that offline GEPA work is §4.11's hand-off).
        """
        while not stop.is_set():
            await self.tick()
            now = self._clock()
            if self.regen_due(now):
                await self.regen_tick()
                self.mark_regen(now)
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
