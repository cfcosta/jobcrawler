"""The async scheduler / orchestrator — owns the run loop (§4.9).

``jobcrawler`` runs as a long-running service that operates the whole
job-board crawl loop itself rather than relying on external cron. The scheduler:

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
  * **recheck** — a §4.9 timer re-verifies the known-open positions of windowed
    sources (``enumerates_all=False``) and closes the ones that 404/410 or show an
    expired-posting marker, plus fleet-wide expiry-based closure. Rechecking is
    *observation* of the board, not self-modification, so — like adaptive cadence —
    it is exempt from the master autonomy brake.

Clock, the per-site ``crawl`` coroutine, and ``sleep`` are injected for
deterministic tests. The §4.8 regeneration, §4.11 re-optimization, and §4.9 recheck
timers hang off the same loop on their own cadences; ``compile()`` never runs
in-band. The host-side profile :class:`~jobcrawler.match.Matcher` is built once at
startup (:func:`build_matcher`) and threaded into the crawl pipeline — absent a
``PROFILE.md`` the daemon runs matching-disabled rather than refusing to boot.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, field_validator, model_validator

if TYPE_CHECKING:  # deferred: importing the matcher pulls dspy, so keep it out of boot
    from jobcrawler.match import Matcher

from jobcrawler import config
from jobcrawler.adaptive import next_interval
from jobcrawler.durations import parse_duration
from jobcrawler.health import HealthState
from jobcrawler.profile import ProfileNotFound, load_profile
from jobcrawler.runtime.runner import CrawlSummary
from jobcrawler.state import StateStore

_log = logging.getLogger("jobcrawler.schedule")


class Healer(Protocol):
    """The §4.8 self-healing seam the scheduler drives on its regen timer."""

    async def assess(self, slug: str) -> HealthState: ...


class _MatchPacer(Protocol):
    """The rate-pacing seam the §8 matcher expects (structural; matches match._Governor).

    :func:`build_matcher` types the injected match-rate governor by shape rather than
    importing :mod:`jobcrawler.match`'s private protocol, keeping this module free of the
    dspy-heavy matcher import until a profile actually exists.
    """

    def pace(self) -> None: ...


# The §4.9 recheck fetch seam: given a known-open position URL, return
# ``(http_status, page_text)`` from a politeness-layer GET — the SAME per-host pacer +
# robots the crawl path uses (§4.5). Injected so the daemon wires the real host-side
# fetch and tests inject a fake. A transient network error must RAISE (the recheck loop
# isolates and skips it — a blip is never a closure signal); only a definitive 404/410 or
# an expired-marker hit in the returned text closes the position.
RecheckFetch = Callable[[str], Awaitable[tuple[int, str]]]

# The §4.7 match-drain seam: one drain pass — reload PROFILE.md (a hash change requeues
# the open backlog), then re-present parked pending rows to the matcher straight from
# the store (descriptions are persisted, so no crawl round-trip). Returns the pass's
# summary counters for the tick log. Injected as a whole because the matcher lives in
# the daemon wiring (cli), not in the scheduler: the closure owns the matcher cell and
# the profile reload; the scheduler owns only the cadence.
MatchDrain = Callable[[], Awaitable[dict[str, int]]]


class Site(BaseModel):
    """One entry of the §4.1 site registry — a job board to crawl on a cadence."""

    slug: str
    url: str
    name: str = ""
    # The company (or org) the board belongs to, for operator-facing display/notify. A
    # board is one company's careers page; the per-position ``company`` still comes from
    # each yielded Job (an aggregator board carries many), so this is descriptive, not a
    # crawl input — optional and defaulted so existing registries parse unchanged.
    company: str | None = None
    render: Literal["auto", "static", "browser", "solver"] = "auto"
    hints: str = ""
    schedule: str | None = None
    adaptive: bool = False
    # Does one crawl enumerate the board's *entire* open set? True (the default) for a
    # full careers page/ATS listing, where the §4.7 enumerate-diff (``close_missing``)
    # soundly closes positions absent from a healthy, error-free crawl. False for a
    # windowed feed (RSS / latest-N APIs like RemoteOK/WWR) whose crawl only ever sees a
    # recent slice: diffing against it would false-close everything that scrolled off, so
    # closure for these defers entirely to the §4.9 recheck timer (per-URL 404/410/expiry
    # re-verification) instead.
    enumerates_all: bool = True
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
    """Parse an ``JOBCRAWLER_*`` duration; ``off`` (or unparseable) → None (disabled)."""
    if raw.strip().lower() == "off":
        return None
    try:
        return parse_duration(raw)
    except ValueError:
        return None


def _regen_interval(raw: str) -> timedelta | None:
    """Parse ``JOBCRAWLER_REGEN_CHECK``; ``off`` (or unparseable) disables it."""
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
        has_crawler: Callable[[str], bool] | None = None,
        autonomy: bool | None = None,
        regen_check: str | None = None,
        reopt_check: str | None = None,
        reopt_dispatch: Callable[[], bool] | None = None,
        adopt_watch: Callable[[datetime], None] | None = None,
        recheck_check: str | None = None,
        recheck_cap: int | None = None,
        recheck_fetch: RecheckFetch | None = None,
        match_drain: MatchDrain | None = None,
        match_drain_check: str | None = None,
        expired_markers: tuple[str, ...] | None = None,
        heal_cooldown: str | None = None,
        description_quality_floor: float | None = None,
        min_schedule: float | None = None,
        max_schedule: float | None = None,
        adaptive_window: int | None = None,
        adaptive_target_n: float | None = None,
        bootstrap_budget: float | None = None,
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
        # Does a site have a committed crawler yet? A due site with none can't be crawled
        # (it would just FileNotFoundError every tick), so the loop routes it to the §4.2
        # cold-start bootstrap instead. Defaults to "always present" so the many tests
        # that inject only a crawl fn keep exercising the pure crawl path unchanged.
        self._has_crawler = has_crawler or (lambda _slug: True)
        # §4.8/§4.11 master autonomy switch: when off, every self-*modifying* action
        # (periodic regen, event-driven heal, out-of-band reopt — and the auto-promote /
        # auto-adopt they gate) is frozen, while crawl + ingest keep running so the loop
        # still gathers signal. Read once here; flip + restart to apply. Governs the
        # daemon's own initiative only — operator commands (promote/optimize/rollback)
        # stay live, and adaptive cadence is untouched (it's timing, not code).
        self._autonomy = config.autonomy() if autonomy is None else autonomy
        self._regen_interval = _regen_interval(
            config.regen_check() if regen_check is None else regen_check
        )
        self._next_regen: datetime | None = None
        # §4.11 out-of-band re-optimization timer: like the regen timer, but instead of
        # assessing sites it *dispatches* the detached, single-flight optimize job — the
        # multi-hour GEPA compile never runs in-band. Off by default (reopt_check "off").
        # The dispatch is injectable for tests; production uses the real detached
        # `optimize --adopt` ("full auto, gated by proof" — adoption still margin-gated).
        self._reopt_interval = _optional_interval(
            config.reopt_check() if reopt_check is None else reopt_check
        )
        self._reopt_dispatch = reopt_dispatch
        self._next_reopt: datetime | None = None
        # §4.11 P4c post-adoption regression watcher: marker-gated and cheap, so it runs
        # every loop iteration (no timer) rather than on a cadence; injectable for tests,
        # production uses the real `adopt_watch.check_adoption`. Frozen by the autonomy
        # switch like the other self-modifying actions (an auto-rollback rewrites state).
        self._adopt_watch = adopt_watch
        # §4.9 recheck timer: re-verify the known-open positions of windowed sources
        # (``enumerates_all=False``) and close the gone ones, plus fleet-wide expiry
        # closure. OBSERVATION, NOT self-modification — so, unlike regen/reopt, it is
        # EXEMPT from the master autonomy brake (see `recheck_due`): it only records that
        # a job the operator can no longer apply to has closed; it never rewrites a crawler
        # or adopts a generator. Disabled unless both a cadence and a fetch seam are wired.
        self._recheck_interval = _optional_interval(
            config.recheck_interval() if recheck_check is None else recheck_check
        )
        self._recheck_cap = (
            recheck_cap if recheck_cap is not None else config.recheck_cap()
        )
        self._recheck_fetch = recheck_fetch
        self._expired_markers = (
            expired_markers if expired_markers is not None else config.expired_markers()
        )
        self._next_recheck: datetime | None = None
        # §4.7 match-drain timer: profile watch + store-side pending drain. Like the
        # recheck, this is judgement over already-gathered data, never self-modification,
        # so it is EXEMPT from the master autonomy brake (matching already runs with
        # autonomy off — it rides every crawl). Disabled unless both a cadence and the
        # injected drain seam are wired (a crawl-only deployment has no matcher).
        self._match_drain_interval = _optional_interval(
            config.match_drain_interval()
            if match_drain_check is None
            else match_drain_check
        )
        self._match_drain = match_drain
        self._next_match_drain: datetime | None = None
        # §4.8 event-driven healing: a drifted crawl assesses immediately (not waiting
        # for the periodic sweep), rate-limited per-site by this cooldown (None = off).
        self._heal_cooldown = _optional_interval(
            config.heal_cooldown() if heal_cooldown is None else heal_cooldown
        )
        # Host-side partial-drift floor: the minimum share of a crawl's fetched positions
        # whose description cleared the §4.6 completeness bar before the crawl counts as
        # drifted (§8: never reaches the generator/RLM). Env-configured as
        # ``JOBCRAWLER_DESCRIPTION_QUALITY_FLOOR``.
        self._description_quality_floor = (
            description_quality_floor
            if description_quality_floor is not None
            else config.description_quality_floor()
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
        # §4.2 cold-start pacing: how long one tick's bootstrap pass may hold the
        # single-process loop before deferring the rest of the roster (see
        # _bootstrap_missing).
        self._bootstrap_budget = (
            bootstrap_budget
            if bootstrap_budget is not None
            else config.bootstrap_budget()
        )
        # Slugs a §16 "crawl now" button forced this tick (set by due_sites, read by
        # tick to jump a crawler-less forced site to the front of the bootstrap pass).
        self._forced_this_tick: frozenset[str] = frozenset()

    def due_sites(self) -> list[Site]:
        """Sites to crawl this tick: those past their cadence, plus any a §16 web-UI
        "crawl now" button requested.

        Drains the manual-request queue exactly once — this is the queue's sole consumer
        and :meth:`tick` its sole caller — and folds those slugs into the due set even when
        their cadence hasn't elapsed. A forced slug still respects the invariants that
        matter: a disabled site or one already ``_inflight`` is skipped (so a button press
        never overlaps a board's own run, §4.9), and a request naming a slug outside the
        registry is logged and dropped, since the daemon can only crawl boards it knows.
        """
        forced = set(self._store.take_crawl_requests())
        known = {s.slug for s in self._sites}
        # Remember this tick's manual requests so tick() can bootstrap a forced but
        # crawler-less site *ahead* of the cold-start backlog (§4.2) rather than behind
        # it — a button press is an explicit, high-intent signal, not just another due
        # site. Reset every tick (this is the queue's sole drain).
        self._forced_this_tick = frozenset(forced & known)
        if forced:
            if forced & known:
                _log.info(
                    "manual crawl requested slugs=%s", ",".join(sorted(forced & known))
                )
            if forced - known:
                _log.warning(
                    "manual crawl requested for unknown slug(s)=%s "
                    "(not in the site registry; ignored)",
                    ",".join(sorted(forced - known)),
                )
        return [
            s
            for s in self._sites
            if s.enabled
            and s.slug not in self._inflight
            and (s.slug in forced or self._is_due(s))
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
        is a crawl that found ≥1 new position) and steers the interval toward checking the
        source ``target_n`` times per posting, bounded by ``[min, max]_schedule``. Called
        once per *completed* crawl in ``tick``; a no-op for fixed-cadence sites.
        """
        if not site.adaptive:
            return
        history = self._store.runs_for(site.slug, limit=self._adaptive_window)
        # ``new_items`` is the §5 run counter (unchanged column name); semantically it is
        # this crawl's count of newly-seen open positions. Newest-first, matching
        # next_interval's window convention.
        hits = [r.new_items > 0 for r in history]
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
            # Don't require more observations than the window can ever hold, or an
            # operator who sets a window < the controller's default min would freeze
            # the cadence at its seed forever. Caps at the default 3 otherwise.
            min_observations=min(3, self._adaptive_window),
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

        Two host-side signals, computed straight from the crawl counters: a blackout
        (zero positions found) or a description-quality collapse (positions came back but
        a large share of their descriptions fell below the §4.6 completeness bar —
        truncated/boilerplate-only bodies). The completeness share is
        ``(positions_found - incomplete) / positions_found``, mirroring the runner's
        persisted ``body_quality`` signal.
        """
        if summary.positions_found == 0:
            return True
        description_quality = (
            summary.positions_found - summary.incomplete
        ) / summary.positions_found
        return description_quality < self._description_quality_floor

    def _should_event_heal(self, summary: CrawlSummary, now: datetime) -> bool:
        """True if ``summary`` warrants an immediate heal and the site is off cooldown."""
        if not self._autonomy:
            return False  # master switch off: freeze all self-modification (§4.8)
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

    async def _bootstrap_missing(
        self, sites: list[Site], forced: frozenset[str] = frozenset()
    ) -> dict[str, HealthState]:
        """Write a first crawler for each due site that has none yet (§4.2 cold-start).

        A registry entry with no ``crawlers/<slug>.py`` can't be crawled, so the daemon
        generates one before crawling — the same generator the §4.8 healer runs, via the
        healer's ``assess`` (which treats a missing crawler as an immediately-confirmed
        regenerate candidate). Delivery honors ``JOBCRAWLER_REVIEW_GATE``: staged for
        review by default, live under ``auto``/``off``. The health state machine
        (pending-review / generation-failed + backoff) keeps a staged or failed
        bootstrap from re-generating every tick.

        Frozen by the master autonomy switch — generating crawler code is
        self-modification — so with autonomy off (or no healer wired) a crawler-less site
        simply stays idle rather than being crawled into a FileNotFoundError. Each site is
        isolated like the heal path so one generation raising can't abort the tick.

        The pass carries a time budget (``JOBCRAWLER_BOOTSTRAP_BUDGET``): one
        generation blocks the single-process loop for many minutes, and a cold-start
        roster can queue dozens of sites into one tick — unbudgeted, that tick runs
        the whole roster back-to-back while every already-promoted crawler sits idle,
        due but never dispatched, its first crawl starved until the wave ends. Cheap
        assesses (a site in generation-failed backoff) sweep through freely; once the
        pass is over budget the rest of the roster defers to the next tick, where
        crawls — including the freshly promoted crawler's first — always run first.
        Deferral loses nothing: a crawler-less site is re-detected from disk every
        tick, and the health state machine still owns per-site backoff.

        Slugs in ``forced`` (this tick's manual "crawl now" requests, sorted to the
        front by the caller) are exempt from the budget cutoff: an operator who pressed
        the button asked for that crawler explicitly, so it is always attempted even if
        an earlier generation already blew the budget. Only the cadence-driven backlog
        yields.
        """
        if not self._autonomy or self._healer is None:
            return {}
        healer = self._healer
        states: dict[str, HealthState] = {}
        pass_started = self._clock()
        for done, site in enumerate(sites, start=1):
            try:
                states[site.slug] = await healer.assess(site.slug)
            except Exception:
                _log.exception(
                    "cold-start bootstrap failed for %s; skipping", site.slug
                )
            if site.slug in forced:
                continue  # explicit request — never deferred by the budget
            elapsed = (self._clock() - pass_started).total_seconds()
            if elapsed > self._bootstrap_budget and done < len(sites):
                _log.info(
                    "bootstrap pass over budget (%.0fs > %.0fs); deferring %d site(s)"
                    " to the next tick",
                    elapsed,
                    self._bootstrap_budget,
                    len(sites) - done,
                )
                break
        if states:
            _log.info("bootstrap generated=%d %s", len(states), _state_summary(states))
        return states

    async def tick(self) -> list[CrawlSummary]:
        """Dispatch every currently-due site (bounded), returning the successes.

        Each dispatched crawl logs its outcome (the daemon's operational heartbeat,
        §4.9): position counts at INFO on success, a WARNING when a site is dropped after
        exhausting its retries so the lost work isn't invisible. A crawl that comes back
        drifted (blackout or description-quality collapse) triggers an immediate §4.8
        heal, rate-limited per-site by the heal cooldown.

        A due site that has no committed crawler yet is never crawled (that would only
        FileNotFoundError); it's routed to the §4.2 cold-start bootstrap after the crawl
        wave, which writes its first crawler honoring the review gate.
        """
        due = self.due_sites()
        crawlable = [s for s in due if self._has_crawler(s.slug)]
        missing = [s for s in due if not self._has_crawler(s.slug)]
        if crawlable:
            _log.info(
                "tick dispatching n=%d sites=%s",
                len(crawlable),
                ",".join(s.slug for s in crawlable),
            )
        results = await asyncio.gather(
            *(self._dispatch(s) for s in crawlable), return_exceptions=True
        )
        summaries: list[CrawlSummary] = []
        to_heal: list[str] = []
        now = self._clock()
        for site, result in zip(crawlable, results):
            if isinstance(result, CrawlSummary):
                _log.info(
                    "crawl ok slug=%s found=%d new=%d pages=%d errors=%d",
                    result.site,
                    result.positions_found,
                    result.new_positions,
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
        if missing:
            # A manual "crawl now" on a crawler-less site is an explicit request: write
            # its crawler first (and don't let the time budget defer it behind the whole
            # cold-start backlog). Forced slugs sort ahead; the cadence-due remainder
            # follows under the budget exactly as before.
            forced = self._forced_this_tick
            missing.sort(key=lambda s: s.slug not in forced)
            await self._bootstrap_missing(missing, forced=forced)
        return summaries

    def regen_due(self, now: datetime) -> bool:
        """True if a §4.8 staleness sweep is due (per ``JOBCRAWLER_REGEN_CHECK``)."""
        if not self._autonomy:
            return False  # master switch off: freeze all self-modification (§4.8)
        if self._healer is None or self._regen_interval is None:
            return False
        return self._next_regen is None or now >= self._next_regen

    def mark_regen(self, now: datetime) -> None:
        """Record that a sweep ran at ``now``; the next is one interval later."""
        if self._regen_interval is not None:
            self._next_regen = now + self._regen_interval

    async def regen_tick(self) -> dict[str, HealthState]:
        """Assess every enabled site with a live crawler for staleness (§4.8).

        One orchestrator drives both crawling and regeneration; the healer persists
        each resulting state (so ``list`` can surface it and a generation-failed
        site is not re-enqueued into the same failing generation).

        Crawler-less sites are skipped: a §4.2 cold-start belongs to the budgeted
        bootstrap pass in :meth:`tick`, and the healer treats a missing crawler as
        an immediately-confirmed regenerate — a sweep that included those sites
        would run the very generations the bootstrap budget just deferred, unpaced.
        The first sweep fires on daemon boot (``_next_regen`` starts unset), which
        made this real: one restart into a cold roster held the loop for the whole
        roster's generations while freshly promoted crawlers sat uncrawled.
        """
        if self._healer is None:
            return {}
        states: dict[str, HealthState] = {}
        for site in self._sites:
            if not site.enabled or not self._has_crawler(site.slug):
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

    def reopt_due(self, now: datetime) -> bool:
        """True if an out-of-band §4.11 re-opt is due (per ``JOBCRAWLER_REOPT_CHECK``)."""
        if not self._autonomy:
            return False  # master switch off: freeze all self-modification (§4.11)
        if self._reopt_interval is None:
            return False
        return self._next_reopt is None or now >= self._next_reopt

    def mark_reopt(self, now: datetime) -> None:
        """Record that a reopt dispatch ran at ``now``; the next is one interval later."""
        if self._reopt_interval is not None:
            self._next_reopt = now + self._reopt_interval

    def reopt_tick(self) -> bool:
        """Dispatch the offline §4.11 self-improvement job out-of-band — never in-band.

        The GEPA compile is multi-hour, so this only *spawns* the detached, single-flight
        ``jobcrawl optimize --adopt`` job and returns at once (a dispatch while one is
        already running is a no-op). ``--adopt`` is the §4.11 "full auto, gated by proof"
        path: the child still adopts the optimized generator *only* if it clears the
        frozen held-out margin — the scheduler never makes that promotion call itself,
        and never blocks the crawl loop on the compile. Isolated like :meth:`regen_tick`
        so a dispatch error can't abort the run loop. Returns True if a job was launched.
        """
        try:
            launched = self._dispatch_reopt()
        except Exception:
            _log.exception("reopt dispatch failed; continuing")
            return False
        _log.info(
            "reopt: %s",
            "dispatched out-of-band optimize job (§4.11)"
            if launched
            else "optimize job already running; skipping",
        )
        return launched

    def _dispatch_reopt(self) -> bool:
        """Call the injected dispatch, else the real detached ``optimize --adopt``."""
        if self._reopt_dispatch is not None:
            return self._reopt_dispatch()
        from jobcrawler.optimize_job import dispatch_optimize

        return dispatch_optimize(adopt=True)

    def recheck_due(self, now: datetime) -> bool:
        """True if a §4.9 recheck sweep is due (per ``JOBCRAWLER_RECHECK_INTERVAL``).

        Deliberately **not** gated by the master autonomy switch. Re-fetching a known-open
        listing to learn whether it now 404s is pure *observation* — the same category as
        the §4.9 adaptive cadence, and unlike regen/reopt it never rewrites a crawler or
        adopts a generator. Freezing self-modification must not blind the board to jobs
        that have closed, so this timer keeps running with autonomy off. Requires both a
        configured cadence and an injected fetch seam (``recheck_fetch``).
        """
        if self._recheck_interval is None or self._recheck_fetch is None:
            return False
        return self._next_recheck is None or now >= self._next_recheck

    def mark_recheck(self, now: datetime) -> None:
        """Record that a recheck sweep ran at ``now``; the next is one interval later."""
        if self._recheck_interval is not None:
            self._next_recheck = now + self._recheck_interval

    async def recheck_tick(self) -> int:
        """Close positions that have quietly gone away (§4.9); return how many closed.

        Two host-side closure paths the enumerate-diff (``close_missing``) can't cover:

          * **expiry** — every open position whose stated ``expires_at`` has passed is
            closed fleet-wide via :meth:`StateStore.expire_due` (one indexed UPDATE across
            all sites, regardless of ``enumerates_all``).
          * **windowed re-verification** — for each enabled ``enumerates_all=False`` source
            (RSS / latest-N feeds, where diffing against the recent view would false-close
            everything that scrolled off it) re-fetch up to ``recheck_cap`` of its stalest
            known-open URLs through the SAME politeness layer as a crawl and close the ones
            that come back HTTP 404/410 or show an expired-posting marker. Full-enumeration
            sources are skipped here — their closure is the runner's guarded diff.

        A transient fetch error is **not** a closure signal: it is isolated and skipped
        (a blip must never close a live posting — mirrors the §4.8 re-probe's blip
        tolerance), so one bad fetch can't abort the sweep either. Emits structured log
        lines in the run-loop style. Never self-modifies (autonomy-exempt).
        """
        fetch = self._recheck_fetch
        if fetch is None:  # pragma: no cover - gated by recheck_due
            return 0
        closed = self._store.expire_due()
        if closed:
            _log.info("recheck expired closed=%d", closed)
        for site in self._sites:
            if not site.enabled or site.enumerates_all:
                continue
            urls = self._store.open_urls(site.slug, limit=self._recheck_cap)
            if not urls:
                continue
            checked = 0
            site_closed = 0
            for url in urls:
                try:
                    status, text = await fetch(url)
                except Exception:
                    # A transient fetch failure is not evidence the posting closed — skip
                    # it (closure is one-way; a blip must never end a live job). Surfaced.
                    _log.exception(
                        "recheck fetch failed slug=%s url=%s; skipping", site.slug, url
                    )
                    continue
                checked += 1
                reason = self._closure_reason(status, text)
                if reason is not None:
                    self._store.mark_closed(site.slug, url, reason=reason)
                    site_closed += 1
            _log.info(
                "recheck slug=%s checked=%d closed=%d cap=%d",
                site.slug,
                checked,
                site_closed,
                self._recheck_cap,
            )
            closed += site_closed
        return closed

    def match_drain_due(self, now: datetime) -> bool:
        """True if a §4.7 match-drain pass is due (``JOBCRAWLER_MATCH_DRAIN_INTERVAL``).

        Like the recheck, deliberately NOT gated by the master autonomy switch: scoring
        already-tracked rows against the operator's profile is judgement over gathered
        data, not self-modification — matching already runs with autonomy off (it rides
        every crawl). Requires both a cadence and the injected drain seam. The first
        pass fires on boot, which is what catches a ``PROFILE.md`` edited while the
        daemon was down (the persisted ``profile_hash`` meta no longer matches).
        """
        if self._match_drain_interval is None or self._match_drain is None:
            return False
        return self._next_match_drain is None or now >= self._next_match_drain

    def mark_match_drain(self, now: datetime) -> None:
        """Record that a match-drain pass ran at ``now``; the next is one interval later."""
        if self._match_drain_interval is not None:
            self._next_match_drain = now + self._match_drain_interval

    async def match_drain_tick(self) -> dict[str, int]:
        """Run one injected §4.7 match-drain pass and log its counters.

        The seam does the whole job (profile reload → requeue on hash change → rebuild
        parked rows → score under cache/cap); the scheduler contributes only cadence and
        the run-loop log line. An empty backlog with an unchanged profile is a cheap
        no-op, so the pass is quiet unless it actually did something.
        """
        drain = self._match_drain
        if drain is None:  # pragma: no cover - gated by match_drain_due
            return {}
        summary = await drain()
        if any(summary.values()):
            _log.info(
                "match drain %s",
                " ".join(f"{k}={v}" for k, v in sorted(summary.items())),
            )
        return summary

    def _closure_reason(self, status: int, text: str) -> str | None:
        """Why a re-fetched open position should close now, or None to keep it open.

        Only a *definitive* gone-signal closes a job — closure is one-way and
        operator-visible, so an ambiguous response leaves the posting open: an HTTP
        404/410 (the listing was removed) or an expired-posting marker in the page body
        (some ATSes keep the URL live but swap the posting for a 'no longer accepting
        applications' notice). Any other status — a 200 with a live posting, a 5xx, a
        403 — is not a closure signal.
        """
        if status in (404, 410):
            return f"recheck http {status}"
        lowered = text.lower()
        for marker in self._expired_markers:
            if marker and marker.lower() in lowered:
                return f"recheck expired-marker {marker!r}"
        return None

    def adopt_watch_tick(self, now: datetime) -> None:
        """Run the §4.11 post-adoption regression watcher once (P4c), if autonomy is on.

        Marker-gated and cheap (a file read when there's no adoption under probation),
        so it runs every loop iteration rather than on its own timer; the probation
        window lives in the marker, not a scheduler cadence. Frozen by the master
        autonomy switch — an auto-rollback is self-modification — and isolated so a
        watcher error can't abort the run loop.
        """
        if not self._autonomy:
            return  # master switch off: freeze all self-modification (§4.11)
        try:
            self._run_adopt_watch(now)
        except Exception:
            _log.exception("adoption regression watch failed; continuing")

    def _run_adopt_watch(self, now: datetime) -> None:
        """Call the injected watcher, else the real :func:`adopt_watch.check_adoption`."""
        if self._adopt_watch is not None:
            self._adopt_watch(now)
            return
        from jobcrawler import adopt_watch

        adopt_watch.check_adoption(self._store, now)

    async def run(self, *, stop: asyncio.Event, interval: float = 60.0) -> None:
        """Loop ticks until ``stop`` is set; each tick drains before the next.

        The slow §4.8 regeneration sweep and the §4.11 re-optimization *dispatch* hang
        off the same loop on their own cadences — never ``.compile()`` in-band (that
        offline GEPA work is handed off to a detached single-flight subprocess). The §4.9
        recheck sweep rides the loop too, closing positions that 404/expire (observation,
        so it runs even with autonomy off), as does the §4.7 match drain (profile watch +
        store-side pending rescoring — judgement, so likewise autonomy-exempt). The §4.11
        P4c post-adoption regression watcher rides every iteration (marker-gated, so it's
        a cheap no-op until an adoption is under probation).
        """
        _log.info(
            "scheduler starting sites=%d interval=%ss autonomy=%s "
            "regen=%s reopt=%s recheck=%s match_drain=%s",
            len(self._sites),
            interval,
            "on" if self._autonomy else "OFF",
            "off" if self._regen_interval is None else self._regen_interval,
            "off" if self._reopt_interval is None else self._reopt_interval,
            "off" if self._recheck_interval is None else self._recheck_interval,
            "off" if self._match_drain_interval is None else self._match_drain_interval,
        )
        while not stop.is_set():
            try:
                await self.tick()
                now = self._clock()
                if self.regen_due(now):
                    await self.regen_tick()
                    self.mark_regen(now)
                if self.reopt_due(now):
                    self.reopt_tick()
                    self.mark_reopt(now)
                if self.recheck_due(now):
                    await self.recheck_tick()
                    self.mark_recheck(now)
                if self.match_drain_due(now):
                    await self.match_drain_tick()
                    self.mark_match_drain(now)
                self.adopt_watch_tick(now)
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


def build_matcher(
    store: StateStore,
    *,
    governor: _MatchPacer,
    clock: Callable[[], datetime] = _utcnow,
    profile_path: str | None = None,
) -> Matcher | None:
    """Build the host-side profile :class:`~jobcrawler.match.Matcher` at daemon startup.

    The single place the daemon wires the §8 matcher. Returns ``None`` — matching disabled,
    the crawl/dedup/closure loop runs completely unchanged — whenever there is nothing to
    match against, and does so *loudly but non-fatally*:

      * **no ``PROFILE.md``** — a :class:`~jobcrawler.profile.ProfileNotFound` is caught and
        turned into a prominent operator warning (``matching disabled — create PROFILE.md``),
        never a boot crash. Matching is opt-in: a fresh deployment crawls and tracks jobs
        immediately, and starts scoring them against the operator the moment a profile
        exists.
      * **no ``JOBCRAWLER_MATCH_MODEL``** — with no LM configured there is nothing to score
        with, so matching stays off under the same warning category.

    Deliberately lives in this run-loop module rather than :mod:`jobcrawler.match` so that
    importing the matcher's dspy-heavy module is deferred to the one moment a profile
    actually exists — a profile-less (or match-model-less) deployment never imports dspy.
    ``governor`` (the match-rate pacer) and ``clock`` are injected so the caller owns the
    §8 daily budget and tests stay hermetic.
    """
    resolved = config.profile_path() if profile_path is None else profile_path
    try:
        profile = load_profile(resolved)
    except ProfileNotFound as exc:
        _log.warning("matching disabled — create PROFILE.md: %s", exc)
        return None
    model = config.match_model()
    if model is None:
        _log.warning(
            "matching disabled — set JOBCRAWLER_MATCH_MODEL to score jobs against %s",
            resolved,
        )
        return None
    # Deferred import: only now, with a real profile + model, do we pull in the matcher
    # (and its dspy dependency). Keeps `import jobcrawler.schedule` cheap for the many
    # code paths (CLI subcommands, tests) that never build a matcher.
    from jobcrawler.match import Matcher

    _log.info(
        "matching enabled model=%s profile=%s (%s)",
        model,
        resolved,
        profile.profile_hash[:12],
    )
    return Matcher(
        store,
        profile,
        model=model,
        batch=config.match_batch(),
        daily_cap=config.match_daily_cap(),
        governor=governor,
        clock=clock,
    )
