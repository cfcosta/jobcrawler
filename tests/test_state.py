"""Tests for the crawl-state store (§4.7).

The store is decoupled persistence: a per-site set of seen/ingested canonical
URLs (the dedup key) plus a run-history log. It holds no policy — the runner
(§4.4) decides *when* to mark seen vs ingested; the store only records the two
states and never silently downgrades an ingested URL back to merely seen.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone

import pytest

from bizup_autocrawler.state import RunRecord, StateStore


@pytest.fixture
def store() -> Iterator[StateStore]:
    s = StateStore(":memory:")
    yield s
    s.close()


def test_unseen_then_seen(store: StateStore) -> None:
    assert not store.is_seen("acme", "https://acme.test/a")
    store.mark_seen("acme", "https://acme.test/a")
    assert store.is_seen("acme", "https://acme.test/a")


def test_seen_is_scoped_per_site(store: StateStore) -> None:
    store.mark_seen("acme", "https://x.test/a")
    assert not store.is_seen("other", "https://x.test/a")


def test_mark_seen_does_not_imply_ingested(store: StateStore) -> None:
    store.mark_seen("acme", "https://acme.test/a")
    assert not store.is_ingested("acme", "https://acme.test/a")


def test_mark_ingested_implies_seen_and_ingested(store: StateStore) -> None:
    store.mark_ingested("acme", "https://acme.test/a")
    assert store.is_seen("acme", "https://acme.test/a")
    assert store.is_ingested("acme", "https://acme.test/a")


def test_mark_seen_is_idempotent(store: StateStore) -> None:
    store.mark_seen("acme", "https://acme.test/a")
    store.mark_seen("acme", "https://acme.test/a")
    assert store.is_seen("acme", "https://acme.test/a")


def test_mark_seen_does_not_downgrade_ingested(store: StateStore) -> None:
    store.mark_ingested("acme", "https://acme.test/a")
    store.mark_seen("acme", "https://acme.test/a")  # must not clobber ingested
    assert store.is_ingested("acme", "https://acme.test/a")


def test_run_lifecycle_records_counters(store: StateStore) -> None:
    run_id = store.start_run("acme")
    pending = store.last_run("acme")
    assert pending is not None
    assert pending.site == "acme"
    assert pending.finished is None

    store.finish_run(run_id, items_found=10, new_items=3, errors=1, pages_fetched=7)
    done = store.last_run("acme")
    assert done is not None
    assert isinstance(done, RunRecord)
    assert done.finished is not None
    assert (done.items_found, done.new_items, done.errors, done.pages_fetched) == (
        10,
        3,
        1,
        7,
    )


def test_last_run_is_none_with_no_history(store: StateStore) -> None:
    assert store.last_run("acme") is None


def test_runs_for_returns_history_newest_first(store: StateStore) -> None:
    first = store.start_run("acme")
    store.finish_run(first, items_found=1, new_items=1, errors=0, pages_fetched=1)
    second = store.start_run("acme")
    store.finish_run(second, items_found=2, new_items=0, errors=0, pages_fetched=1)

    history = store.runs_for("acme", limit=10)
    assert [r.items_found for r in history] == [2, 1]


def test_started_timestamp_is_timezone_aware(store: StateStore) -> None:
    store.start_run("acme")
    rec = store.last_run("acme")
    assert rec is not None
    assert isinstance(rec.started, datetime)
    assert rec.started.tzinfo is not None


def test_injected_clock_is_used_for_timestamps() -> None:
    fixed = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    s = StateStore(":memory:", clock=lambda: fixed)
    s.start_run("acme")
    rec = s.last_run("acme")
    assert rec is not None
    assert rec.started == fixed
    s.close()


def test_state_persists_across_reopen(tmp_path: object) -> None:
    db = f"{tmp_path}/state.db"  # type: ignore[str-bytes-safe]
    s1 = StateStore(db)
    s1.mark_ingested("acme", "https://acme.test/a")
    s1.close()

    s2 = StateStore(db)
    assert s2.is_ingested("acme", "https://acme.test/a")
    s2.close()
