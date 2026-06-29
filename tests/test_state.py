"""Tests for the crawl-state store (§4.7).

The store is decoupled persistence: a per-site set of seen/ingested canonical
URLs (the dedup key) plus a run-history log. It holds no policy — the runner
(§4.4) decides *when* to mark seen vs ingested; the store only records the two
states and never silently downgrades an ingested URL back to merely seen.
"""

from __future__ import annotations

import sqlite3
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


def test_reap_orphans_clears_unfinished_runs(store: StateStore) -> None:
    # A kill -9 mid-crawl leaves finished=NULL; _is_due then blocks the site forever
    # (last.finished is None). Reaping clears the orphan so the site is due again.
    store.start_run("acme")  # never finished
    assert store.last_run("acme") is not None
    assert store.reap_orphans() == 1
    assert store.last_run("acme") is None  # orphan gone → site no longer wedged


def test_reap_orphans_leaves_finished_runs(store: StateStore) -> None:
    run_id = store.start_run("acme")
    store.finish_run(run_id, items_found=3, new_items=1, errors=0, pages_fetched=2)
    assert store.reap_orphans() == 0
    last = store.last_run("acme")
    assert last is not None and last.items_found == 3


def test_reap_orphans_preserves_a_prior_finished_run(store: StateStore) -> None:
    done = store.start_run("acme")
    store.finish_run(done, items_found=5, new_items=2, errors=0, pages_fetched=3)
    store.start_run("acme")  # a later crawl that crashed mid-run
    assert store.reap_orphans() == 1
    last = store.last_run("acme")  # falls back to the earlier completed run
    assert last is not None and last.finished is not None and last.items_found == 5


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


# -- per-site health state (§4.8 persistence) --------------------------------


def test_health_is_none_until_set(store: StateStore) -> None:
    assert store.get_health("acme") is None


def test_set_then_get_health(store: StateStore) -> None:
    store.set_health("acme", "stale")
    assert store.get_health("acme") == "stale"


def test_set_health_overwrites_prior_state(store: StateStore) -> None:
    store.set_health("acme", "stale")
    store.set_health("acme", "generation_failed")
    assert store.get_health("acme") == "generation_failed"


def test_health_persists_across_reopen(tmp_path: object) -> None:
    db = f"{tmp_path}/state.db"  # type: ignore[str-bytes-safe]
    s1 = StateStore(db)
    s1.set_health("acme", "generation_failed")
    s1.close()

    s2 = StateStore(db)
    assert s2.get_health("acme") == "generation_failed"
    s2.close()


# -- health record: updated_at + attempts (§4.8 retry-with-backoff, P2c) ------


def test_health_record_is_none_until_set(store: StateStore) -> None:
    assert store.get_health_record("acme") is None


def test_health_record_carries_state_time_and_attempts() -> None:
    fixed = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    s = StateStore(":memory:", clock=lambda: fixed)
    s.set_health("acme", "generation_failed")
    rec = s.get_health_record("acme")
    assert rec is not None
    assert rec.state == "generation_failed"
    assert rec.updated_at == fixed
    assert rec.attempts == 0  # no bump → counter starts at 0
    s.close()


def test_bump_attempts_increments_the_counter(store: StateStore) -> None:
    store.set_health("acme", "generation_failed", bump_attempts=True)
    store.set_health("acme", "generation_failed", bump_attempts=True)
    store.set_health("acme", "generation_failed", bump_attempts=True)
    rec = store.get_health_record("acme")
    assert rec is not None and rec.attempts == 3


def test_attempts_reset_when_not_bumped(store: StateStore) -> None:
    store.set_health("acme", "generation_failed", bump_attempts=True)
    store.set_health("acme", "generation_failed", bump_attempts=True)
    bumped = store.get_health_record("acme")
    assert bumped is not None and bumped.attempts == 2
    # A non-bumping set (e.g. recovering to healthy) zeroes the consecutive count.
    store.set_health("acme", "healthy")
    rec = store.get_health_record("acme")
    assert rec is not None and rec.state == "healthy" and rec.attempts == 0


def test_attempts_persist_across_reopen(tmp_path: object) -> None:
    db = f"{tmp_path}/state.db"  # type: ignore[str-bytes-safe]
    s1 = StateStore(db)
    s1.set_health("acme", "generation_failed", bump_attempts=True)
    s1.set_health("acme", "generation_failed", bump_attempts=True)
    s1.close()

    s2 = StateStore(db)
    rec = s2.get_health_record("acme")
    assert rec is not None and rec.attempts == 2
    s2.close()


def test_migration_adds_attempts_column_to_legacy_db(tmp_path: object) -> None:
    # A DB created before the `attempts` column existed: build site_health by hand
    # without it, then open a StateStore and confirm the column is backfilled.
    db = f"{tmp_path}/legacy.db"  # type: ignore[str-bytes-safe]
    raw = sqlite3.connect(db)
    raw.executescript(
        """
        CREATE TABLE site_health (
            site       TEXT PRIMARY KEY,
            state      TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO site_health (site, state, updated_at)
        VALUES ('acme', 'generation_failed', '2026-01-01T00:00:00+00:00');
        """
    )
    raw.commit()
    raw.close()

    s = StateStore(db)
    rec = s.get_health_record("acme")  # legacy row reads back with attempts defaulted
    assert rec is not None and rec.state == "generation_failed" and rec.attempts == 0
    s.set_health("acme", "generation_failed", bump_attempts=True)
    bumped = s.get_health_record("acme")
    assert bumped is not None and bumped.attempts == 1
    s.close()


def test_attempts_migration_is_idempotent(tmp_path: object) -> None:
    db = f"{tmp_path}/state.db"  # type: ignore[str-bytes-safe]
    StateStore(db).close()
    s = StateStore(db)  # re-open re-runs _migrate
    cols = [row["name"] for row in s._conn.execute("PRAGMA table_info(site_health)")]
    assert cols.count("attempts") == 1
    s.close()


# -- health-transition log (§4.8 observability, P0) --------------------------


def test_no_transitions_before_any_health_set(store: StateStore) -> None:
    assert store.transitions_for("acme") == []


def test_first_set_health_logs_transition_from_none(store: StateStore) -> None:
    store.set_health("acme", "healthy")
    log = store.transitions_for("acme")
    assert len(log) == 1
    assert log[0].from_state is None
    assert log[0].to_state == "healthy"
    assert log[0].site == "acme"
    assert log[0].ts.tzinfo is not None


def test_real_change_logs_a_transition(store: StateStore) -> None:
    store.set_health("acme", "healthy")
    store.set_health("acme", "stale")
    log = store.transitions_for("acme")  # newest-first
    assert [(t.from_state, t.to_state) for t in log] == [
        ("healthy", "stale"),
        (None, "healthy"),
    ]


def test_reasserting_same_state_logs_no_transition(store: StateStore) -> None:
    store.set_health("acme", "healthy")
    store.set_health("acme", "healthy")  # no-op re-assert
    store.set_health("acme", "healthy")
    assert len(store.transitions_for("acme")) == 1


def test_transitions_are_scoped_per_site(store: StateStore) -> None:
    store.set_health("acme", "healthy")
    store.set_health("other", "stale")
    assert len(store.transitions_for("acme")) == 1
    assert len(store.transitions_for("other")) == 1


def test_transitions_for_since_filters_by_time() -> None:
    times = iter(
        [
            datetime(2026, 1, 1, tzinfo=timezone.utc),  # None -> healthy
            datetime(2026, 1, 2, tzinfo=timezone.utc),  # healthy -> stale
            datetime(2026, 1, 3, tzinfo=timezone.utc),  # stale -> healthy
        ]
    )
    s = StateStore(":memory:", clock=lambda: next(times))
    s.set_health("acme", "healthy")
    s.set_health("acme", "stale")
    s.set_health("acme", "healthy")
    recent = s.transitions_for(
        "acme", since=datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
    )
    assert [(t.from_state, t.to_state) for t in recent] == [("stale", "healthy")]
    s.close()


def test_transitions_persist_across_reopen(tmp_path: object) -> None:
    db = f"{tmp_path}/state.db"  # type: ignore[str-bytes-safe]
    s1 = StateStore(db)
    s1.set_health("acme", "healthy")
    s1.set_health("acme", "stale")
    s1.close()

    s2 = StateStore(db)
    assert len(s2.transitions_for("acme")) == 2
    s2.close()


# -- run signals (§4.8 partial-drift telemetry, P0) --------------------------


def test_signals_default_to_none(store: StateStore) -> None:
    run_id = store.start_run("acme")
    store.finish_run(run_id, items_found=5, new_items=2, errors=0, pages_fetched=1)
    rec = store.last_run("acme")
    assert rec is not None and rec.signals is None


def test_signals_round_trip(store: StateStore) -> None:
    run_id = store.start_run("acme")
    store.finish_run(
        run_id,
        items_found=5,
        new_items=2,
        errors=0,
        pages_fetched=1,
        signals={"body_quality": 0.8, "incomplete": 1},
    )
    rec = store.last_run("acme")
    assert rec is not None and rec.signals == {"body_quality": 0.8, "incomplete": 1}


def test_signals_persist_across_reopen(tmp_path: object) -> None:
    db = f"{tmp_path}/state.db"  # type: ignore[str-bytes-safe]
    s1 = StateStore(db)
    rid = s1.start_run("acme")
    s1.finish_run(
        rid,
        items_found=1,
        new_items=1,
        errors=0,
        pages_fetched=1,
        signals={"body_quality": 0.5},
    )
    s1.close()

    s2 = StateStore(db)
    rec = s2.last_run("acme")
    assert rec is not None and rec.signals == {"body_quality": 0.5}
    s2.close()


def test_migration_adds_signals_column_to_legacy_db(tmp_path: object) -> None:
    # Simulate a DB created before the `signals` column existed: build the runs
    # table by hand without it, then open a StateStore and confirm signals work.
    db = f"{tmp_path}/legacy.db"  # type: ignore[str-bytes-safe]
    raw = sqlite3.connect(db)
    raw.executescript(
        """
        CREATE TABLE runs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            site          TEXT NOT NULL,
            started       TEXT NOT NULL,
            finished      TEXT,
            items_found   INTEGER NOT NULL DEFAULT 0,
            new_items     INTEGER NOT NULL DEFAULT 0,
            errors        INTEGER NOT NULL DEFAULT 0,
            pages_fetched INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO runs (site, started, finished, items_found)
        VALUES ('acme', '2026-01-01T00:00:00+00:00', '2026-01-01T00:01:00+00:00', 3);
        """
    )
    raw.commit()
    raw.close()

    s = StateStore(db)
    legacy = s.last_run("acme")  # pre-existing row reads back with signals=None
    assert legacy is not None and legacy.items_found == 3 and legacy.signals is None
    rid = s.start_run("acme")
    s.finish_run(
        rid,
        items_found=4,
        new_items=1,
        errors=0,
        pages_fetched=1,
        signals={"body_quality": 0.9},
    )
    rec = s.last_run("acme")
    assert rec is not None and rec.signals == {"body_quality": 0.9}
    s.close()


def test_migration_is_idempotent(tmp_path: object) -> None:
    db = f"{tmp_path}/state.db"  # type: ignore[str-bytes-safe]
    StateStore(db).close()
    # Re-opening (which re-runs _migrate) must not error or double-add the column.
    s = StateStore(db)
    cols = [row["name"] for row in s._conn.execute("PRAGMA table_info(runs)")]
    assert cols.count("signals") == 1
    s.close()


# -- adaptive learned interval (§4.9 hit-rate controller, P1) -----------------


def test_interval_is_none_until_set(store: StateStore) -> None:
    assert store.get_interval("acme") is None


def test_set_then_get_interval(store: StateStore) -> None:
    store.set_interval("acme", 1800.0)
    assert store.get_interval("acme") == 1800.0


def test_set_interval_overwrites_prior(store: StateStore) -> None:
    store.set_interval("acme", 1800.0)
    store.set_interval("acme", 3600.0)
    assert store.get_interval("acme") == 3600.0


def test_interval_is_scoped_per_site(store: StateStore) -> None:
    store.set_interval("acme", 900.0)
    assert store.get_interval("other") is None


def test_interval_persists_across_reopen(tmp_path: object) -> None:
    db = f"{tmp_path}/state.db"  # type: ignore[str-bytes-safe]
    s1 = StateStore(db)
    s1.set_interval("acme", 1234.0)
    s1.close()

    s2 = StateStore(db)
    assert s2.get_interval("acme") == 1234.0
    s2.close()
