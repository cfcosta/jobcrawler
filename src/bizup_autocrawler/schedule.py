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
import logging
import random
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime, timedelta, timezone
from typing import Literal, Protocol

from pydantic import BaseModel, field_validator, model_validator

from bizup_autocrawler import config
from bizup_autocrawler.adaptive import next_interval
from bizup_autocrawler.durations import parse_duration
from bizup_autocrawler.health import HealthState
from bizup_autocrawler.runtime.runner import CrawlSummary
from bizup_autocrawler.state import StateStore

_log = logging.getLogger("bizup_autocrawler.schedule")


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
    adaptive: bool = False
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
        # The ``adaptive`` sentinel passes through here; ``_resolve_adaptive``
        # below turns it into the adaptive flag.
        if value is None or value == "adaptive":
            return value
        try:
            parse_duration(value)
        except ValueError as exc:
            raise ValueError(
                f"schedule {value!r}: cron is not supported yet (§4.9); "
                "use a duration like 30m, 6h, 1d"
            ) from exc
        return value

    @model_validator(mode="after")
    def _resolve_adaptive(self) -> Site:
        # ``schedule: adaptive`` is sugar for the §4.9 hit-rate controller — it
        # raises the adaptive flag and clears the fixed cadence, since the
        # controller owns the interval (learned + persisted per-site). Setting
        # ``adaptive: true`` directly works too; the two forms converge here.
        if self.schedule == "adaptive":
            self.adaptive = True
            self.schedule = None
        return self


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


CrawlFn = Callable[[Site], Awaitable[CrawlSummary]]
Sleep = Callable[[float], Awaitable[None]]


def _optional_interval(raw: str) -> timedelta | None:
    """Parse an ``AUTOCRAWLER_*`` duration; ``off`` (or unparseable) → None (disabled)."""
    if raw.strip().lower() == "off":
        return None
    try:
        return parse_duration(raw)
    except ValueError:
        return None


def _regen_interval(raw: str) -> timedelta | None:
    """Parse ``AUTOCRAWLER_REGEN_CHECK``; ``off`` (or unparseable) disables it."""
    return _optional_interval(raw)


def _state_summary(states: dict[str, HealthState]) -> str:
    """A compact ``healthy=2 pending_review=1`` tally for the regen sweep log line."""
    counts = Counter(state.value for state in states.values())
    return " ".join(f"{name}={counts[name]}" for name in sorted(counts))


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
        heal_cooldown: str | None = None,
        body_quality_floor: float | None = None,
        min_schedule: float | None = None,
        max_schedule: float | None = None,
        adaptive_window: int | None = None,
        adaptive_target_n: float | None = None,
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
        # §4.8 event-driven healing: a drifted crawl assesses immediately (not waiting
        # for the periodic sweep), rate-limited per-site by this cooldown (None = off).
        self._heal_cooldown = _optional_interval(
            config.heal_cooldown() if heal_cooldown is None else heal_cooldown
        )
        self._body_quality_floor = (
            body_quality_floor
            if body_quality_floor is not None
            else config.body_quality_floor()
        )
        self._last_heal: dict[str, datetime] = {}
        # §4.9 adaptive cadence bounds/tuning (host-side; never sent to the generator).
        self._min_schedule = (
            min_schedule if min_schedule is not None else config.min_schedule()
        )
        self._max_schedule = (
            max_schedule if max_schedule is not None else config.max_schedule()
        )
        self._adaptive_window = (
            adaptive_window if adaptive_window is not None else config.adaptive_window()
        )
        target_n = (
            adaptive_target_n
            if adaptive_target_n is not None
            else config.adaptive_target_n()
        )
        self._target_hit_rate = 1.0 / target_n

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
        if site.adaptive:
            secs = self._store.get_interval(site.slug)
            if secs is None:
                secs = self._adaptive_seed()
            return timedelta(seconds=secs)
        raw = site.schedule or self._default
        try:
            return parse_duration(raw)
        except ValueError:
            return parse_duration(self._default)  # cron exprs deferred to a later v

    def _adaptive_seed(self) -> float:
        """Starting interval (seconds) for an adaptive site with no learned one yet.

        Seeds from the fixed default cadence, clamped into the politeness/staleness
        band, so a freshly-added adaptive site behaves like a default-scheduled one
        until the §4.9 controller accumulates enough crawls to tune it.
        """
        try:
            seed = parse_duration(self._default).total_seconds()
        except ValueError:
            seed = self._min_schedule
        return max(self._min_schedule, min(self._max_schedule, seed))

    def _update_cadence(self, site: Site) -> None:
        """Recompute and persist an adaptive site's interval from recent outcomes.

        The §4.9 hit-rate controller reads the last ``adaptive_window`` runs (a "hit"
        is a crawl that found ≥1 new item) and steers the interval toward checking the
        source ``target_n`` times per post, bounded by ``[min, max]_schedule``. Called
        once per *completed* crawl in ``tick``; a no-op for fixed-cadence sites.
        """
        if not site.adaptive:
            return
        history = self._store.runs_for(site.slug, limit=self._adaptive_window)
        hits = [
            r.new_items > 0 for r in history
        ]  # newest-first, matching next_interval
        current = self._store.get_interval(site.slug)
        if current is None:
            current = self._adaptive_seed()
        nxt = next_interval(
            current,
            hits,
            floor_secs=self._min_schedule,
            ceiling_secs=self._max_schedule,
            target_hit_rate=self._target_hit_rate,
            window=self._adaptive_window,
        )
        if nxt != current:
            _log.info(
                "adaptive cadence slug=%s %.0fs -> %.0fs (window=%d hits=%d/%d)",
                site.slug,
                current,
                nxt,
                self._adaptive_window,
                sum(hits[: self._adaptive_window]),
                min(len(hits), self._adaptive_window),
            )
        self._store.set_interval(site.slug, nxt)

    def _drift_signal(self, summary: CrawlSummary) -> bool:
        """True if this crawl's outcome looks like §4.8 drift worth assessing now.

        Two host-side signals, computed straight from the run counters: a blackout
        (zero items found) or a body-quality collapse (items came back but a large
        share fell below the completeness bar — teasers/paywalls). ``body_quality`` is
        ``(found - incomplete) / found``, matching the runner's persisted signal.
        """
        if summary.items_found == 0:
            return True
        body_quality = (summary.items_found - summary.incomplete) / summary.items_found
        return body_quality < self._body_quality_floor

    def _should_event_heal(self, summary: CrawlSummary, now: datetime) -> bool:
        """True if ``summary`` warrants an immediate heal and the site is off cooldown."""
        if self._healer is None or self._heal_cooldown is None:
            return False  # no healer, or event-driven healing disabled (cooldown off)
        if not self._drift_signal(summary):
            return False
        last = self._last_heal.get(summary.site)
        return last is None or now - last >= self._heal_cooldown

    async def _event_heal(self, slugs: list[str]) -> dict[str, HealthState]:
        """Assess drifted sites immediately (§4.8 event path), isolating each failure.

        Mirrors :meth:`regen_tick`'s robustness — one site's regenerate raising (LM /
        Deno / network) must not abort the tick — but is triggered by a bad crawl
        rather than the periodic timer, so drift is acted on within one cadence.
        """
        healer = self._healer
        if healer is None:  # pragma: no cover - gated by _should_event_heal
            return {}
        states: dict[str, HealthState] = {}
        for slug in slugs:
            try:
                states[slug] = await healer.assess(slug)
            except Exception:
                _log.exception("event-driven heal failed for %s; skipping", slug)
        if states:
            _log.info("event heal assessed=%d %s", len(states), _state_summary(states))
        return states

    async def tick(self) -> list[CrawlSummary]:
        """Dispatch every currently-due site (bounded), returning the successes.

        Each dispatched crawl logs its outcome (the daemon's operational heartbeat,
        §4.9): item counts at INFO on success, a WARNING when a site is dropped after
        exhausting its retries so the lost work isn't invisible. A crawl that comes back
        drifted (blackout or body-quality collapse) triggers an immediate §4.8 heal,
        rate-limited per-site by the heal cooldown.
        """
        due = self.due_sites()
        if due:
            _log.info(
                "tick dispatching n=%d sites=%s",
                len(due),
                ",".join(s.slug for s in due),
            )
        results = await asyncio.gather(
            *(self._dispatch(s) for s in due), return_exceptions=True
        )
        summaries: list[CrawlSummary] = []
        to_heal: list[str] = []
        now = self._clock()
        for site, result in zip(due, results):
            if isinstance(result, CrawlSummary):
                _log.info(
                    "crawl ok slug=%s found=%d new=%d pages=%d errors=%d",
                    result.site,
                    result.items_found,
                    result.new_items,
                    result.pages_fetched,
                    result.errors,
                )
                summaries.append(result)
                # Retune an adaptive site's interval from this (now-persisted) crawl's
                # outcome plus its recent history (§4.9); a no-op for fixed sites.
                self._update_cadence(site)
                if self._should_event_heal(result, now):
                    self._last_heal[site.slug] = now  # start the per-site cooldown
                    to_heal.append(site.slug)
            else:
                # Persistent failure after the bounded retries — dropped as a §4.8
                # health signal (the regen sweep handles it), but surfaced, not silent.
                _log.warning(
                    "crawl dropped slug=%s after retries: %r", site.slug, result
                )
        if to_heal:
            await self._event_heal(to_heal)
        return summaries

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
            if not site.enabled:
                continue
            try:
                states[site.slug] = await self._healer.assess(site.slug)
            except Exception:
                # One site's regenerate (LM / Deno / network) raising must not abort
                # the sweep or the daemon — isolate it, log, and assess the rest (§4.8).
                _log.exception("regen sweep failed for %s; skipping", site.slug)
        if states:
            _log.info("regen sweep assessed=%d %s", len(states), _state_summary(states))
        return states

    async def run(self, *, stop: asyncio.Event, interval: float = 60.0) -> None:
        """Loop ticks until ``stop`` is set; each tick drains before the next.

        The slow §4.8 regeneration sweep hangs off the same loop on its own cadence
        — never ``.compile()`` in-band (that offline GEPA work is §4.11's hand-off).
        """
        _log.info(
            "scheduler starting sites=%d interval=%ss regen=%s",
            len(self._sites),
            interval,
            "off" if self._regen_interval is None else self._regen_interval,
        )
        while not stop.is_set():
            try:
                await self.tick()
                now = self._clock()
                if self.regen_due(now):
                    await self.regen_tick()
                    self.mark_regen(now)
            except Exception:
                # Last-resort guard: an unexpected error in a tick or sweep must not
                # terminate the daemon — log it and carry on to the next interval.
                _log.exception("scheduler loop iteration failed; continuing")
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
