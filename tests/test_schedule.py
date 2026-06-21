"""Tests for the async scheduler (§4.9).

The scheduler owns the run loop: due-selection by per-site cadence against the
run-history store, bounded global concurrency, never overlapping a site with its
own prior run, and a small backoff retry for a crawl that fails before doing any
work. Clock, crawl, and sleep are injected so the timing is deterministic.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from bizup_autocrawler.health import HealthState, SelfHealer
from bizup_autocrawler.runtime.runner import CrawlSummary
from bizup_autocrawler.schedule import Scheduler, Site
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

    async def regenerate(slug: str) -> bool:
        return regen_passes

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


async def test_regen_tick_skips_disabled_sites() -> None:
    store = _stale_store("s0")

    async def regenerate(slug: str) -> bool:
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
