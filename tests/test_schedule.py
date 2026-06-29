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

from bizup_autocrawler.health import HealthState, RegenOutcome, SelfHealer
from bizup_autocrawler.runtime.runner import CrawlSummary
from bizup_autocrawler.schedule import CrawlFn, Scheduler, Site
from bizup_autocrawler.state import StateStore

UTC = timezone.utc
T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _summary(slug: str) -> CrawlSummary:
    return CrawlSummary(slug, items_found=1, new_items=1, errors=0, pages_fetched=1)


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
    # A baseline run that found items, then a zero-items run: a staleness candidate.
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


# -- structured run-loop logging (§4.9 operability) --------------------------


async def test_tick_logs_each_crawl_outcome(caplog: pytest.LogCaptureFixture) -> None:
    # A production daemon must not be silent: every dispatched crawl logs its item
    # counts at INFO so an operator can see the loop is working without a debugger.
    store = StateStore(":memory:", clock=lambda: T0)

    async def crawl(site: Site) -> CrawlSummary:
        return CrawlSummary(
            site.slug, items_found=4, new_items=2, errors=0, pages_fetched=3
        )

    sched = Scheduler(
        [Site(slug="s0", url="https://x.com")],
        store=store,
        crawl=crawl,
        clock=lambda: T0,
    )
    with caplog.at_level(logging.INFO, logger="bizup_autocrawler.schedule"):
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
    with caplog.at_level(logging.WARNING, logger="bizup_autocrawler.schedule"):
        await sched.tick()

    assert any(
        r.levelno == logging.WARNING and "s0" in r.getMessage() for r in caplog.records
    )


async def test_regen_tick_logs_a_summary(caplog: pytest.LogCaptureFixture) -> None:
    # The §4.8 sweep logs a one-line state tally so a drifting fleet is visible.
    store = _stale_store()
    sched = _sched(store, healer=_healer(store, reprobe_items=0, regen_passes=False))
    with caplog.at_level(logging.INFO, logger="bizup_autocrawler.schedule"):
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
    with caplog.at_level(logging.INFO, logger="bizup_autocrawler.schedule"):
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


def test_reopt_off_is_never_due() -> None:
    # Off by default — the multi-hour compile is opt-in, never fires unconfigured.
    assert _reopt_sched(reopt_check="off").reopt_due(T0) is False
    assert _reopt_sched(reopt_check=None).reopt_due(T0) is False


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
    with caplog.at_level(logging.ERROR, logger="bizup_autocrawler.schedule"):
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
    # daemon — the other sites are still assessed (§4.8 failure isolation).
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
    assert "s0" not in states  # the failing site is isolated, not fleet-fatal


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
        return CrawlSummary(site.slug, 3, 3, 0, 1)

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
        site.slug, items_found=0, new_items=0, errors=1, pages_fetched=1
    )


async def _teasers(site: Site) -> CrawlSummary:
    # 10 items found, 9 below the completeness bar → body_quality 0.1 (< floor 0.5).
    return CrawlSummary(
        site.slug, items_found=10, new_items=2, errors=0, pages_fetched=1, incomplete=9
    )


async def _healthy(site: Site) -> CrawlSummary:
    return CrawlSummary(
        site.slug, items_found=10, new_items=2, errors=0, pages_fetched=1, incomplete=0
    )


def _heal_sched(
    *,
    crawl: CrawlFn,
    healer: _RecordingHealer | None,
    clock: _Clock,
    heal_cooldown: str = "6h",
    body_quality_floor: float = 0.5,
) -> Scheduler:
    return Scheduler(
        [Site(slug="s0", url="https://x.com")],
        store=StateStore(":memory:", clock=clock),
        crawl=crawl,
        healer=healer,
        clock=clock,
        regen_check="off",  # isolate the event path from the periodic sweep
        heal_cooldown=heal_cooldown,
        body_quality_floor=body_quality_floor,
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
        "s0", items_found=10, new_items=0, errors=0, pages_fetched=1, incomplete=5
    )
    below = CrawlSummary(
        "s0", items_found=10, new_items=0, errors=0, pages_fetched=1, incomplete=6
    )
    assert not sched._drift_signal(at_floor)  # 0.5 == floor → healthy
    assert sched._drift_signal(below)  # 0.4 < floor → drift


# -- §4.8/§4.11 master autonomy kill-switch ----------------------------------
#
# AUTOCRAWLER_AUTONOMY=off is the single operator brake: it must freeze every
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
    with caplog.at_level(logging.INFO, logger="bizup_autocrawler.schedule"):
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
    with caplog.at_level(logging.ERROR, logger="bizup_autocrawler.schedule"):
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
