"""Tests for the async scheduler (§4.9).

The scheduler owns the run loop: due-selection by per-site cadence against the
run-history store, bounded global concurrency, never overlapping a site with its
own prior run, and a small backoff retry for a crawl that fails before doing any
work. Clock, crawl, and sleep are injected so the timing is deterministic.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from jobcrawler import config, schedule
from jobcrawler.health import HealthState, RegenOutcome, SelfHealer
from jobcrawler.profile import ProfileNotFound
from jobcrawler.runtime.runner import CrawlSummary
from jobcrawler.schedule import CrawlFn, Scheduler, Site, build_matcher
from jobcrawler.state import StateStore

UTC = timezone.utc
T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _summary(slug: str) -> CrawlSummary:
    return CrawlSummary(
        slug,
        positions_found=1,
        new_positions=1,
        changed_positions=0,
        closed_positions=0,
        errors=0,
        pages_fetched=1,
    )


async def _ok_crawl(site: Site) -> CrawlSummary:
    return _summary(site.slug)


def test_slug_must_be_import_safe() -> None:
    with pytest.raises(ValidationError):
        Site(slug="valor-globo", url="https://x.com")  # hyphen is not a module name
    assert Site(slug="valor_globo", url="https://x.com").slug == "valor_globo"


def test_render_must_be_a_known_tier() -> None:
    with pytest.raises(ValidationError):
        Site.model_validate({"slug": "s", "url": "https://x.com", "render": "borwser"})


def test_cron_schedule_is_rejected_until_supported() -> None:
    with pytest.raises(ValidationError):
        Site(slug="s", url="https://x.com", schedule="0 6 * * *")


def test_duration_schedule_is_accepted() -> None:
    assert Site(slug="s", url="https://x.com", schedule="6h").schedule == "6h"


def test_adaptive_defaults_off() -> None:
    site = Site(slug="s", url="https://x.com")
    assert site.adaptive is False
    assert site.schedule is None


def test_schedule_adaptive_sentinel_sets_flag_and_clears_cadence() -> None:
    # `schedule: adaptive` is sugar — it raises the flag and nulls the fixed cadence
    # so the §4.9 controller owns the interval.
    site = Site(slug="s", url="https://x.com", schedule="adaptive")
    assert site.adaptive is True
    assert site.schedule is None


def test_adaptive_flag_can_be_set_directly() -> None:
    site = Site(slug="s", url="https://x.com", adaptive=True)
    assert site.adaptive is True


def test_fixed_schedule_leaves_adaptive_off() -> None:
    site = Site(slug="s", url="https://x.com", schedule="30m")
    assert site.adaptive is False
    assert site.schedule == "30m"


def _store_with_finished_run(slug: str, at: datetime) -> StateStore:
    store = StateStore(":memory:", clock=lambda: at)
    run_id = store.start_run(slug)
    store.finish_run(run_id, items_found=1, new_items=0, errors=0, pages_fetched=1)
    return store


def test_never_run_site_is_due() -> None:
    sched = Scheduler(
        [Site(slug="s0", url="https://x.com")],
        store=StateStore(":memory:"),
        crawl=_ok_crawl,
        clock=lambda: T0,
    )
    assert [s.slug for s in sched.due_sites()] == ["s0"]


def test_recently_run_site_is_not_due() -> None:
    store = _store_with_finished_run("s0", T0)
    sched = Scheduler(
        [Site(slug="s0", url="https://x.com", schedule="6h")],
        store=store,
        crawl=_ok_crawl,
        clock=lambda: T0 + timedelta(hours=1),  # 1h < 6h cadence
    )
    assert sched.due_sites() == []


def test_site_due_once_cadence_elapses() -> None:
    store = _store_with_finished_run("s0", T0)
    sched = Scheduler(
        [Site(slug="s0", url="https://x.com", schedule="6h")],
        store=store,
        crawl=_ok_crawl,
        clock=lambda: T0 + timedelta(hours=7),
    )
    assert [s.slug for s in sched.due_sites()] == ["s0"]


def test_disabled_site_is_never_due() -> None:
    sched = Scheduler(
        [Site(slug="s0", url="https://x.com", enabled=False)],
        store=StateStore(":memory:"),
        crawl=_ok_crawl,
        clock=lambda: T0,
    )
    assert sched.due_sites() == []


def test_unfinished_last_run_blocks_redispatch() -> None:
    # a started-but-not-finished run means in-flight or crashed; don't double-dispatch.
    store = StateStore(":memory:", clock=lambda: T0)
    store.start_run("s0")
    sched = Scheduler(
        [Site(slug="s0", url="https://x.com")],
        store=store,
        crawl=_ok_crawl,
        clock=lambda: T0 + timedelta(days=1),
    )
    assert sched.due_sites() == []


async def test_tick_dispatches_due_sites() -> None:
    sites = [Site(slug=f"s{i}", url="https://x.com") for i in range(3)]
    sched = Scheduler(
        sites, store=StateStore(":memory:"), crawl=_ok_crawl, clock=lambda: T0
    )
    results = await sched.tick()
    assert {r.site for r in results} == {"s0", "s1", "s2"}


async def test_tick_respects_concurrency_cap() -> None:
    active = 0
    peak = 0

    async def crawl(site: Site) -> CrawlSummary:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return _summary(site.slug)

    sites = [Site(slug=f"s{i}", url="https://x.com") for i in range(5)]
    sched = Scheduler(
        sites,
        store=StateStore(":memory:"),
        crawl=crawl,
        concurrency=2,
        clock=lambda: T0,
    )
    await sched.tick()
    assert peak <= 2


async def test_backoff_retry_then_success() -> None:
    attempts = 0

    async def flaky(site: Site) -> CrawlSummary:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("site down")
        return _summary(site.slug)

    async def no_sleep(_: float) -> None:
        return None

    sched = Scheduler(
        [Site(slug="s0", url="https://x.com")],
        store=StateStore(":memory:"),
        crawl=flaky,
        clock=lambda: T0,
        sleep=no_sleep,
        max_retries=2,
    )
    results = await sched.tick()
    assert attempts == 2
    assert [r.site for r in results] == ["s0"]


async def test_gives_up_after_max_retries() -> None:
    async def always_fail(site: Site) -> CrawlSummary:
        raise RuntimeError("persistent failure")

    async def no_sleep(_: float) -> None:
        return None

    sched = Scheduler(
        [Site(slug="s0", url="https://x.com")],
        store=StateStore(":memory:"),
        crawl=always_fail,
        clock=lambda: T0,
        sleep=no_sleep,
        max_retries=2,
    )
    results = await sched.tick()
    assert results == []  # dropped after retries; a §4.8 health signal, not a stall


# -- §4.8/§4.9 regeneration timer --------------------------------------------


def _stale_store(slug: str = "s0") -> StateStore:
    # A baseline run that found positions, then a zero-positions run: a staleness candidate.
    store = StateStore(":memory:", clock=lambda: T0)
    baseline = store.start_run(slug)
    store.finish_run(baseline, items_found=8, new_items=8, errors=0, pages_fetched=2)
    latest = store.start_run(slug)
    store.finish_run(latest, items_found=0, new_items=0, errors=1, pages_fetched=1)
    return store


def _healer(store: StateStore, *, reprobe_items: int, regen_passes: bool) -> SelfHealer:
    async def reprobe(slug: str) -> int:
        return reprobe_items

    async def regenerate(slug: str) -> RegenOutcome:
        # These scheduler tests predate the §14.2 stage/live split; they only assert
        # heal-vs-fail, so a pass maps to a live promotion (→ HEALTHY).
        return RegenOutcome.PROMOTED if regen_passes else RegenOutcome.FAILED

    return SelfHealer(store, reprobe=reprobe, regenerate=regenerate)


def _sched(
    store: StateStore, *, healer: SelfHealer | None, regen_check: str | None = None
) -> Scheduler:
    return Scheduler(
        [Site(slug="s0", url="https://x.com")],
        store=store,
        crawl=_ok_crawl,
        healer=healer,
        clock=lambda: T0,
        regen_check=regen_check,
    )


async def test_regen_tick_assesses_and_persists_health() -> None:
    store = _stale_store()
    sched = _sched(store, healer=_healer(store, reprobe_items=0, regen_passes=False))
    states = await sched.regen_tick()
    assert states["s0"] is HealthState.GENERATION_FAILED
    assert store.get_health("s0") == "generation_failed"  # persisted for `list`


async def test_regen_tick_is_a_noop_without_a_healer() -> None:
    sched = _sched(StateStore(":memory:"), healer=None)
    assert await sched.regen_tick() == {}


async def test_tick_hot_reloads_the_registry() -> None:
    # §4.1: a board added to sites.yaml after boot is crawled on the next tick without a
    # restart, and a probe returning None (file unchanged) leaves the roster in place.
    store = StateStore(":memory:", clock=lambda: T0)
    crawled: list[str] = []

    async def crawl(site: Site) -> CrawlSummary:
        crawled.append(site.slug)
        return _summary(site.slug)

    fresh = [
        Site(slug="s0", url="https://s0.com"),
        Site(slug="new", url="https://new.com"),
    ]
    calls = {"n": 0}

    def reload_sites() -> list[Site] | None:
        calls["n"] += 1
        return fresh if calls["n"] == 1 else None  # changed once, then steady

    sched = Scheduler(
        [Site(slug="s0", url="https://s0.com")],
        store=store,
        crawl=crawl,
        clock=lambda: T0,
        reload_sites=reload_sites,
    )

    await sched.tick()
    assert "new" in crawled  # the just-added board dispatched the same tick
    assert {s.slug for s in sched._sites} == {"s0", "new"}

    await sched.tick()  # reload now returns None
    assert {s.slug for s in sched._sites} == {"s0", "new"}  # roster unchanged


async def test_regen_tick_defers_over_budget_and_stays_due() -> None:
    # A correlated drift wave: every site is a staleness candidate. With a zero budget the
    # sweep assesses one site, then defers the rest rather than regenerating the whole
    # roster back-to-back (which would freeze the crawl loop). The deferral stays due so the
    # next tick resumes it, instead of waiting a full regen interval.
    store = StateStore(":memory:", clock=lambda: T0)
    slugs = ["s0", "s1", "s2"]
    for slug in slugs:
        base = store.start_run(slug)
        store.finish_run(base, items_found=8, new_items=8, errors=0, pages_fetched=2)
        latest = store.start_run(slug)
        store.finish_run(latest, items_found=0, new_items=0, errors=1, pages_fetched=1)

    ticking = {"t": T0}

    def clock() -> datetime:
        cur = ticking["t"]
        ticking["t"] = cur + timedelta(seconds=1)  # each read advances 1s
        return cur

    sched = Scheduler(
        [Site(slug=s, url=f"https://{s}.com") for s in slugs],
        store=store,
        crawl=_ok_crawl,
        healer=_healer(store, reprobe_items=0, regen_passes=False),
        clock=clock,
        regen_check="24h",
        regen_budget=0.0,
    )

    states = await sched.regen_tick()
    assert len(states) == 1  # one assessed; the other two deferred to the next tick
    assert sched._regen_incomplete is True

    # The loop marks an incomplete sweep as still-due (completed=False keeps _next_regen==now).
    now = clock()
    sched.mark_regen(now, completed=not sched._regen_incomplete)
    assert sched.regen_due(now) is True


# -- structured run-loop logging (§4.9 operability) --------------------------


async def test_tick_logs_each_crawl_outcome(caplog: pytest.LogCaptureFixture) -> None:
    # A production daemon must not be silent: every dispatched crawl logs its item
    # counts at INFO so an operator can see the loop is working without a debugger.
    store = StateStore(":memory:", clock=lambda: T0)

    async def crawl(site: Site) -> CrawlSummary:
        return CrawlSummary(
            site.slug,
            positions_found=4,
            new_positions=2,
            changed_positions=0,
            closed_positions=0,
            errors=0,
            pages_fetched=3,
        )

    sched = Scheduler(
        [Site(slug="s0", url="https://x.com")],
        store=store,
        crawl=crawl,
        clock=lambda: T0,
    )
    with caplog.at_level(logging.INFO, logger="jobcrawler.schedule"):
        await sched.tick()

    line = next(r.getMessage() for r in caplog.records if "slug=s0" in r.getMessage())
    assert "found=4" in line and "new=2" in line and "pages=3" in line


async def test_tick_warns_when_a_crawl_is_dropped_after_retries(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A site that exhausts its retries is dropped (a §4.8 health signal), but the drop
    # must be visible at WARNING — silently vanishing work is the bug this guards.
    async def always_fail(site: Site) -> CrawlSummary:
        raise RuntimeError("boom")

    sched = Scheduler(
        [Site(slug="s0", url="https://x.com")],
        store=StateStore(":memory:"),
        crawl=always_fail,
        clock=lambda: T0,
        max_retries=0,
    )
    with caplog.at_level(logging.WARNING, logger="jobcrawler.schedule"):
        await sched.tick()

    assert any(
        r.levelno == logging.WARNING and "s0" in r.getMessage() for r in caplog.records
    )


async def test_regen_tick_logs_a_summary(caplog: pytest.LogCaptureFixture) -> None:
    # The §4.8 sweep logs a one-line state tally so a drifting fleet is visible.
    store = _stale_store()
    sched = _sched(store, healer=_healer(store, reprobe_items=0, regen_passes=False))
    with caplog.at_level(logging.INFO, logger="jobcrawler.schedule"):
        await sched.regen_tick()

    line = next(r.getMessage() for r in caplog.records if "assessed=" in r.getMessage())
    assert "assessed=1" in line and "generation_failed=1" in line


async def test_run_logs_startup(caplog: pytest.LogCaptureFixture) -> None:
    # The loop announces its operating parameters once on start (sites + cadences),
    # so a deployed daemon's logs confirm it came up and how it's configured.
    sched = Scheduler(
        [Site(slug="s0", url="https://x.com")],
        store=StateStore(":memory:", clock=lambda: T0),
        crawl=_ok_crawl,
        clock=lambda: T0,
    )
    stop = asyncio.Event()
    stop.set()  # stop immediately; only the pre-loop startup line is under test
    with caplog.at_level(logging.INFO, logger="jobcrawler.schedule"):
        await sched.run(stop=stop, interval=0.001)

    assert any(
        "starting" in r.getMessage() and "sites=1" in r.getMessage()
        for r in caplog.records
    )


async def test_regen_tick_skips_disabled_sites() -> None:
    store = _stale_store("s0")

    async def regenerate(slug: str) -> RegenOutcome:
        raise AssertionError("a disabled site must not be regenerated")

    async def reprobe(slug: str) -> int:
        return 0

    sched = Scheduler(
        [Site(slug="s0", url="https://x.com", enabled=False)],
        store=store,
        crawl=_ok_crawl,
        healer=SelfHealer(store, reprobe=reprobe, regenerate=regenerate),
        clock=lambda: T0,
    )
    assert await sched.regen_tick() == {}


async def test_regen_tick_skips_crawlerless_sites() -> None:
    # A §4.2 cold-start belongs to the budgeted bootstrap pass in tick(): the healer
    # treats a missing crawler as an immediately-confirmed regenerate, so a sweep that
    # included crawler-less sites would run the very generations the bootstrap budget
    # just deferred — unpaced, and on daemon boot (first sweep is always due) it held
    # the loop for a whole cold roster while promoted crawlers sat uncrawled.
    store = _stale_store("s0")

    async def regenerate(slug: str) -> RegenOutcome:
        raise AssertionError("a crawler-less site must not be swept into regeneration")

    async def reprobe(slug: str) -> int:
        return 0

    sched = Scheduler(
        [Site(slug="s0", url="https://x.com")],
        store=store,
        crawl=_ok_crawl,
        healer=SelfHealer(store, reprobe=reprobe, regenerate=regenerate),
        clock=lambda: T0,
        has_crawler=lambda slug: False,
    )
    assert await sched.regen_tick() == {}


def test_regen_due_respects_the_cadence() -> None:
    store = _stale_store()
    sched = _sched(
        store,
        healer=_healer(store, reprobe_items=1, regen_passes=True),
        regen_check="6h",
    )
    assert sched.regen_due(T0) is True  # never checked yet → due immediately
    sched.mark_regen(T0)
    assert sched.regen_due(T0 + timedelta(hours=1)) is False
    assert sched.regen_due(T0 + timedelta(hours=7)) is True


def test_regen_off_is_never_due() -> None:
    store = _stale_store()
    sched = _sched(
        store,
        healer=_healer(store, reprobe_items=1, regen_passes=True),
        regen_check="off",
    )
    assert sched.regen_due(T0) is False


def test_regen_disabled_without_a_healer() -> None:
    sched = _sched(StateStore(":memory:"), healer=None, regen_check="6h")
    assert sched.regen_due(T0) is False


# -- §4.11 out-of-band re-optimization dispatch timer ------------------------


def _reopt_sched(
    *,
    reopt_check: str | None = None,
    reopt_dispatch: Callable[[], bool] | None = None,
) -> Scheduler:
    return Scheduler(
        [Site(slug="s0", url="https://x.com")],
        store=StateStore(":memory:", clock=lambda: T0),
        crawl=_ok_crawl,
        clock=lambda: T0,
        reopt_check=reopt_check,
        reopt_dispatch=reopt_dispatch,
    )


def test_reopt_due_respects_the_cadence() -> None:
    sched = _reopt_sched(reopt_check="24h")
    assert sched.reopt_due(T0) is True  # never dispatched yet → due immediately
    sched.mark_reopt(T0)
    assert sched.reopt_due(T0 + timedelta(hours=1)) is False
    assert sched.reopt_due(T0 + timedelta(hours=25)) is True


def test_reopt_off_disables_the_timer() -> None:
    # Explicit `off` disables the multi-hour compile timer entirely.
    assert _reopt_sched(reopt_check="off").reopt_due(T0) is False


def test_reopt_unset_uses_the_config_default_cadence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # reopt_check=None falls back to JOBCRAWLER_REOPT_CHECK, which now defaults to a
    # real cadence (autonomous self-improvement on by default) → due on the first check.
    monkeypatch.delenv("JOBCRAWLER_REOPT_CHECK", raising=False)
    assert _reopt_sched(reopt_check=None).reopt_due(T0) is True


def test_reopt_tick_dispatches_via_the_injected_callable() -> None:
    calls: list[bool] = []

    def dispatch() -> bool:
        calls.append(True)
        return True

    sched = _reopt_sched(reopt_check="24h", reopt_dispatch=dispatch)
    assert sched.reopt_tick() is True
    assert calls == [True]  # the detached optimize dispatch was spawned exactly once


def test_reopt_tick_reports_a_skip_when_a_job_is_already_running() -> None:
    # The single-flight dispatch returns False when a compile is still running; the
    # tick surfaces that as a no-op skip (not a second concurrent multi-hour run).
    sched = _reopt_sched(reopt_check="24h", reopt_dispatch=lambda: False)
    assert sched.reopt_tick() is False


def test_reopt_tick_isolates_a_dispatch_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A dispatch error (the optimize module failed to spawn) must not abort the run
    # loop — reopt_tick swallows it like regen_tick, returning False after logging.
    def boom() -> bool:
        raise RuntimeError("spawn failed")

    sched = _reopt_sched(reopt_check="24h", reopt_dispatch=boom)
    with caplog.at_level(logging.ERROR, logger="jobcrawler.schedule"):
        assert sched.reopt_tick() is False  # must not raise
    assert any("reopt dispatch failed" in r.getMessage() for r in caplog.records)


async def test_run_invokes_reopt_at_least_once() -> None:
    calls: list[bool] = []

    def dispatch() -> bool:
        calls.append(True)
        return True

    sched = _reopt_sched(reopt_check="24h", reopt_dispatch=dispatch)
    stop = asyncio.Event()

    async def stopper() -> None:
        await asyncio.sleep(0.02)
        stop.set()

    await asyncio.gather(sched.run(stop=stop, interval=0.005), stopper())
    # The frozen clock makes reopt due exactly once; the loop dispatched it then marked
    # the next one a full interval out, so it never re-fires within this window.
    assert calls == [True]


def _two_site_stale_store() -> StateStore:
    store = StateStore(":memory:", clock=lambda: T0)
    for slug in ("s0", "s1"):
        baseline = store.start_run(slug)
        store.finish_run(
            baseline, items_found=8, new_items=8, errors=0, pages_fetched=2
        )
        latest = store.start_run(slug)
        store.finish_run(latest, items_found=0, new_items=0, errors=1, pages_fetched=1)
    return store


async def test_regen_tick_isolates_a_single_sites_failure() -> None:
    # One site's regenerate (LM/Deno/network) raising must not abort the sweep or the
    # daemon — the other sites are still assessed (§4.8 failure isolation). The healer
    # holds the crash as generation-failed (recorded, backoff engaged) rather than
    # letting it escape, so the sweep sees a state, not an exception.
    store = _two_site_stale_store()

    async def reprobe(slug: str) -> int:
        return 0  # confirmed stale → proceeds to regenerate

    async def regenerate(slug: str) -> RegenOutcome:
        if slug == "s0":
            raise RuntimeError("LM backend exploded")
        return RegenOutcome.PROMOTED

    healer = SelfHealer(store, reprobe=reprobe, regenerate=regenerate)
    sched = Scheduler(
        [Site(slug="s0", url="https://x.com"), Site(slug="s1", url="https://x.com")],
        store=store,
        crawl=_ok_crawl,
        healer=healer,
        clock=lambda: T0,
    )
    states = await sched.regen_tick()  # must not raise
    assert states["s1"] is HealthState.HEALTHY  # the healthy site was still assessed
    assert states["s0"] is HealthState.GENERATION_FAILED  # held, not fleet-fatal
    assert store.get_health("s0") == "generation_failed"  # backoff record persisted


async def test_run_loop_survives_a_tick_error(monkeypatch: pytest.MonkeyPatch) -> None:
    store = StateStore(":memory:", clock=lambda: T0)
    sched = Scheduler(
        [Site(slug="s0", url="https://x.com")],
        store=store,
        crawl=_ok_crawl,
        clock=lambda: T0,
    )
    calls = 0
    real_tick = sched.tick

    async def flaky_tick() -> list[CrawlSummary]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient loop error")
        return await real_tick()

    monkeypatch.setattr(sched, "tick", flaky_tick)
    stop = asyncio.Event()

    async def stopper() -> None:
        await asyncio.sleep(0.03)
        stop.set()

    await asyncio.gather(sched.run(stop=stop, interval=0.005), stopper())
    assert calls >= 2  # the loop kept ticking after the first tick raised


async def test_run_invokes_regen_at_least_once() -> None:
    store = _stale_store()
    healer = _healer(store, reprobe_items=0, regen_passes=False)
    sched = _sched(store, healer=healer, regen_check="6h")
    stop = asyncio.Event()

    async def stopper() -> None:
        await asyncio.sleep(0.02)
        stop.set()

    await asyncio.gather(sched.run(stop=stop, interval=0.005), stopper())
    # The frozen clock makes regen due exactly once; it should have run and persisted.
    assert store.get_health("s0") == "generation_failed"


# -- §4.9 adaptive cadence integration ---------------------------------------


def _adaptive_sched(
    store: StateStore,
    *,
    crawl: CrawlFn = _ok_crawl,
    default_schedule: str = "30m",
    min_schedule: float = 900.0,
    max_schedule: float = 86400.0,
    adaptive_window: int = 8,
    adaptive_target_n: float = 2.0,
) -> Scheduler:
    return Scheduler(
        [Site(slug="s0", url="https://x.com", adaptive=True)],
        store=store,
        crawl=crawl,
        default_schedule=default_schedule,
        min_schedule=min_schedule,
        max_schedule=max_schedule,
        adaptive_window=adaptive_window,
        adaptive_target_n=adaptive_target_n,
        clock=lambda: T0,
    )


def _seed_runs(store: StateStore, slug: str, *, hits: int, misses: int) -> None:
    for _ in range(hits):
        rid = store.start_run(slug)
        store.finish_run(rid, items_found=1, new_items=1, errors=0, pages_fetched=1)
    for _ in range(misses):
        rid = store.start_run(slug)
        store.finish_run(rid, items_found=0, new_items=0, errors=0, pages_fetched=1)


def test_adaptive_cadence_uses_seed_when_no_interval_persisted() -> None:
    # No learned interval yet → seed from the default cadence (30m), within the band.
    store = StateStore(":memory:")
    sched = _adaptive_sched(store)
    assert sched._cadence(sched._sites[0]) == timedelta(seconds=1800)


def test_adaptive_seed_is_clamped_into_band() -> None:
    # A floor above the default cadence pulls the seed up to the floor.
    store = StateStore(":memory:")
    sched = _adaptive_sched(store, min_schedule=3600.0)
    assert sched._cadence(sched._sites[0]) == timedelta(seconds=3600)


def test_adaptive_cadence_uses_persisted_interval() -> None:
    store = StateStore(":memory:")
    store.set_interval("s0", 5400.0)
    sched = _adaptive_sched(store)
    assert sched._cadence(sched._sites[0]) == timedelta(seconds=5400)


def test_update_cadence_shrinks_on_busy_source() -> None:
    store = StateStore(":memory:")
    _seed_runs(store, "s0", hits=8, misses=0)
    store.set_interval("s0", 3600.0)
    sched = _adaptive_sched(store)
    sched._update_cadence(sched._sites[0])
    assert store.get_interval("s0") == 1800.0  # 3600 × down(0.5): under-checking


def test_update_cadence_grows_on_quiet_source() -> None:
    store = StateStore(":memory:")
    _seed_runs(store, "s0", hits=0, misses=8)
    store.set_interval("s0", 3600.0)
    sched = _adaptive_sched(store)
    sched._update_cadence(sched._sites[0])
    assert store.get_interval("s0") == 5400.0  # 3600 × up(1.5): over-checking


def test_update_cadence_holds_at_target() -> None:
    store = StateStore(":memory:")
    _seed_runs(store, "s0", hits=4, misses=4)  # 0.5 == target
    store.set_interval("s0", 3600.0)
    sched = _adaptive_sched(store)
    sched._update_cadence(sched._sites[0])
    assert store.get_interval("s0") == 3600.0


def test_update_cadence_adapts_when_window_below_default_min_observations() -> None:
    # A small adaptive_window (operator override) must not freeze the controller: the
    # scheduler caps min_observations at the window, so 2 busy crawls in a window of 2
    # still shrink the interval instead of holding at the seed forever.
    store = StateStore(":memory:")
    _seed_runs(store, "s0", hits=2, misses=0)
    store.set_interval("s0", 3600.0)
    sched = _adaptive_sched(store, adaptive_window=2)
    sched._update_cadence(sched._sites[0])
    assert store.get_interval("s0") == 1800.0  # 3600 × down(0.5): adapted, not frozen


def test_update_cadence_is_noop_for_fixed_site() -> None:
    # A fixed-cadence site never grows a learned interval, even with rich history.
    store = StateStore(":memory:")
    _seed_runs(store, "s0", hits=8, misses=0)
    sched = Scheduler(
        [Site(slug="s0", url="https://x.com", schedule="30m")],
        store=store,
        crawl=_ok_crawl,
        clock=lambda: T0,
    )
    sched._update_cadence(sched._sites[0])
    assert store.get_interval("s0") is None


async def test_tick_persists_adaptive_interval() -> None:
    # End-to-end: a real tick records the crawl and then retunes+persists the
    # interval. With one hit (< min_observations) the controller holds the seed,
    # but persistence proves the wiring fired.
    store = StateStore(":memory:")

    async def recording_crawl(site: Site) -> CrawlSummary:
        rid = store.start_run(site.slug)
        store.finish_run(rid, items_found=1, new_items=1, errors=0, pages_fetched=1)
        return _summary(site.slug)

    sched = _adaptive_sched(store, crawl=recording_crawl)
    summaries = await sched.tick()
    assert [s.site for s in summaries] == ["s0"]
    assert store.get_interval("s0") == 1800.0  # seed held + persisted


async def test_adaptive_interval_converges_over_repeated_busy_ticks() -> None:
    # A persistently busy source drives the interval down to the floor across ticks.
    store = StateStore(":memory:")

    async def busy_crawl(site: Site) -> CrawlSummary:
        rid = store.start_run(site.slug)
        store.finish_run(rid, items_found=3, new_items=3, errors=0, pages_fetched=1)
        return CrawlSummary(
            site.slug,
            positions_found=3,
            new_positions=3,
            changed_positions=0,
            closed_positions=0,
            errors=0,
            pages_fetched=1,
        )

    sched = _adaptive_sched(store, crawl=busy_crawl)
    for _ in range(12):
        # due-check would gate real time; call _update via the success path directly.
        await sched._dispatch(sched._sites[0])
        sched._update_cadence(sched._sites[0])
    assert store.get_interval("s0") == 900.0  # pinned at the politeness floor


# -- §4.8 event-driven healing from tick() (P2b) -----------------------------


class _RecordingHealer:
    """A §4.8 healer stub that records which slugs were assessed (and how often)."""

    def __init__(self, *, raises: bool = False) -> None:
        self.assessed: list[str] = []
        self._raises = raises

    async def assess(self, slug: str) -> HealthState:
        self.assessed.append(slug)
        if self._raises:
            raise RuntimeError("regenerate blew up")
        return HealthState.HEALTHY


class _Clock:
    """A hand-advanced clock so cooldown windows are tested deterministically."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


async def _blackout(site: Site) -> CrawlSummary:
    return CrawlSummary(
        site.slug,
        positions_found=0,
        new_positions=0,
        changed_positions=0,
        closed_positions=0,
        errors=1,
        pages_fetched=1,
    )


async def _teasers(site: Site) -> CrawlSummary:
    # 10 positions found, 9 with descriptions below the completeness bar →
    # body_quality 0.1 (< the description-quality floor 0.5).
    return CrawlSummary(
        site.slug,
        positions_found=10,
        new_positions=2,
        changed_positions=0,
        closed_positions=0,
        errors=0,
        pages_fetched=1,
        incomplete=9,
    )


async def _healthy(site: Site) -> CrawlSummary:
    return CrawlSummary(
        site.slug,
        positions_found=10,
        new_positions=2,
        changed_positions=0,
        closed_positions=0,
        errors=0,
        pages_fetched=1,
        incomplete=0,
    )


def _heal_sched(
    *,
    crawl: CrawlFn,
    healer: _RecordingHealer | None,
    clock: _Clock,
    heal_cooldown: str = "6h",
    description_quality_floor: float = 0.5,
) -> Scheduler:
    return Scheduler(
        [Site(slug="s0", url="https://x.com")],
        store=StateStore(":memory:", clock=clock),
        crawl=crawl,
        healer=healer,
        clock=clock,
        regen_check="off",  # isolate the event path from the periodic sweep
        heal_cooldown=heal_cooldown,
        description_quality_floor=description_quality_floor,
    )


async def test_blackout_crawl_triggers_an_immediate_heal() -> None:
    healer = _RecordingHealer()
    sched = _heal_sched(crawl=_blackout, healer=healer, clock=_Clock(T0))
    await sched.tick()
    assert healer.assessed == ["s0"]


async def test_body_quality_collapse_crawl_triggers_an_immediate_heal() -> None:
    healer = _RecordingHealer()
    sched = _heal_sched(crawl=_teasers, healer=healer, clock=_Clock(T0))
    await sched.tick()
    assert healer.assessed == ["s0"]


async def test_healthy_crawl_does_not_trigger_a_heal() -> None:
    healer = _RecordingHealer()
    sched = _heal_sched(crawl=_healthy, healer=healer, clock=_Clock(T0))
    await sched.tick()
    assert healer.assessed == []


async def test_event_heal_respects_the_per_site_cooldown() -> None:
    healer = _RecordingHealer()
    clock = _Clock(T0)
    sched = _heal_sched(crawl=_blackout, healer=healer, clock=clock, heal_cooldown="6h")

    await sched.tick()  # T0: first blackout → heals
    clock.now = T0 + timedelta(hours=1)
    await sched.tick()  # +1h: still within cooldown → suppressed
    assert healer.assessed == ["s0"]

    clock.now = T0 + timedelta(hours=7)
    await sched.tick()  # +7h: cooldown elapsed → heals again
    assert healer.assessed == ["s0", "s0"]


async def test_event_heal_off_disables_the_fast_path() -> None:
    healer = _RecordingHealer()
    sched = _heal_sched(
        crawl=_blackout, healer=healer, clock=_Clock(T0), heal_cooldown="off"
    )
    await sched.tick()
    assert healer.assessed == []  # only the periodic sweep would heal now


async def test_event_heal_without_a_healer_is_a_noop() -> None:
    # No healer wired → a drifted crawl still completes the tick without raising.
    sched = _heal_sched(crawl=_blackout, healer=None, clock=_Clock(T0))
    summaries = await sched.tick()
    assert [s.site for s in summaries] == ["s0"]


async def test_a_raising_heal_does_not_abort_the_tick() -> None:
    # assess() raising (LM/Deno/network) must be isolated like the periodic sweep —
    # the tick still returns its crawl summaries.
    healer = _RecordingHealer(raises=True)
    sched = _heal_sched(crawl=_blackout, healer=healer, clock=_Clock(T0))
    summaries = await sched.tick()
    assert [s.site for s in summaries] == ["s0"]
    assert healer.assessed == ["s0"]  # it was attempted, the failure swallowed


def test_drift_signal_boundary_is_the_floor() -> None:
    # body_quality exactly at the floor is healthy; just below it is drift.
    sched = _heal_sched(crawl=_healthy, healer=None, clock=_Clock(T0))
    at_floor = CrawlSummary(
        "s0",
        positions_found=10,
        new_positions=0,
        changed_positions=0,
        closed_positions=0,
        errors=0,
        pages_fetched=1,
        incomplete=5,
    )
    below = CrawlSummary(
        "s0",
        positions_found=10,
        new_positions=0,
        changed_positions=0,
        closed_positions=0,
        errors=0,
        pages_fetched=1,
        incomplete=6,
    )
    assert not sched._drift_signal(at_floor)  # 0.5 == floor → healthy
    assert sched._drift_signal(below)  # 0.4 < floor → drift


# -- §4.8/§4.11 master autonomy kill-switch ----------------------------------
#
# JOBCRAWLER_AUTONOMY=off is the single operator brake: it must freeze every
# self-*modifying* action (periodic regen, event-driven heal, out-of-band reopt —
# and the auto-promote / auto-adopt those gate) while leaving crawl + ingest, and
# adaptive cadence (timing, not code), fully live. Each test pins ``autonomy``
# explicitly so it is hermetic regardless of the ambient env.


def _autonomy_sched(
    *,
    autonomy: bool,
    crawl: CrawlFn = _ok_crawl,
    healer: _RecordingHealer | None = None,
    clock: Callable[[], datetime] = lambda: T0,
    regen_check: str = "6h",
    reopt_check: str = "24h",
    heal_cooldown: str = "6h",
) -> Scheduler:
    return Scheduler(
        [Site(slug="s0", url="https://x.com")],
        store=StateStore(":memory:", clock=clock),
        crawl=crawl,
        healer=healer,
        clock=clock,
        autonomy=autonomy,
        regen_check=regen_check,
        reopt_check=reopt_check,
        heal_cooldown=heal_cooldown,
    )


def test_autonomy_off_freezes_the_regen_sweep() -> None:
    # Healer wired and regen configured, yet the master switch freezes the sweep.
    healer = _RecordingHealer()
    assert _autonomy_sched(autonomy=False, healer=healer).regen_due(T0) is False
    # Same config with the switch on *is* due — proving the switch is the cause.
    assert _autonomy_sched(autonomy=True, healer=healer).regen_due(T0) is True


def test_autonomy_off_freezes_the_reopt_dispatch() -> None:
    assert _autonomy_sched(autonomy=False).reopt_due(T0) is False
    assert _autonomy_sched(autonomy=True).reopt_due(T0) is True


async def test_autonomy_off_freezes_event_healing_but_keeps_crawling() -> None:
    # A blackout would normally trigger an immediate heal; with the switch off the
    # crawl still completes (data keeps flowing) but no self-heal is attempted —
    # the loop keeps gathering the signal it will act on once re-enabled.
    healer = _RecordingHealer()
    sched = _autonomy_sched(autonomy=False, crawl=_blackout, healer=healer)
    summaries = await sched.tick()
    assert [s.site for s in summaries] == ["s0"]  # still crawls + ingests
    assert healer.assessed == []  # but never self-modifies


async def test_autonomy_off_still_learns_adaptive_cadence() -> None:
    # The brake governs self-*modification* (code), not scheduling (timing): an
    # adaptive site keeps retuning + persisting its interval while frozen.
    clock = _Clock(T0)
    store = StateStore(":memory:", clock=clock)
    sched = Scheduler(
        [Site(slug="s0", url="https://x.com", adaptive=True)],
        store=store,
        crawl=_ok_crawl,
        clock=clock,
        autonomy=False,
    )
    await sched.tick()
    assert store.get_interval("s0") is not None  # cadence still learned while frozen


async def test_autonomy_off_is_announced_at_startup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # An operator scanning logs must be able to confirm the daemon came up frozen.
    sched = _autonomy_sched(autonomy=False)
    stop = asyncio.Event()
    stop.set()
    with caplog.at_level(logging.INFO, logger="jobcrawler.schedule"):
        await sched.run(stop=stop, interval=0.001)
    assert any("autonomy=OFF" in r.getMessage() for r in caplog.records)


# -- §4.11 P4c post-adoption regression watcher wiring -----------------------
#
# The watcher policy is tested in test_adopt_watch.py; here we pin only the scheduler
# wiring: that the run loop drives it, the autonomy switch freezes it (an auto-rollback
# is self-modification), and a watcher error can't abort the loop. The watcher is
# injected so these stay hermetic (no marker files, no real generator dir).


def _adopt_sched(
    *,
    autonomy: bool = True,
    adopt_watch: Callable[[datetime], None],
) -> Scheduler:
    return Scheduler(
        [Site(slug="s0", url="https://x.com")],
        store=StateStore(":memory:", clock=lambda: T0),
        crawl=_ok_crawl,
        clock=lambda: T0,
        autonomy=autonomy,
        adopt_watch=adopt_watch,
    )


def test_adopt_watch_tick_runs_the_injected_watcher() -> None:
    seen: list[datetime] = []
    sched = _adopt_sched(adopt_watch=lambda now: seen.append(now))
    sched.adopt_watch_tick(T0)
    assert seen == [T0]


def test_adopt_watch_tick_is_frozen_when_autonomy_off() -> None:
    seen: list[datetime] = []
    sched = _adopt_sched(autonomy=False, adopt_watch=lambda now: seen.append(now))
    sched.adopt_watch_tick(T0)
    assert seen == []  # an auto-rollback is self-modification → frozen by the switch


def test_adopt_watch_tick_isolates_a_watcher_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def boom(now: datetime) -> None:
        raise RuntimeError("watcher blew up")

    sched = _adopt_sched(adopt_watch=boom)
    with caplog.at_level(logging.ERROR, logger="jobcrawler.schedule"):
        sched.adopt_watch_tick(T0)  # must not raise
    assert any(
        "adoption regression watch failed" in r.getMessage() for r in caplog.records
    )


async def test_run_invokes_the_adopt_watch() -> None:
    seen: list[datetime] = []
    sched = _adopt_sched(adopt_watch=lambda now: seen.append(now))
    stop = asyncio.Event()

    async def stopper() -> None:
        await asyncio.sleep(0.02)
        stop.set()

    await asyncio.gather(sched.run(stop=stop, interval=0.005), stopper())
    assert seen  # the watcher rode the run loop at least once


# -- §4.2 cold-start bootstrap: a due site with no committed crawler ----------


def _bootstrap_sched(
    *,
    crawl: CrawlFn,
    healer: _RecordingHealer | None,
    has_crawler: Callable[[str], bool],
    autonomy: bool = True,
) -> Scheduler:
    # "has" already owns a committed crawler; "new" does not. Both are due (no prior
    # runs). The periodic sweep and the event-heal path are off, so the only thing that
    # can act on the crawler-less site is the dispatch-time cold-start bootstrap.
    return Scheduler(
        [
            Site(slug="has", url="https://has.com"),
            Site(slug="new", url="https://new.com"),
        ],
        store=StateStore(":memory:"),
        crawl=crawl,
        healer=healer,
        has_crawler=has_crawler,
        autonomy=autonomy,
        regen_check="off",
        heal_cooldown="off",
    )


async def test_due_site_without_a_crawler_is_bootstrapped_not_crawled() -> None:
    # The core of the fix: a crawler-less site is never dispatched to crawl (that would
    # only FileNotFoundError every tick); it's routed to the generator instead, while the
    # site that has a crawler crawls as usual.
    crawled: list[str] = []

    async def crawl(site: Site) -> CrawlSummary:
        crawled.append(site.slug)
        return await _ok_crawl(site)

    healer = _RecordingHealer()
    sched = _bootstrap_sched(
        crawl=crawl, healer=healer, has_crawler=lambda slug: slug == "has"
    )
    summaries = await sched.tick()

    assert crawled == ["has"]
    assert healer.assessed == ["new"]
    assert [s.site for s in summaries] == ["has"]


async def test_bootstrap_is_frozen_when_autonomy_is_off() -> None:
    # Writing a first crawler is self-modification, so the master autonomy switch freezes
    # it. The crawler-less site is then simply left idle — never crawled into an error.
    crawled: list[str] = []

    async def crawl(site: Site) -> CrawlSummary:
        crawled.append(site.slug)
        return await _ok_crawl(site)

    healer = _RecordingHealer()
    sched = _bootstrap_sched(
        crawl=crawl,
        healer=healer,
        has_crawler=lambda slug: slug == "has",
        autonomy=False,
    )
    await sched.tick()

    assert crawled == ["has"]  # the present-crawler site still crawls
    assert healer.assessed == []  # no generation while autonomy is off


async def test_bootstrap_without_a_healer_is_a_noop() -> None:
    # No generator wired → a crawler-less site isn't crawled; the tick still completes and
    # the crawlable site's summary comes back.
    crawled: list[str] = []

    async def crawl(site: Site) -> CrawlSummary:
        crawled.append(site.slug)
        return await _ok_crawl(site)

    sched = _bootstrap_sched(
        crawl=crawl, healer=None, has_crawler=lambda slug: slug == "has"
    )
    summaries = await sched.tick()

    assert crawled == ["has"]
    assert [s.site for s in summaries] == ["has"]


async def test_a_raising_bootstrap_does_not_abort_the_tick() -> None:
    # A generation raising (LM/Deno/network) is isolated like the heal path — the
    # crawlable site's summary is still returned and the daemon survives.
    healer = _RecordingHealer(raises=True)
    sched = _bootstrap_sched(
        crawl=_ok_crawl, healer=healer, has_crawler=lambda slug: slug == "has"
    )
    summaries = await sched.tick()

    assert [s.site for s in summaries] == ["has"]
    assert healer.assessed == ["new"]  # attempted, raised, tick survived


async def test_bootstrap_over_budget_defers_and_the_next_tick_crawls_the_promoted() -> (
    None
):
    # The cold-start wave pattern: a generation is many minutes of wall time — over
    # any sane budget — so tick 1 bootstraps ONE site and defers the rest of the
    # roster. Tick 2 then crawls the freshly promoted crawler first (due: it has no
    # prior run) before continuing the roster. Unbudgeted, one tick held the whole
    # roster back-to-back and every promoted crawler sat idle until the wave ended.
    clock = _Clock(T0)
    generated: set[str] = set()

    class _PromotingHealer(_RecordingHealer):
        async def assess(self, slug: str) -> HealthState:
            clock.now += timedelta(minutes=20)  # generation-scale wall time
            generated.add(slug)
            return await super().assess(slug)

    crawled: list[str] = []

    async def crawl(site: Site) -> CrawlSummary:
        crawled.append(site.slug)
        return await _ok_crawl(site)

    healer = _PromotingHealer()
    sched = Scheduler(
        [Site(slug="a", url="https://a.com"), Site(slug="b", url="https://b.com")],
        store=StateStore(":memory:"),
        crawl=crawl,
        healer=healer,
        has_crawler=lambda slug: slug in generated,
        autonomy=True,
        regen_check="off",
        heal_cooldown="off",
        clock=clock,
        bootstrap_budget=60.0,
        bootstrap_concurrency=1,  # per-site budget granularity (see the wave test below)
    )

    await sched.tick()
    assert healer.assessed == ["a"]  # one generation trips the budget; b deferred
    assert crawled == []

    await sched.tick()
    assert crawled == ["a"]  # the promoted crawler's first crawl leads the tick
    assert healer.assessed == ["a", "b"]  # then the roster continues


async def test_manual_crawl_now_on_a_crawlerless_site_jumps_the_bootstrap_queue() -> (
    None
):
    # §13: a "crawl now" press on a site with no crawler is an explicit request — it must
    # be generated ahead of the cold-start backlog and NOT deferred by the tick's budget.
    # Every generation here is over budget (20min > 60s) and the forced site 'c' sits LAST
    # in registry order, so without prioritization it would be starved behind 'a' (cf.
    # test_bootstrap_over_budget_defers, where the tail is deferred). Forcing it moves it
    # to the front and exempts it from the cutoff.
    clock = _Clock(T0)
    generated: set[str] = set()

    class _PromotingHealer(_RecordingHealer):
        async def assess(self, slug: str) -> HealthState:
            clock.now += timedelta(minutes=20)  # generation-scale wall time
            generated.add(slug)
            return await super().assess(slug)

    store = StateStore(":memory:")
    store.request_crawl("c")  # the web-UI button enqueues a manual request
    healer = _PromotingHealer()
    sched = Scheduler(
        [
            Site(slug="a", url="https://a.com"),
            Site(slug="b", url="https://b.com"),
            Site(slug="c", url="https://c.com"),
        ],
        store=store,
        crawl=_ok_crawl,
        healer=healer,
        has_crawler=lambda slug: slug in generated,
        autonomy=True,
        regen_check="off",
        heal_cooldown="off",
        clock=clock,
        bootstrap_budget=60.0,
        bootstrap_concurrency=1,  # per-site budget granularity (see the wave test below)
    )

    await sched.tick()

    # 'c' (forced) generates first and survives the budget; 'a' then runs and trips the
    # cutoff, deferring 'b'. Without the §13 fix this tick would be just ['a'].
    assert healer.assessed == ["c", "a"]
    assert "c" in generated


async def test_bootstrap_generates_in_bounded_concurrent_waves() -> None:
    # §4.2: cold start runs generations in bounded-concurrency waves so a fresh roster
    # comes up in parallel rather than one multi-minute generation at a time — but never
    # more than JOBCRAWLER_BOOTSTRAP_CONCURRENCY at once (spend/rate-limit ceiling). With
    # k=3 over six crawlerless boards, at most 3 assessments are ever in flight.
    clock = _Clock(T0)
    inflight = 0
    peak = 0

    class _ConcurrentHealer(_RecordingHealer):
        async def assess(self, slug: str) -> HealthState:
            nonlocal inflight, peak
            inflight += 1
            peak = max(peak, inflight)
            await asyncio.sleep(0)  # yield so a whole wave overlaps before any finishes
            try:
                return await super().assess(slug)
            finally:
                inflight -= 1

    healer = _ConcurrentHealer()
    sites = [Site(slug=f"s{i}", url=f"https://s{i}.example") for i in range(6)]
    sched = Scheduler(
        sites,
        store=StateStore(":memory:"),
        crawl=_ok_crawl,
        healer=healer,
        has_crawler=lambda _slug: False,  # all crawlerless → all bootstrap candidates
        autonomy=True,
        regen_check="off",
        heal_cooldown="off",
        clock=clock,
        bootstrap_budget=1e9,  # never defer: exercise the whole roster in one tick
        bootstrap_concurrency=3,
    )

    await sched.tick()

    assert healer.assessed == [f"s{i}" for i in range(6)]  # whole roster generated
    assert peak == 3  # ...in waves of k, never more at once
    # The budget only bites after real generation work: cheap assesses (a site in
    # generation-failed backoff is a fast no-op) sweep through in a single tick.
    clock = _Clock(T0)
    healer = _RecordingHealer()  # never advances the clock: every assess is free
    sched = Scheduler(
        [Site(slug="a", url="https://a.com"), Site(slug="b", url="https://b.com")],
        store=StateStore(":memory:"),
        crawl=_ok_crawl,
        healer=healer,
        has_crawler=lambda slug: False,
        autonomy=True,
        regen_check="off",
        heal_cooldown="off",
        clock=clock,
        bootstrap_budget=60.0,
    )

    await sched.tick()
    assert healer.assessed == ["a", "b"]


# -- Site model: job-domain fields (§4.6/§4.9) -------------------------------


def test_site_defaults_company_none_and_enumerates_all_true() -> None:
    # A board is assumed fully enumerable (the runner's guarded diff closes it); the
    # display company is optional. These defaults keep every existing sites.yaml valid.
    site = Site(slug="s", url="https://x.com")
    assert site.company is None
    assert site.enumerates_all is True


def test_site_accepts_a_company_and_a_windowed_flag() -> None:
    # RSS / latest-N feeds only ever expose a recent window, so they opt out of
    # diff-closure (``enumerates_all=False``) and lean on the §4.9 recheck sweep instead.
    site = Site(
        slug="remoteok",
        url="https://remoteok.com/remote-jobs",
        company="RemoteOK",
        enumerates_all=False,
    )
    assert site.company == "RemoteOK"
    assert site.enumerates_all is False


# -- §4.9 recheck timer: closure by re-verification + expiry -----------------
#
# Windowed sources (enumerates_all=False) can't be closed by the runner's diff, so a
# recheck sweep re-fetches their known-open URLs and closes only the definitively-gone
# ones (404/410 or an expired-posting marker), plus fleet-wide expiry via the store. It
# is pure OBSERVATION, so — unlike regen/reopt — it stays live with autonomy off.


class _RecheckStore(StateStore):
    """A store whose §4.9 recheck surface returns canned data and records closures."""

    def __init__(self, open_by_site: dict[str, list[str]], *, expired: int = 0) -> None:
        super().__init__(":memory:")
        self._open_by_site = open_by_site
        self._expired = expired
        self.closed: list[tuple[str, str, str]] = []

    def expire_due(self) -> int:
        return self._expired

    def open_urls(self, site: str, *, limit: int) -> list[str]:
        return list(self._open_by_site.get(site, [])[:limit])

    def mark_closed(self, site: str, url: str, *, reason: str) -> None:
        self.closed.append((site, url, reason))


_MARKERS = ("no longer accepting applications", "position filled")


def _recheck_sched(
    store: StateStore,
    *,
    sites: list[Site],
    fetch: schedule.RecheckFetch,
    autonomy: bool = True,
    cap: int = 150,
) -> Scheduler:
    return Scheduler(
        sites,
        store=store,
        crawl=_ok_crawl,
        clock=lambda: T0,
        recheck_check="12h",
        recheck_cap=cap,
        recheck_fetch=fetch,
        expired_markers=_MARKERS,
        autonomy=autonomy,
        regen_check="off",
        reopt_check="off",
    )


async def _never_fetch(url: str) -> tuple[int, str]:  # pragma: no cover - guard
    raise AssertionError(f"unexpected recheck fetch of {url}")


def test_recheck_due_requires_both_a_cadence_and_a_fetch_seam() -> None:
    store = StateStore(":memory:")
    sites = [Site(slug="s0", url="https://x.com", enumerates_all=False)]
    # A cadence but no fetch seam → disabled.
    no_fetch = Scheduler(
        sites, store=store, crawl=_ok_crawl, clock=lambda: T0, recheck_check="12h"
    )
    assert no_fetch.recheck_due(T0) is False
    # A fetch seam but the cadence turned off → disabled.
    no_cadence = Scheduler(
        sites,
        store=store,
        crawl=_ok_crawl,
        clock=lambda: T0,
        recheck_check="off",
        recheck_fetch=_never_fetch,
    )
    assert no_cadence.recheck_due(T0) is False
    # Both wired → due on the first pass (no prior sweep recorded).
    both = _recheck_sched(store, sites=sites, fetch=_never_fetch)
    assert both.recheck_due(T0) is True


def test_mark_recheck_advances_the_timer_by_one_interval() -> None:
    sched = _recheck_sched(
        StateStore(":memory:"),
        sites=[Site(slug="s0", url="https://x.com", enumerates_all=False)],
        fetch=_never_fetch,
    )
    assert sched.recheck_due(T0) is True
    sched.mark_recheck(T0)
    assert sched.recheck_due(T0) is False  # inside the 12h window now
    assert sched.recheck_due(T0 + timedelta(hours=11, minutes=59)) is False
    assert sched.recheck_due(T0 + timedelta(hours=12)) is True


async def test_recheck_closes_only_definitively_gone_windowed_positions() -> None:
    store = _RecheckStore({"remoteok": ["u1", "u2", "u3", "u4"]})

    async def fetch(url: str) -> tuple[int, str]:
        return {
            "u1": (404, ""),  # removed → close
            "u2": (200, "<h1>Still hiring</h1>"),  # live → keep
            "u3": (200, "This position filled last week."),  # marker → close
            "u4": (410, "Gone"),  # gone → close
        }[url]

    sched = _recheck_sched(
        store,
        sites=[Site(slug="remoteok", url="https://x.com", enumerates_all=False)],
        fetch=fetch,
    )
    closed = await sched.recheck_tick()

    assert closed == 3
    assert store.closed == [
        ("remoteok", "u1", "recheck http 404"),
        ("remoteok", "u3", "recheck expired-marker 'position filled'"),
        ("remoteok", "u4", "recheck http 410"),
    ]


async def test_recheck_skips_full_enumeration_sources() -> None:
    # A full-enumeration board is closed by the runner's guarded diff, never re-fetched
    # here — so even though it has open URLs, none are touched by the sweep.
    fetched: list[str] = []
    store = _RecheckStore({"windowed": ["w1"], "full": ["f1", "f2"]})

    async def fetch(url: str) -> tuple[int, str]:
        fetched.append(url)
        return (404, "")

    sched = _recheck_sched(
        store,
        sites=[
            Site(slug="windowed", url="https://x.com", enumerates_all=False),
            Site(slug="full", url="https://y.com", enumerates_all=True),
        ],
        fetch=fetch,
    )
    closed = await sched.recheck_tick()

    assert fetched == ["w1"]  # only the windowed source was re-verified
    assert closed == 1
    assert store.closed == [("windowed", "w1", "recheck http 404")]


async def test_recheck_expiry_closes_fleet_wide_and_is_counted() -> None:
    # expire_due closes every past-``expires_at`` position across all sites (regardless of
    # enumerates_all) and its count folds into the sweep total.
    store = _RecheckStore({"windowed": []}, expired=4)
    sched = _recheck_sched(
        store,
        sites=[Site(slug="windowed", url="https://x.com", enumerates_all=False)],
        fetch=_never_fetch,
    )
    assert await sched.recheck_tick() == 4
    assert store.closed == []  # expiry is the store's own UPDATE, not a mark_closed


async def test_recheck_runs_with_autonomy_off() -> None:
    # THE key invariant: recheck is observation, not self-modification, so the master
    # autonomy brake must NOT freeze it (contrast regen/reopt, which it does freeze).
    store = _RecheckStore({"remoteok": ["u1"]})

    async def fetch(url: str) -> tuple[int, str]:
        return (404, "")

    sched = _recheck_sched(
        store,
        sites=[Site(slug="remoteok", url="https://x.com", enumerates_all=False)],
        fetch=fetch,
        autonomy=False,
    )
    assert sched.recheck_due(T0) is True
    assert await sched.recheck_tick() == 1
    assert store.closed == [("remoteok", "u1", "recheck http 404")]


async def test_recheck_tolerates_a_transient_fetch_error() -> None:
    # A fetch that raises is isolated and skipped — a blip must never close a live job,
    # and one bad fetch must not abort the rest of the sweep. Closure is one-way.
    store = _RecheckStore({"remoteok": ["boom", "u2"]})

    async def fetch(url: str) -> tuple[int, str]:
        if url == "boom":
            raise RuntimeError("connection reset")
        return (404, "")

    sched = _recheck_sched(
        store,
        sites=[Site(slug="remoteok", url="https://x.com", enumerates_all=False)],
        fetch=fetch,
    )
    assert await sched.recheck_tick() == 1
    assert store.closed == [("remoteok", "u2", "recheck http 404")]


def test_closure_reason_fires_only_on_definitive_gone_signals() -> None:
    sched = _recheck_sched(
        StateStore(":memory:"),
        sites=[Site(slug="s0", url="https://x.com", enumerates_all=False)],
        fetch=_never_fetch,
    )
    assert sched._closure_reason(404, "") == "recheck http 404"
    assert sched._closure_reason(410, "") == "recheck http 410"
    assert (
        sched._closure_reason(200, "Sorry, this position filled.")
        == "recheck expired-marker 'position filled'"
    )
    # Case-insensitive marker match.
    assert (
        sched._closure_reason(200, "NO LONGER ACCEPTING APPLICATIONS")
        == "recheck expired-marker 'no longer accepting applications'"
    )
    # Ambiguous / live responses never close: a live 200, a server error, a 403.
    assert sched._closure_reason(200, "<h1>Apply now</h1>") is None
    assert sched._closure_reason(500, "") is None
    assert sched._closure_reason(403, "") is None


# -- build_matcher: host-side matcher wired at daemon startup (§8) ------------


class _Pacer:
    """A minimal §8 match-rate pacer (the Matcher's governor seam wants ``pace``)."""

    def pace(self) -> None: ...


class _FakeProfile:
    profile_hash = "0123456789abcdeffedcba"


def test_build_matcher_without_a_profile_disables_matching(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # No PROFILE.md → matching is disabled with a prominent warning, never a boot crash;
    # the crawl/close loop runs unchanged.
    def _raise(path: str) -> object:
        raise ProfileNotFound(path)

    monkeypatch.setattr(schedule, "load_profile", _raise)
    with caplog.at_level(logging.WARNING, logger="jobcrawler.schedule"):
        matcher = build_matcher(StateStore(":memory:"), governor=_Pacer())
    assert matcher is None
    assert any(
        "matching disabled" in r.getMessage() and "PROFILE.md" in r.getMessage()
        for r in caplog.records
    )


def test_build_matcher_without_a_model_disables_matching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(schedule, "load_profile", lambda path: _FakeProfile())
    monkeypatch.setattr(config, "match_model", lambda: None)
    assert build_matcher(StateStore(":memory:"), governor=_Pacer()) is None


def test_build_matcher_wires_the_matcher_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(schedule, "load_profile", lambda path: _FakeProfile())
    monkeypatch.setattr(config, "match_model", lambda: "openai/gpt-x")
    monkeypatch.setattr(config, "match_batch", lambda: 8)
    monkeypatch.setattr(config, "match_daily_cap", lambda: 500)
    monkeypatch.setattr(config, "require_extract_model", lambda: "openai/gpt-mini")
    monkeypatch.setattr(config, "extract_min_score", lambda: 55)
    monkeypatch.setattr(config, "extract_cap", lambda: 30)

    captured: dict[str, object] = {}

    class _StubMatcher:
        def __init__(
            self,
            store: object,
            profile: object,
            *,
            model: str,
            batch: int,
            daily_cap: int,
            governor: object,
            clock: object,
            extract_model: str | None,
            extract_min_score: int,
            extract_cap: int,
        ) -> None:
            captured.update(
                model=model,
                batch=batch,
                daily_cap=daily_cap,
                governor=governor,
                extract_model=extract_model,
                extract_min_score=extract_min_score,
                extract_cap=extract_cap,
            )

    monkeypatch.setattr("jobcrawler.match.Matcher", _StubMatcher)
    gov = _Pacer()
    matcher = build_matcher(
        StateStore(":memory:"), governor=gov, profile_path="PROFILE.md"
    )
    assert isinstance(matcher, _StubMatcher)
    assert captured == {
        "model": "openai/gpt-x",
        "batch": 8,
        "daily_cap": 500,
        "governor": gov,
        "extract_model": "openai/gpt-mini",
        "extract_min_score": 55,
        "extract_cap": 30,
    }


# -- §4.7 match-drain timer: profile watch + store-side rescoring -------------
#
# The drain is autonomy-EXEMPT (like the recheck): it exercises judgement over data
# already gathered, not self-modification, so it keeps running with autonomy off.


def test_match_drain_due_requires_both_a_cadence_and_a_seam() -> None:
    store = StateStore(":memory:")
    sites = [Site(slug="s0", url="https://x.com")]

    async def drain() -> dict[str, int]:
        return {}

    # A cadence but no seam → disabled.
    no_seam = Scheduler(
        sites, store=store, crawl=_ok_crawl, clock=lambda: T0, match_drain_check="10m"
    )
    assert no_seam.match_drain_due(T0) is False
    # A seam but the cadence turned off → disabled.
    no_cadence = Scheduler(
        sites,
        store=store,
        crawl=_ok_crawl,
        clock=lambda: T0,
        match_drain_check="off",
        match_drain=drain,
    )
    assert no_cadence.match_drain_due(T0) is False
    # Both wired → due on the FIRST pass (catches a PROFILE.md edited while the
    # daemon was down).
    both = Scheduler(
        sites,
        store=store,
        crawl=_ok_crawl,
        clock=lambda: T0,
        match_drain_check="10m",
        match_drain=drain,
    )
    assert both.match_drain_due(T0) is True


async def test_match_drain_tick_invokes_the_seam_and_marks_the_timer() -> None:
    calls: list[str] = []

    async def drain() -> dict[str, int]:
        calls.append("drain")
        return {"requeued": 0, "rows": 0, "unscorable": 0, "prefiltered": 0}

    sched = Scheduler(
        [Site(slug="s0", url="https://x.com")],
        store=StateStore(":memory:"),
        crawl=_ok_crawl,
        clock=lambda: T0,
        match_drain_check="10m",
        match_drain=drain,
    )
    await sched.match_drain_tick()
    assert calls == ["drain"]
    sched.mark_match_drain(T0)
    assert sched.match_drain_due(T0) is False
    assert sched.match_drain_due(T0 + timedelta(minutes=9)) is False
    assert sched.match_drain_due(T0 + timedelta(minutes=10)) is True
