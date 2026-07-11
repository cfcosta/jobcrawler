"""Tests for the crawl-state store (§4.7).

The store is decoupled persistence with two eras of state. The retained legacy surfaces
(a per-site set of seen/ingested canonical URLs, run history, health, learned interval)
hold no policy — the runner decides *when* to mark seen vs ingested; the store records
the states and never silently downgrades an ingested URL. The tracker surfaces added for
jobs own the *position lifecycle* (first_seen/last_seen/missing_streak/open→closed diff,
reopen, expiry) and the *match cache* (content×profile×model×prompt-keyed verdicts plus a
per-UTC-day spend counter) that make LLM matching idempotent and budget-bounded.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest

from jobcrawler.state import PositionRecord, RunRecord, StateStore

# The three NOT-NULL job fields every ``upsert_position`` must carry (a ``Job`` requires
# non-empty title/company/location); optional columns default to NULL when omitted.
FIELDS: dict[str, object] = {
    "title": "Senior Backend Engineer",
    "company": "Acme",
    "location": "Remote",
}


class _MutableClock:
    """A hand-advanced clock so a test can prove last_seen/closed_at move with wall time."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


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


def test_new_positions_since_counts_first_seen(store: StateStore) -> None:
    # The §16 dashboard "new today" delta: a single COUNT over first_seen.
    store.upsert_position(
        "s",
        "https://x.test/a",
        fields={"title": "t", "company": "c", "location": "l"},
        content_hash="h1",
    )
    assert store.new_positions_since("2000-01-01T00:00:00+00:00") == 1
    assert store.new_positions_since("2999-01-01T00:00:00+00:00") == 0


def test_health_updated_by_site_maps_latest_attempt_times() -> None:
    # The §4.2/§4.8 sweep-fairness key: one bulk query of each site's latest health
    # timestamp, bumped on every re-attempt, absent for never-attempted sites.
    now = {"t": datetime(2026, 7, 8, 10, 0, tzinfo=timezone.utc)}
    s = StateStore(":memory:", clock=lambda: now["t"])
    s.set_health("a", "generation_failed")
    now["t"] = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    s.set_health("b", "healthy")

    first = s.health_updated_by_site()
    assert set(first) == {"a", "b"}  # never-attempted sites simply absent
    assert first["a"] < first["b"]  # ISO text orders chronologically

    now["t"] = datetime(2026, 7, 8, 13, 0, tzinfo=timezone.utc)
    s.set_health("a", "generation_failed")  # a re-attempt bumps the timestamp
    assert s.health_updated_by_site()["a"] > first["b"]
    s.close()


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


# -- fleet-wide transition counts (§4.11 P4c regression watcher) -------------


def test_count_transitions_to_is_fleet_wide() -> None:
    # Distinct sites entering the same state are all counted (the watcher asks "how many
    # sites newly failed generation", across the whole fleet, not per-site).
    times = iter([datetime(2026, 1, d, tzinfo=timezone.utc) for d in range(1, 6)])
    s = StateStore(":memory:", clock=lambda: next(times))
    s.set_health("a", "generation_failed")
    s.set_health("b", "generation_failed")
    s.set_health("c", "healthy")  # a different to_state — not counted
    since = datetime(2025, 12, 31, tzinfo=timezone.utc)
    assert s.count_transitions_to("generation_failed", since=since) == 2
    assert s.count_transitions_to("healthy", since=since) == 1
    s.close()


def test_count_transitions_to_windows_since_inclusive_until_exclusive() -> None:
    times = iter(
        [
            datetime(2026, 1, 1, tzinfo=timezone.utc),  # before the window
            datetime(2026, 1, 5, tzinfo=timezone.utc),  # == since → inside
            datetime(2026, 1, 8, tzinfo=timezone.utc),  # inside
            datetime(2026, 1, 10, tzinfo=timezone.utc),  # == until → excluded
        ]
    )
    s = StateStore(":memory:", clock=lambda: next(times))
    for site in ("a", "b", "c", "d"):
        s.set_health(site, "generation_failed")
    n = s.count_transitions_to(
        "generation_failed",
        since=datetime(2026, 1, 5, tzinfo=timezone.utc),
        until=datetime(2026, 1, 10, tzinfo=timezone.utc),
    )
    assert n == 2  # Jan 5 (inclusive) + Jan 8; Jan 1 before, Jan 10 (until) excluded
    s.close()


def test_count_transitions_to_is_zero_with_no_matching_rows(store: StateStore) -> None:
    since = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert store.count_transitions_to("generation_failed", since=since) == 0


def test_count_transitions_to_ignores_a_reasserted_state() -> None:
    # set_health logs a row only on a real change, so a site that stays failed across
    # several sweeps is counted once — the watcher measures *newly* failing sites.
    fixed = datetime(2026, 1, 2, tzinfo=timezone.utc)
    s = StateStore(":memory:", clock=lambda: fixed)
    s.set_health("a", "generation_failed")
    s.set_health("a", "generation_failed", bump_attempts=True)
    s.set_health("a", "generation_failed", bump_attempts=True)
    since = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert s.count_transitions_to("generation_failed", since=since) == 1
    s.close()


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


# -- fx rates: currency → USD conversion factors (§4.6) -----------------------


def test_fx_rates_empty_until_set(store: StateStore) -> None:
    assert store.get_fx_rates() == {}


def test_set_then_get_fx_rates(store: StateStore) -> None:
    store.set_fx_rates({"EUR": 1.14, "GBP": 1.32})
    assert store.get_fx_rates() == {"EUR": 1.14, "GBP": 1.32}


def test_set_fx_rates_upserts_per_currency(store: StateStore) -> None:
    store.set_fx_rates({"EUR": 1.14, "GBP": 1.32})
    store.set_fx_rates({"EUR": 1.20, "SEK": 0.10})
    assert store.get_fx_rates() == {"EUR": 1.20, "GBP": 1.32, "SEK": 0.10}


def test_set_fx_rates_empty_is_noop(store: StateStore) -> None:
    store.set_fx_rates({})
    assert store.get_fx_rates() == {}


def test_fx_rates_age_is_none_until_set(store: StateStore) -> None:
    assert store.fx_rates_age() is None


def test_fx_rates_age_tracks_elapsed_time() -> None:
    clock = _MutableClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    s = StateStore(":memory:", clock=clock)
    s.set_fx_rates({"EUR": 1.1})
    assert s.fx_rates_age() == timedelta(0)
    clock.now += timedelta(hours=6)
    assert s.fx_rates_age() == timedelta(hours=6)
    s.close()


def test_fx_rates_persist_across_reopen(tmp_path: object) -> None:
    db = f"{tmp_path}/state.db"  # type: ignore[str-bytes-safe]
    s1 = StateStore(db)
    s1.set_fx_rates({"EUR": 1.14})
    s1.close()

    s2 = StateStore(db)
    assert s2.get_fx_rates() == {"EUR": 1.14}
    s2.close()


def test_fx_table_is_static_majors_when_empty(store: StateStore) -> None:
    table = store.fx_table()
    assert {"USD", "EUR", "GBP", "BRL"} <= table.keys()
    assert table["USD"] == 1.0


def test_fx_table_overlays_live_rates_on_static_floor(store: StateStore) -> None:
    store.set_fx_rates({"EUR": 1.50, "SEK": 0.10})
    table = store.fx_table()
    assert table["EUR"] == 1.50  # live overrides the static major
    assert table["SEK"] == 0.10  # a currency the static floor lacks is now present
    assert "GBP" in table  # a static major absent from the live set is retained
    assert table["USD"] == 1.0


# -- position lifecycle: upsert new/changed/unchanged (§4.7 tracker) ----------


def test_upsert_new_row_returns_new(store: StateStore) -> None:
    assert store.upsert_position("acme", "u", fields=FIELDS, content_hash="h") == "new"
    rec = store.open_positions("acme")[0]
    assert isinstance(rec, PositionRecord)
    assert (rec.site, rec.url) == ("acme", "u")
    assert (rec.title, rec.company, rec.location) == (
        "Senior Backend Engineer",
        "Acme",
        "Remote",
    )
    # optional columns default to None; lifecycle/match defaults on a fresh row
    assert rec.discipline is None and rec.salary_min is None and rec.external_id is None
    assert rec.status == "open" and rec.missing_streak == 0 and rec.closed_at is None
    assert rec.match_score is None and rec.match_pending is False
    assert rec.first_seen.tzinfo is not None and rec.last_seen.tzinfo is not None


def test_upsert_same_hash_is_unchanged(store: StateStore) -> None:
    store.upsert_position("acme", "u", fields=FIELDS, content_hash="h")
    assert (
        store.upsert_position("acme", "u", fields=FIELDS, content_hash="h")
        == "unchanged"
    )


def test_upsert_different_hash_is_changed(store: StateStore) -> None:
    store.upsert_position("acme", "u", fields=FIELDS, content_hash="h1")
    result = store.upsert_position(
        "acme", "u", fields={**FIELDS, "title": "Staff Engineer"}, content_hash="h2"
    )
    assert result == "changed"


def test_upsert_is_scoped_per_site(store: StateStore) -> None:
    store.upsert_position("acme", "u", fields=FIELDS, content_hash="h")
    # same url under a different site is a distinct, brand-new position
    assert store.upsert_position("other", "u", fields=FIELDS, content_hash="h") == "new"


def test_upsert_persists_enrichment_and_salary_fields(store: StateStore) -> None:
    fields = {
        **FIELDS,
        "external_id": "ATS-42",
        "discipline": "backend",
        "level": "senior",
        "workplace": "remote",
        "salary_min": 152800.0,
        "salary_max": 259200.0,
        "salary_currency": "USD",
        "salary_period": "year",
        "salary_usd_min": 152800.0,
        "salary_usd_max": 259200.0,
        "employment_type": "full_time",
        "department": "Platform",
    }
    store.upsert_position("acme", "u", fields=fields, content_hash="h")
    rec = store.open_positions("acme")[0]
    assert (rec.discipline, rec.level, rec.workplace) == ("backend", "senior", "remote")
    assert (rec.salary_min, rec.salary_max) == (152800.0, 259200.0)
    assert (rec.salary_currency, rec.salary_period) == ("USD", "year")
    assert (rec.salary_usd_min, rec.salary_usd_max) == (152800.0, 259200.0)
    assert (rec.employment_type, rec.department, rec.external_id) == (
        "full_time",
        "Platform",
        "ATS-42",
    )


def test_upsert_persists_and_refreshes_apply_url(store: StateStore) -> None:
    # The canonical apply link rides the crawler ``fields`` dict like external_id: it is
    # stored on insert, read back on the detail record, and — being OUT of content_hash —
    # refreshes on a same-hash re-crawl without marking the row changed.
    store.upsert_position(
        "acme",
        "u",
        fields={**FIELDS, "apply_url": "https://apply.example/acme/42"},
        content_hash="h",
    )
    rec = store.get_position("acme", "u")
    assert rec is not None
    assert rec.apply_url == "https://apply.example/acme/42"
    result = store.upsert_position(
        "acme",
        "u",
        fields={**FIELDS, "apply_url": "https://apply.example/acme/42-v2"},
        content_hash="h",
    )
    assert result == "unchanged"
    rec = store.get_position("acme", "u")
    assert rec is not None
    assert rec.apply_url == "https://apply.example/acme/42-v2"


def test_upsert_roundtrips_datetime_fields_as_iso(store: StateStore) -> None:
    posted = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    expires = datetime(2026, 8, 1, tzinfo=timezone.utc)
    store.upsert_position(
        "acme",
        "u",
        fields={**FIELDS, "posted_at": posted, "expires_at": expires},
        content_hash="h",
    )
    rec = store.open_positions("acme")[0]
    assert rec.posted_at == posted and rec.expires_at == expires


def test_upsert_ignores_unknown_field_keys(store: StateStore) -> None:
    # A crawler may hand over keys the schema does not track (raw description, source);
    # they are dropped rather than crashing the insert.
    store.upsert_position(
        "acme",
        "u",
        fields={
            **FIELDS,
            "description": "a very long body",
            "bogus": 1,
            "source": "acme",
        },
        content_hash="h",
    )
    assert store.open_positions("acme")[0].title == "Senior Backend Engineer"


def test_changed_upsert_refreshes_stored_fields(store: StateStore) -> None:
    store.upsert_position(
        "acme", "u", fields={**FIELDS, "salary_min": 100000.0}, content_hash="h1"
    )
    store.upsert_position(
        "acme",
        "u",
        fields={**FIELDS, "title": "Staff Engineer", "salary_min": 150000.0},
        content_hash="h2",
    )
    rec = store.open_positions("acme")[0]
    assert rec.title == "Staff Engineer" and rec.salary_min == 150000.0


def test_unchanged_upsert_still_refreshes_non_hash_fields(store: StateStore) -> None:
    # Salary/expiry are not part of content_hash, so they can drift while the identity is
    # 'unchanged'; the store keeps the latest values without triggering a re-match.
    store.upsert_position(
        "acme", "u", fields={**FIELDS, "salary_min": 100000.0}, content_hash="h"
    )
    expires = datetime(2027, 1, 1, tzinfo=timezone.utc)
    result = store.upsert_position(
        "acme",
        "u",
        fields={**FIELDS, "salary_min": 120000.0, "expires_at": expires},
        content_hash="h",
    )
    assert result == "unchanged"
    rec = store.open_positions("acme")[0]
    assert rec.salary_min == 120000.0 and rec.expires_at == expires


def test_upsert_touches_last_seen_and_resets_missing_streak() -> None:
    clock = _MutableClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    s = StateStore(":memory:", clock=clock)
    s.upsert_position("acme", "u", fields=FIELDS, content_hash="h")
    # a miss bumps the streak (threshold high enough not to close)
    clock.now = datetime(2026, 1, 2, tzinfo=timezone.utc)
    s.close_missing("acme", set(), threshold=5)
    assert s.open_positions("acme")[0].missing_streak == 1
    # reappearing advances last_seen and zeroes the streak
    clock.now = datetime(2026, 1, 3, tzinfo=timezone.utc)
    assert (
        s.upsert_position("acme", "u", fields=FIELDS, content_hash="h") == "unchanged"
    )
    rec = s.open_positions("acme")[0]
    assert rec.last_seen == datetime(2026, 1, 3, tzinfo=timezone.utc)
    assert rec.missing_streak == 0
    s.close()


# -- reopen after close (§4.7: a churned-then-returned role) ------------------


def test_upsert_reopens_a_closed_row_as_changed(store: StateStore) -> None:
    store.upsert_position("acme", "u", fields=FIELDS, content_hash="h")
    assert store.close_missing("acme", set(), threshold=1) == ["u"]
    assert store.open_positions("acme") == []  # closed → not open
    # the same posting reappears with identical content
    assert (
        store.upsert_position("acme", "u", fields=FIELDS, content_hash="h") == "changed"
    )
    rec = store.open_positions("acme")[0]
    assert rec.status == "open" and rec.closed_at is None and rec.missing_streak == 0


def test_reopen_preserves_the_match_verdict(store: StateStore) -> None:
    # The reopen must not clear match_* — combined with the content-hash cache this is
    # what stops a churned-then-returned unchanged job from re-billing the matcher.
    store.upsert_position("acme", "u", fields=FIELDS, content_hash="h")
    store.set_match("acme", "u", score=88, verdict="good", reasons="strong fit")
    store.close_missing("acme", set(), threshold=1)
    store.upsert_position("acme", "u", fields=FIELDS, content_hash="h")  # reopen
    rec = store.open_positions("acme")[0]
    assert rec.match_score == 88 and rec.match_verdict == "good"
    assert rec.match_reasons == "strong fit" and rec.match_pending is False


# -- close_missing: the enumeration diff (§4.7) ------------------------------


def test_close_missing_threshold_boundary(store: StateStore) -> None:
    store.upsert_position("acme", "u", fields=FIELDS, content_hash="h")
    # one miss under threshold=2 bumps but does not close
    assert store.close_missing("acme", set(), threshold=2) == []
    assert [r.url for r in store.open_positions("acme")] == ["u"]
    assert store.open_positions("acme")[0].missing_streak == 1
    # the second consecutive miss reaches the threshold and closes
    assert store.close_missing("acme", set(), threshold=2) == ["u"]
    assert store.open_positions("acme") == []


def test_close_missing_never_touches_present_rows(store: StateStore) -> None:
    store.upsert_position("acme", "a", fields=FIELDS, content_hash="h")
    store.upsert_position("acme", "b", fields=FIELDS, content_hash="h")
    # 'a' is present this crawl, 'b' is absent; threshold=1 closes only 'b'
    assert store.close_missing("acme", {"a"}, threshold=1) == ["b"]
    assert store.open_urls("acme", limit=10) == ["a"]
    kept = store.open_positions("acme")[0]
    assert kept.url == "a" and kept.missing_streak == 0  # present → streak untouched


def test_close_missing_is_scoped_per_site(store: StateStore) -> None:
    store.upsert_position("acme", "u", fields=FIELDS, content_hash="h")
    store.upsert_position("other", "u", fields=FIELDS, content_hash="h")
    # diffing 'acme' with an empty present set must not close 'other'
    assert store.close_missing("acme", set(), threshold=1) == ["u"]
    assert [r.url for r in store.open_positions("other")] == ["u"]


def test_close_missing_ignores_already_closed_rows(store: StateStore) -> None:
    store.upsert_position("acme", "u", fields=FIELDS, content_hash="h")
    store.mark_closed("acme", "u", reason="filled")
    # already closed → not re-counted, not re-returned
    assert store.close_missing("acme", set(), threshold=1) == []


def test_close_missing_returns_empty_when_all_present(store: StateStore) -> None:
    store.upsert_position("acme", "a", fields=FIELDS, content_hash="h")
    store.upsert_position("acme", "b", fields=FIELDS, content_hash="h")
    assert store.close_missing("acme", {"a", "b"}, threshold=1) == []


def test_close_missing_resets_the_streak_of_a_reseen_row(store: StateStore) -> None:
    # A row can accrue a streak while absent, then be re-seen ALIVE this crawl without an
    # upsert resetting it — the runner passes such a URL (e.g. a momentarily-truncated
    # re-serve the completeness floor skipped) in the present/seen set. close_missing must
    # treat it as alive: reset the streak to 0, never age it toward closure (finding: the
    # completeness floor must not punch a hole in the closure set and silently close a live
    # posting).
    store.upsert_position("acme", "u", fields=FIELDS, content_hash="h")
    assert store.close_missing("acme", set(), threshold=3) == []  # absent → streak 1
    assert store.open_positions("acme")[0].missing_streak == 1
    # re-seen alive this crawl (present) though not re-upserted: streak resets, stays open
    assert store.close_missing("acme", {"u"}, threshold=3) == []
    assert store.open_positions("acme")[0].missing_streak == 0
    # and having reset, a single later miss can't tip it over the threshold
    assert store.close_missing("acme", set(), threshold=3) == []
    assert store.open_positions("acme")[0].missing_streak == 1


# -- explicit / expiry-driven closure (§4.7) ---------------------------------


def test_mark_closed_closes_an_open_position(store: StateStore) -> None:
    store.upsert_position("acme", "u", fields=FIELDS, content_hash="h")
    store.mark_closed("acme", "u", reason="no longer accepting applications")
    assert store.open_positions("acme") == []
    assert store.position_counts()["closed"] == 1


def test_mark_closed_stamps_closed_at_and_is_idempotent() -> None:
    first = datetime(2026, 7, 2, tzinfo=timezone.utc)
    clock = _MutableClock(first)
    s = StateStore(":memory:", clock=clock)
    s.upsert_position("acme", "u", fields=FIELDS, content_hash="h")
    s.mark_closed("acme", "u", reason="filled")
    row = s._conn.execute(
        "SELECT status, closed_at FROM positions WHERE site='acme' AND url='u'"
    ).fetchone()
    assert row["status"] == "closed" and row["closed_at"] == first.isoformat()
    # a re-close on the already-closed row is a no-op (closed_at is not moved)
    clock.now = datetime(2026, 7, 3, tzinfo=timezone.utc)
    s.mark_closed("acme", "u", reason="filled again")
    again = s._conn.execute(
        "SELECT closed_at FROM positions WHERE site='acme' AND url='u'"
    ).fetchone()
    assert again["closed_at"] == first.isoformat()
    s.close()


def test_expire_due_closes_only_past_expiries() -> None:
    now = datetime(2026, 7, 2, tzinfo=timezone.utc)
    s = StateStore(":memory:", clock=lambda: now)
    s.upsert_position(
        "acme",
        "past",
        fields={**FIELDS, "expires_at": datetime(2026, 1, 1, tzinfo=timezone.utc)},
        content_hash="h",
    )
    s.upsert_position(
        "acme",
        "future",
        fields={**FIELDS, "expires_at": datetime(2027, 1, 1, tzinfo=timezone.utc)},
        content_hash="h",
    )
    s.upsert_position("acme", "none", fields=FIELDS, content_hash="h")  # no expiry
    assert s.expire_due() == 1
    assert set(s.open_urls("acme", limit=10)) == {"future", "none"}
    s.close()


def test_expire_due_is_zero_with_no_due_rows(store: StateStore) -> None:
    store.upsert_position("acme", "u", fields=FIELDS, content_hash="h")
    assert store.expire_due() == 0


def test_expire_due_normalizes_offsets_before_the_lexicographic_compare() -> None:
    # expire_due filters `expires_at < now` as ISO TEXT, so a non-UTC offset must be
    # normalized to UTC at the write boundary or the compare is unsound (finding: a naive
    # lexicographic compare expires a FUTURE negative-offset posting early and lets a PAST
    # positive-offset one linger). _sql_value/_utc_iso normalize on store; lock that in with
    # two rows whose wall-clock text sorts the OPPOSITE way from their true instants.
    now = datetime(2026, 7, 2, 20, 0, tzinfo=timezone.utc)  # '...T20:00:00+00:00'
    s = StateStore(":memory:", clock=lambda: now)
    # 18:00-05:00 == 23:00Z, a FUTURE instant; its text ('...T18') sorts BEFORE now's.
    s.upsert_position(
        "acme",
        "future_neg",
        fields={
            **FIELDS,
            "expires_at": datetime(
                2026, 7, 2, 18, 0, tzinfo=timezone(timedelta(hours=-5))
            ),
        },
        content_hash="h",
    )
    # 23:00+05:00 == 18:00Z, a PAST instant; its text ('...T23') sorts AFTER now's.
    s.upsert_position(
        "acme",
        "past_pos",
        fields={
            **FIELDS,
            "expires_at": datetime(
                2026, 7, 2, 23, 0, tzinfo=timezone(timedelta(hours=5))
            ),
        },
        content_hash="h",
    )
    assert s.expire_due() == 1  # only the truly-past instant closes
    assert set(s.open_urls("acme", limit=10)) == {"future_neg"}
    s.close()


# -- open queries (§4.7 status / recheck surfaces) ---------------------------


def test_open_positions_excludes_closed_and_can_span_the_fleet(
    store: StateStore,
) -> None:
    store.upsert_position("acme", "a", fields=FIELDS, content_hash="h")
    store.upsert_position("beta", "b", fields=FIELDS, content_hash="h")
    store.upsert_position("acme", "c", fields=FIELDS, content_hash="h")
    store.mark_closed("acme", "c", reason="filled")
    assert {r.url for r in store.open_positions()} == {"a", "b"}  # fleet-wide
    assert {r.url for r in store.open_positions("acme")} == {"a"}  # per-site


def test_open_urls_returns_open_only(store: StateStore) -> None:
    store.upsert_position("acme", "a", fields=FIELDS, content_hash="h")
    store.upsert_position("acme", "b", fields=FIELDS, content_hash="h")
    store.mark_closed("acme", "b", reason="x")
    assert store.open_urls("acme", limit=10) == ["a"]


def test_note_rechecked_rotates_a_url_off_the_recheck_head() -> None:
    # open_urls serves the stalest last_seen first (§4.9 recheck order). A recheck that
    # does not close the URL must record the attempt so the row rotates to the BACK —
    # otherwise a windowed source's frozen-last_seen URL (esp. a poison one that times
    # out and is never closed) stays permanently head-of-line, re-fetched every sweep.
    clock = _MutableClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    s = StateStore(":memory:", clock=clock)
    s.upsert_position("acme", "old", fields=FIELDS, content_hash="h")
    clock.now += timedelta(hours=1)
    s.upsert_position("acme", "fresh", fields=FIELDS, content_hash="h")
    assert s.open_urls("acme", limit=10) == ["old", "fresh"]  # stalest leads

    clock.now += timedelta(hours=1)
    s.note_rechecked("acme", "old")  # the sweep attempted "old"; rotate it back
    assert s.open_urls("acme", limit=10) == ["fresh", "old"]

    # Scoped to open rows: a closed URL has left the recheck set, so noting it is a no-op.
    s.mark_closed("acme", "fresh", reason="x")
    clock.now += timedelta(hours=1)
    s.note_rechecked("acme", "fresh")
    assert s.open_urls("acme", limit=10) == ["old"]
    s.close()


# -- match verdicts, pending backlog, counts (§6) ----------------------------


def test_matches_filters_by_min_score_and_orders_best_first(store: StateStore) -> None:
    for url, score in [("a", 90), ("b", 60), ("c", 75)]:
        store.upsert_position("acme", url, fields=FIELDS, content_hash=url)
        store.set_match("acme", url, score=score, verdict="x", reasons="")
    assert [(r.url, r.match_score) for r in store.matches(70)] == [("a", 90), ("c", 75)]


def test_matches_excludes_closed_positions(store: StateStore) -> None:
    store.upsert_position("acme", "u", fields=FIELDS, content_hash="h")
    store.set_match("acme", "u", score=95, verdict="strong", reasons="")
    store.mark_closed("acme", "u", reason="filled")
    assert store.matches(70) == []


def test_matches_exclude_applied_drops_positions_in_the_pipeline(
    store: StateStore,
) -> None:
    # Two scored open positions; applying to the top one stamps applied_at. With
    # exclude_applied the "top matches" view drops it (a lead you're already pursuing)
    # while the default listing still returns the full scored corpus.
    for url, score in [("a", 95), ("b", 80)]:
        store.upsert_position("acme", url, fields=FIELDS, content_hash=url)
        store.set_match("acme", url, score=score, verdict="strong", reasons="")
    store.set_track_status("acme", "a", "applied", stamp_applied=True)

    assert [r.url for r in store.matches(70)] == ["a", "b"]  # default: unchanged
    assert [r.url for r in store.matches(70, exclude_applied=True)] == ["b"]


def test_matches_exclude_applied_keeps_interested_not_yet_applied(
    store: StateStore,
) -> None:
    # "interested" is a pre-application stage (no applied_at stamp), so it is still a
    # live lead and must survive the exclude_applied filter.
    store.upsert_position("acme", "u", fields=FIELDS, content_hash="h")
    store.set_match("acme", "u", score=88, verdict="strong", reasons="")
    store.set_track_status("acme", "u", "interested", stamp_applied=False)
    assert [r.url for r in store.matches(70, exclude_applied=True)] == ["u"]


def test_matches_exclude_dismissed_drops_hidden_positions(store: StateStore) -> None:
    # Dismissing a position (the §16 hide flag, independent of track_status) drops it
    # from the exclude_dismissed view but leaves the default listing untouched.
    for url, score in [("a", 95), ("b", 80)]:
        store.upsert_position("acme", url, fields=FIELDS, content_hash=url)
        store.set_match("acme", url, score=score, verdict="strong", reasons="")
    store.set_dismissed("acme", "a", True)

    assert [r.url for r in store.matches(70)] == ["a", "b"]  # default: unchanged
    assert [r.url for r in store.matches(70, exclude_dismissed=True)] == ["b"]


def test_matches_attainable_only_wants_anywhere_or_relocation(
    store: StateStore,
) -> None:
    # The dashboard shortlist's mobility gate: a geo-fenced remote role with no
    # relocation offer is unattainable however well it scores, and a row §4.6 never
    # classified (NULL remote_scope/relocation) is absence of evidence, not reach.
    for url, extra, score in [
        ("anywhere", {"remote_scope": "anywhere"}, 70),
        ("relo", {"remote_scope": "restricted", "relocation": "offered"}, 65),
        ("fenced", {"remote_scope": "restricted", "relocation": "unspecified"}, 95),
        ("silent", {}, 90),
    ]:
        store.upsert_position("acme", url, fields={**FIELDS, **extra}, content_hash=url)
        store.set_match("acme", url, score=score, verdict="x", reasons="")

    assert [r.url for r in store.matches(0)] == [
        "fenced",
        "silent",
        "anywhere",
        "relo",
    ]  # default: unchanged
    assert [r.url for r in store.matches(0, attainable_only=True)] == [
        "anywhere",
        "relo",
    ]


def _upsert_scored(
    store: StateStore, url: str, *, company: str, canonical: str, score: int = 90
) -> None:
    """Upsert one scored open position carrying an explicit §16 company dedup key."""
    store.upsert_position(
        "acme",
        url,
        fields={
            "title": "Engineer",
            "company": company,
            "company_canonical": canonical,
            "location": "Remote",
        },
        content_hash=url,
    )
    store.set_match("acme", url, score=score, verdict="strong", reasons="")


def test_matches_exclude_company_applied_drops_sibling_openings(
    store: StateStore,
) -> None:
    # Most companies accept one application company-wide (§16): once you apply to any role at
    # Acme, its *other* open roles are noise. A different company's role is untouched.
    _upsert_scored(store, "acme-1", company="Acme", canonical="acme")
    _upsert_scored(store, "acme-2", company="Acme", canonical="acme")
    _upsert_scored(store, "globex-1", company="Globex", canonical="globex")
    store.set_track_status("acme", "acme-1", "applied", stamp_applied=True)

    # default listing is unchanged — the full scored corpus
    assert {r.url for r in store.matches(70)} == {"acme-1", "acme-2", "globex-1"}
    # company-applied exclusion removes both Acme rows, leaving the other company's lead
    assert [r.url for r in store.matches(70, exclude_company_applied=True)] == [
        "globex-1"
    ]


def test_matches_exclude_company_applied_groups_surface_variants(
    store: StateStore,
) -> None:
    # "Acme" and "Acme, Inc." share the canonical key, so applying to one hides the other —
    # the whole point of a normalized key over a raw string match.
    _upsert_scored(store, "plain", company="Acme", canonical="acme")
    _upsert_scored(store, "inc", company="Acme, Inc.", canonical="acme")
    store.set_track_status("acme", "inc", "applied", stamp_applied=True)

    assert store.matches(70, exclude_company_applied=True) == []


def test_search_positions_filters_by_company_canonical(store: StateStore) -> None:
    # The "positions at this company" link (#51) groups on the canonical dedup key, so
    # surface variants ("Acme"/"Acme, Inc.") collapse to one company and a different
    # company is excluded — an exact key match, not a fuzzy company-name substring.
    _upsert_scored(store, "plain", company="Acme", canonical="acme")
    _upsert_scored(store, "inc", company="Acme, Inc.", canonical="acme")
    _upsert_scored(store, "globex", company="Globex", canonical="globex")

    assert {r.url for r in store.search_positions(company_canonical="acme")} == {
        "plain",
        "inc",
    }
    assert [r.url for r in store.search_positions(company_canonical="globex")] == [
        "globex"
    ]
    assert store.search_positions(company_canonical="absent") == []


def test_matches_exclude_company_applied_falls_back_to_lower_company(
    store: StateStore,
) -> None:
    # Rows written before the company_canonical column read NULL. The grouping COALESCEs to
    # lower(company), so same-company dedup still holds until they're re-crawled.
    for url in ["a", "b"]:
        store.upsert_position(
            "acme", url, fields=FIELDS, content_hash=url
        )  # no canonical
        store.set_match("acme", url, score=90, verdict="strong", reasons="")
    store.set_track_status("acme", "a", "applied", stamp_applied=True)

    assert store.matches(70, exclude_company_applied=True) == []


def test_matches_exclude_company_applied_empty_key_never_cross_excludes(
    store: StateStore,
) -> None:
    # Defensive: an unusable (empty) company key must gate nothing — not even itself — so a
    # blank company can't silently swallow the whole board.
    for url in ["a", "b"]:
        store.upsert_position(
            "acme",
            url,
            fields={
                "title": "E",
                "company": "",
                "company_canonical": "",
                "location": "R",
            },
            content_hash=url,
        )
        store.set_match("acme", url, score=90, verdict="strong", reasons="")
    store.set_track_status("acme", "a", "applied", stamp_applied=True)

    assert {r.url for r in store.matches(70, exclude_company_applied=True)} == {
        "a",
        "b",
    }


def test_upsert_persists_company_canonical(store: StateStore) -> None:
    _upsert_scored(store, "u", company="Acme Inc", canonical="acme")
    (rec,) = store.matches(70)
    assert rec.company == "Acme Inc"
    assert rec.company_canonical == "acme"


# -- §16 Applications view: applied filter + per-stage breakdown -------------


def test_search_positions_applied_filter_keeps_only_applied_rows(
    store: StateStore,
) -> None:
    # Three postings in different personal stages; only the one with an application on
    # record (applied_at stamped) is an "application".
    for url in ("applied", "interested", "untouched"):
        store.upsert_position("acme", url, fields=FIELDS, content_hash=url)
    store.set_track_status("acme", "applied", "applied", stamp_applied=True)
    store.set_track_status("acme", "interested", "interested", stamp_applied=False)

    assert [r.url for r in store.search_positions(applied=True)] == ["applied"]

    # The stamp survives a later terminal outcome — a rejected application is still one you
    # applied to, so it stays in the view (applied_at is never cleared).
    store.set_track_status("acme", "applied", "rejected", stamp_applied=False)
    assert [r.url for r in store.search_positions(applied=True)] == ["applied"]
    # A stage filter narrows within the applied set.
    assert store.search_positions(applied=True, track_status="applied") == []
    assert [
        r.url for r in store.search_positions(applied=True, track_status="rejected")
    ] == ["applied"]


def test_applied_counts_breaks_down_only_applied_rows(store: StateStore) -> None:
    for url in ("a", "b", "c", "never"):
        store.upsert_position("acme", url, fields=FIELDS, content_hash=url)
    store.set_track_status("acme", "a", "applied", stamp_applied=True)
    store.set_track_status("acme", "b", "interviewing", stamp_applied=True)
    store.set_track_status("acme", "c", "interviewing", stamp_applied=True)
    # Rejected *before* applying (no stamp) — must not count as an application.
    store.set_track_status("acme", "never", "rejected", stamp_applied=False)

    counts = store.applied_counts()
    assert counts == {"applied": 1, "interviewing": 2}
    assert sum(counts.values()) == len(store.search_positions(applied=True))


def test_applied_company_exclusion_is_indexed(store: StateStore) -> None:
    # Perf guard (§16): the applied-company exclusion is a correlated NOT EXISTS over the
    # derived company key restricted to applied rows. Unindexed it is O(N²) — minutes on a
    # multi-thousand-row corpus. A PARTIAL EXPRESSION index on that key (applied rows only)
    # is what lets the planner index-search per row instead of scanning the whole table.
    rows = store._conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' "
        "AND name='positions_applied_company_key'"
    ).fetchall()
    assert rows, "positions_applied_company_key index missing"
    ddl = str(rows[0]["sql"])
    assert (
        "applied_at IS NOT NULL" in ddl
    )  # partial: indexes only the (few) applied rows
    assert "company" in ddl.lower()  # keyed on the derived company-name expression
    # and the planner actually uses it for the exclusion subquery (no full p2 scan)
    plan = store._conn.execute(
        "EXPLAIN QUERY PLAN SELECT 1 FROM positions WHERE NOT EXISTS ("
        "SELECT 1 FROM positions p2 WHERE "
        "COALESCE(NULLIF(p2.company_canonical, ''), lower(p2.company)) "
        "= COALESCE(NULLIF(positions.company_canonical, ''), lower(positions.company)) "
        "AND COALESCE(NULLIF(p2.company_canonical, ''), lower(p2.company)) != '' "
        "AND p2.applied_at IS NOT NULL)"
    ).fetchall()
    detail = " ".join(str(r["detail"]) for r in plan)
    assert "positions_applied_company_key" in detail, f"index not used: {detail}"


# -- §6 freshness-decay effective score --------------------------------------


def _seed_scored(store: StateStore, url: str, score: int, age_days: float) -> None:
    now = store._clock()
    store.upsert_position(
        "b",
        url,
        fields={
            "title": "T",
            "company": "Co" + url,
            "location": "R",
            "posted_at": (now - timedelta(days=age_days)).isoformat(),
        },
        content_hash=url,
    )
    store.set_match("b", url, score=score, verdict="strong", reasons="[]")


def test_effective_score_decays_with_age_but_a_great_fit_stays_represented() -> None:
    now = datetime(2026, 7, 8, tzinfo=timezone.utc)
    s = StateStore(":memory:", clock=lambda: now)
    try:
        _seed_scored(s, "fresh", 80, 0)
        _seed_scored(s, "old", 80, 120)
        _seed_scored(s, "ancient_great", 95, 3650)  # ~10y: floored
        by = {m.url: m for m in s.matches(0, limit=10)}
        fresh = by["fresh"].effective_score
        old = by["old"].effective_score
        great = by["ancient_great"].effective_score
        assert fresh is not None and old is not None and great is not None
        # a fresh posting keeps its full fit; an equally-good older one decays below it
        assert round(fresh) == 80
        assert old < fresh
        assert [m.url for m in s.matches(0, limit=10)][
            0
        ] == "fresh"  # newer ranks higher
        # ...but a great fit is still represented: never below floor(0.6) × base
        assert great >= 0.6 * 95 - 0.01
    finally:
        s.close()


def test_matches_and_min_score_use_the_effective_score() -> None:
    # A threshold gates on the freshness-decayed score, so an old strong fit can drop out of
    # a high-min view while a fresh one of the same base stays — but both remain in matches(0).
    now = datetime(2026, 7, 8, tzinfo=timezone.utc)
    s = StateStore(":memory:", clock=lambda: now)
    try:
        _seed_scored(s, "fresh85", 85, 0)
        _seed_scored(s, "old85", 85, 365)
        assert [m.url for m in s.matches(80, limit=10)] == ["fresh85"]
        assert {m.url for m in s.matches(0, limit=10)} == {"fresh85", "old85"}
        # search_positions honors it too (sort + min_score)
        assert [p.url for p in s.search_positions(sort="score")][0] == "fresh85"
        assert [p.url for p in s.search_positions(min_score=80)] == ["fresh85"]
    finally:
        s.close()


def test_score_freshness_floor_of_one_disables_decay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JOBCRAWLER_SCORE_FRESHNESS_FLOOR", "1.0")
    now = datetime(2026, 7, 8, tzinfo=timezone.utc)
    s = StateStore(":memory:", clock=lambda: now)
    try:
        _seed_scored(s, "old", 90, 365)
        (m,) = s.matches(0, limit=10)
        eff = m.effective_score
        assert (
            eff is not None and round(eff) == 90
        )  # floor 1.0 → effective == base, no decay
    finally:
        s.close()


# -- §4.2 crawler-generation log ---------------------------------------------


def test_generation_log_records_and_reads_back(store: StateStore) -> None:
    store.record_generation(
        "acme",
        outcome="failed",
        reason="no candidate produced",
        llm_log=[
            {
                "model": "m",
                "prompt": "generate",
                "completion": None,
                "error": "OSError: [Errno 24] Too many open files",
            },
        ],
    )
    store.record_generation("beta", outcome="promoted", reason="ok", score=0.94)

    rows = store.generation_log()
    assert [r["site"] for r in rows] == ["beta", "acme"]  # newest first
    (acme,) = store.generation_log("acme")
    assert acme["outcome"] == "failed"
    assert acme["reason"] == "no candidate produced"
    assert acme["llm_calls"] == 1
    # the captured LLM interaction round-trips as JSON, error and all
    (call,) = json.loads(str(acme["llm_log"]))
    assert call["error"].endswith("Too many open files")
    # a clean success carries its score and no log
    (beta,) = store.generation_log("beta")
    assert beta["score"] == 0.94 and beta["llm_calls"] == 0


def test_generation_log_retention_prunes_oldest(store: StateStore) -> None:
    for i in range(6):
        store.record_generation(f"s{i}", outcome="failed", retain=3)
    rows = store.generation_log(limit=100)
    assert len(rows) == 3  # only the newest 3 survive
    assert [r["site"] for r in rows] == ["s5", "s4", "s3"]


# -- §4.7 LLM extraction setters + cache -------------------------------------


def test_set_salary_fills_an_empty_row_and_reports_it(store: StateStore) -> None:
    store.upsert_position("acme", "u", fields=FIELDS, content_hash="h")  # no salary
    filled = store.set_salary(
        "acme",
        "u",
        salary_min=120000.0,
        salary_max=160000.0,
        salary_currency="USD",
        salary_period="year",
    )
    assert filled is True
    rec = store.open_positions("acme")[0]
    assert (rec.salary_min, rec.salary_max) == (120000.0, 160000.0)
    assert (rec.salary_currency, rec.salary_period) == ("USD", "year")


def test_set_salary_never_clobbers_a_source_salary(store: StateStore) -> None:
    # A crawler/§4.6 salary on the row wins: the guard refuses the LLM backfill and the
    # existing figures survive untouched.
    store.upsert_position(
        "acme",
        "u",
        fields={**FIELDS, "salary_min": 90000.0, "salary_max": 90000.0},
        content_hash="h",
    )
    filled = store.set_salary(
        "acme",
        "u",
        salary_min=200000.0,
        salary_max=200000.0,
        salary_currency="USD",
        salary_period="year",
    )
    assert filled is False
    rec = store.open_positions("acme")[0]
    assert (rec.salary_min, rec.salary_max) == (90000.0, 90000.0)


def test_set_salary_missing_row_is_a_noop(store: StateStore) -> None:
    assert (
        store.set_salary(
            "acme",
            "nope",
            salary_min=1.0,
            salary_max=2.0,
            salary_currency="USD",
            salary_period="year",
        )
        is False
    )


def test_set_hiring_company_repoints_the_canonical_dedup_key(store: StateStore) -> None:
    # A "Confidential" listing whose text names the real employer: the cleaner name is
    # recorded and the §16 dedup key is repointed at its canonical form.
    _upsert_scored(store, "u", company="Confidential", canonical="confidential")
    store.set_hiring_company("acme", "u", name="Globex Corp", canonical="globex")
    rec = store.open_positions("acme")[0]
    assert rec.hiring_company == "Globex Corp"
    assert rec.company_canonical == "globex"  # dedup now groups on the real employer
    assert rec.company == "Confidential"  # the board field is left as crawled


def test_set_hiring_company_empty_canonical_keeps_the_crawl_key(
    store: StateStore,
) -> None:
    # An employer name that canonicalises to nothing must not wipe the crawl-derived key.
    _upsert_scored(store, "u", company="Acme", canonical="acme")
    store.set_hiring_company("acme", "u", name="The Group", canonical="")
    rec = store.open_positions("acme")[0]
    assert rec.hiring_company == "The Group"
    assert rec.company_canonical == "acme"  # unchanged


def test_extract_cache_roundtrips_and_is_keyed_by_model_and_version(
    store: StateStore,
) -> None:
    assert store.cached_extract("h", "m", "extract-v1") is None
    store.cache_extract("h", "m", "extract-v1", '{"salary_min": 1}')
    assert store.cached_extract("h", "m", "extract-v1") == '{"salary_min": 1}'
    # a different model or version is a distinct entry (a miss), never a stale hit
    assert store.cached_extract("h", "other", "extract-v1") is None
    assert store.cached_extract("h", "m", "extract-v2") is None
    # re-caching the same key overwrites (ON CONFLICT DO UPDATE)
    store.cache_extract("h", "m", "extract-v1", '{"salary_min": 2}')
    assert store.cached_extract("h", "m", "extract-v1") == '{"salary_min": 2}'


def test_set_match_records_verdict_and_clears_pending() -> None:
    at = datetime(2026, 7, 2, 9, 30, tzinfo=timezone.utc)
    s = StateStore(":memory:", clock=lambda: at)
    s.upsert_position("acme", "u", fields=FIELDS, content_hash="h")
    s.set_match_pending("acme", "u")
    assert [r.url for r in s.pending_matches(limit=10)] == ["u"]
    s.set_match("acme", "u", score=80, verdict="good", reasons="fits well")
    rec = s.open_positions("acme")[0]
    assert rec.match_score == 80 and rec.match_verdict == "good"
    assert rec.match_reasons == "fits well" and rec.matched_at == at
    assert rec.match_pending is False
    assert s.pending_matches(limit=10) == []  # scoring drained it
    s.close()


def test_set_match_pending_leaves_prior_score_intact(store: StateStore) -> None:
    # Re-pending a scored job (e.g. a rematch beyond the cap) defers it without erasing
    # the score it already carries.
    store.upsert_position("acme", "u", fields=FIELDS, content_hash="h")
    store.set_match("acme", "u", score=72, verdict="good", reasons="")
    store.set_match_pending("acme", "u")
    rec = store.open_positions("acme")[0]
    assert rec.match_score == 72 and rec.match_pending is True


def test_pending_matches_excludes_closed(store: StateStore) -> None:
    store.upsert_position("acme", "u", fields=FIELDS, content_hash="h")
    store.set_match_pending("acme", "u")
    store.mark_closed("acme", "u", reason="x")
    assert store.pending_matches(limit=10) == []


def test_pending_urls_returns_open_pending_oldest_first_and_bounded(
    store: StateStore,
) -> None:
    # The drain seam a later crawl uses to score jobs the daily cap parked never-scored
    # (finding: an over-cap backlog otherwise never gets an automatic second look). It
    # returns only OPEN + pending URLs, oldest first (fairness), capped by ``limit``.
    for url in ("a", "b", "c"):
        store.upsert_position("acme", url, fields=FIELDS, content_hash=url)
    store.set_match_pending("acme", "a")
    store.set_match_pending("acme", "b")
    # 'c' never parked; 'a','b' are the backlog. Oldest-first: 'a' before 'b'.
    assert store.pending_urls("acme", limit=10) == ["a", "b"]
    assert store.pending_urls("acme", limit=1) == ["a"]  # bounded


def test_pending_urls_excludes_scored_and_closed(store: StateStore) -> None:
    store.upsert_position("acme", "scored", fields=FIELDS, content_hash="s")
    store.upsert_position("acme", "closed_pending", fields=FIELDS, content_hash="c")
    store.upsert_position("acme", "live_pending", fields=FIELDS, content_hash="l")
    store.set_match("acme", "scored", score=80, verdict="good", reasons="")  # drained
    store.set_match_pending("acme", "closed_pending")
    store.mark_closed("acme", "closed_pending", reason="filled")  # gone
    store.set_match_pending("acme", "live_pending")
    assert store.pending_urls("acme", limit=10) == ["live_pending"]


def test_pending_urls_is_scoped_per_site(store: StateStore) -> None:
    store.upsert_position("acme", "u", fields=FIELDS, content_hash="h")
    store.upsert_position("other", "u", fields=FIELDS, content_hash="h")
    store.set_match_pending("acme", "u")
    store.set_match_pending("other", "u")
    assert store.pending_urls("acme", limit=10) == ["u"]
    assert store.pending_urls("beta", limit=10) == []  # unrelated site


def test_position_counts_summarizes_the_lifecycle(store: StateStore) -> None:
    store.upsert_position("acme", "a", fields=FIELDS, content_hash="a")
    store.upsert_position("acme", "b", fields=FIELDS, content_hash="b")
    store.upsert_position("acme", "c", fields=FIELDS, content_hash="c")
    store.set_match(
        "acme", "a", score=90, verdict="strong", reasons=""
    )  # open + matched
    store.set_match_pending("acme", "b")  # open + pending
    store.mark_closed("acme", "c", reason="x")  # closed
    assert store.position_counts() == {
        "open": 2,
        "closed": 1,
        "matched": 1,
        "pending": 1,
    }


# -- match cache: idempotent LLM scoring (§6/§8) -----------------------------


def test_cached_match_is_none_until_cached(store: StateStore) -> None:
    assert store.cached_match("ch", "ph", "model-x", "match-v1") is None


def test_cache_match_roundtrips_the_verdict_json(store: StateStore) -> None:
    store.cache_match("ch", "ph", "model-x", "match-v1", '{"score": 90}')
    assert store.cached_match("ch", "ph", "model-x", "match-v1") == '{"score": 90}'


def test_cache_match_is_keyed_by_the_full_tuple(store: StateStore) -> None:
    store.cache_match("ch", "ph", "model-x", "match-v1", "verdict")
    # every dimension participates in the key — a change on any one misses
    assert store.cached_match("OTHER", "ph", "model-x", "match-v1") is None
    assert store.cached_match("ch", "OTHER", "model-x", "match-v1") is None
    assert store.cached_match("ch", "ph", "OTHER", "match-v1") is None
    assert store.cached_match("ch", "ph", "model-x", "match-v2") is None


def test_cache_match_upserts_on_conflict(store: StateStore) -> None:
    store.cache_match("ch", "ph", "m", "v1", "first")
    store.cache_match("ch", "ph", "m", "v1", "second")
    assert store.cached_match("ch", "ph", "m", "v1") == "second"


# -- match spend: the daily token budget (§6) --------------------------------


def test_match_spent_is_zero_until_bumped(store: StateStore) -> None:
    assert store.match_spent("2026-07-02") == 0


def test_bump_match_spend_accumulates_within_a_day(store: StateStore) -> None:
    store.bump_match_spend("2026-07-02", 3)
    store.bump_match_spend("2026-07-02", 2)
    assert store.match_spent("2026-07-02") == 5


def test_match_spend_is_per_day(store: StateStore) -> None:
    store.bump_match_spend("2026-07-02", 4)
    assert store.match_spent("2026-07-02") == 4
    assert store.match_spent("2026-07-03") == 0  # a new UTC day starts fresh


def test_reserve_match_spend_grants_only_the_remaining_budget(
    store: StateStore,
) -> None:
    # Atomic claim-then-charge: each call grants min(n, cap - already_spent) and charges it
    # immediately, so a shared matcher's back-to-back reservations can't jointly overshoot.
    assert store.reserve_match_spend("2026-07-02", 3, cap=5) == 3  # 3 of 5
    assert store.match_spent("2026-07-02") == 3  # charged up front, not on completion
    assert store.reserve_match_spend("2026-07-02", 4, cap=5) == 2  # only 2 remain
    assert store.match_spent("2026-07-02") == 5
    assert store.reserve_match_spend("2026-07-02", 1, cap=5) == 0  # exhausted
    assert store.match_spent("2026-07-02") == 5  # a zero grant writes nothing
    assert store.reserve_match_spend("2026-07-03", 9, cap=5) == 5  # a new day is fresh


def test_reserve_match_spend_holds_reserved_headroom_back(store: StateStore) -> None:
    # #46 drain floor: ``reserved`` keeps that many of the cap untouched for another caller.
    # Fresh scoring passes the floor so it can't claim the last ``reserved`` units; the drain
    # passes 0 and so may spend them (plus whatever fresh left).
    day = "2026-07-02"
    assert (
        store.reserve_match_spend(day, 10, cap=10, reserved=4) == 6
    )  # capped at cap-floor
    assert store.match_spent(day) == 6
    # More fresh scoring still can't cross into the reserved 4.
    assert store.reserve_match_spend(day, 10, cap=10, reserved=4) == 0
    # The drain reserves nothing and claims the held-back remainder.
    assert store.reserve_match_spend(day, 10, cap=10, reserved=0) == 4
    assert store.match_spent(day) == 10  # exactly the cap, never beyond


def test_reserve_match_spend_never_overshoots_under_concurrency() -> None:
    # The finding-5 fix hinges on this: N threads (concurrent crawls sharing ONE matcher via
    # asyncio.to_thread) racing to reserve from a single cap must grant EXACTLY the cap in
    # total — never more. The old read-then-late-bump path could interleave and overspend.
    s = StateStore(":memory:")
    cap, racers = 50, 200
    grants: list[int] = []
    guard = threading.Lock()

    def worker() -> None:
        g = s.reserve_match_spend("2026-07-02", 1, cap=cap)
        with guard:
            grants.append(g)

    threads = [threading.Thread(target=worker) for _ in range(racers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(grants) == cap  # exactly the cap handed out across all racers
    assert grants.count(1) == cap and grants.count(0) == racers - cap
    assert s.match_spent("2026-07-02") == cap  # the ledger agrees with the grants
    s.close()


# -- persistence across reopen (§4.7 durability) -----------------------------


def test_positions_persist_across_reopen(tmp_path: object) -> None:
    db = f"{tmp_path}/state.db"  # type: ignore[str-bytes-safe]
    s1 = StateStore(db)
    s1.upsert_position(
        "acme", "u", fields={**FIELDS, "discipline": "backend"}, content_hash="h"
    )
    s1.set_match("acme", "u", score=91, verdict="strong", reasons="great")
    s1.close()

    s2 = StateStore(db)
    recs = s2.open_positions("acme")
    assert len(recs) == 1
    assert recs[0].discipline == "backend" and recs[0].match_score == 91
    s2.close()


def test_match_cache_persists_across_reopen(tmp_path: object) -> None:
    db = f"{tmp_path}/state.db"  # type: ignore[str-bytes-safe]
    s1 = StateStore(db)
    s1.cache_match("ch", "ph", "m", "match-v1", '{"score": 88}')
    s1.bump_match_spend("2026-07-02", 7)
    s1.close()

    s2 = StateStore(db)
    assert s2.cached_match("ch", "ph", "m", "match-v1") == '{"score": 88}'
    assert s2.match_spent("2026-07-02") == 7
    s2.close()


# -- schema migration & idempotence (§4.7) -----------------------------------


def test_opening_a_pre_positions_db_creates_the_new_tables(tmp_path: object) -> None:
    # A news-era DB predates positions/match_cache/match_spend entirely: opening it must
    # create them (CREATE IF NOT EXISTS) and leave a working tracker.
    db = f"{tmp_path}/legacy.db"  # type: ignore[str-bytes-safe]
    raw = sqlite3.connect(db)
    raw.executescript(
        """
        CREATE TABLE items (
            site TEXT NOT NULL, url TEXT NOT NULL, state TEXT NOT NULL,
            first_seen TEXT NOT NULL, ingested_at TEXT, PRIMARY KEY (site, url)
        );
        """
    )
    raw.commit()
    raw.close()

    s = StateStore(db)
    assert s.upsert_position("acme", "u", fields=FIELDS, content_hash="h") == "new"
    assert s.position_counts()["open"] == 1
    assert s.match_spent("2026-07-02") == 0
    s.close()


def test_migration_backfills_lifecycle_and_match_columns(tmp_path: object) -> None:
    # A positions table created before the lifecycle/match columns (a phase-1 tracker):
    # opening it must ALTER-in the missing columns and leave the legacy row usable.
    db = f"{tmp_path}/legacy.db"  # type: ignore[str-bytes-safe]
    raw = sqlite3.connect(db)
    raw.executescript(
        """
        CREATE TABLE positions (
            site TEXT NOT NULL, url TEXT NOT NULL, content_hash TEXT NOT NULL,
            title TEXT NOT NULL, company TEXT NOT NULL, location TEXT NOT NULL,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            PRIMARY KEY (site, url)
        );
        INSERT INTO positions (site, url, content_hash, title, company, location, first_seen, last_seen)
        VALUES ('acme', 'u', 'h', 'T', 'C', 'L', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');
        """
    )
    raw.commit()
    raw.close()

    s = StateStore(db)
    cols = {row["name"] for row in s._conn.execute("PRAGMA table_info(positions)")}
    assert {
        "external_id",
        "location_normalized",
        "discipline",
        "level",
        "workplace",
        "relocation",
        "salary_min",
        "salary_max",
        "salary_currency",
        "salary_period",
        "salary_usd_min",
        "salary_usd_max",
        "employment_type",
        "department",
        "posted_at",
        "expires_at",
        "missing_streak",
        "status",
        "closed_at",
        "match_score",
        "match_verdict",
        "match_reasons",
        "matched_at",
        "match_pending",
    } <= cols
    # the legacy row reads back as an open, unmatched position and matching works on it
    rec = s.open_positions("acme")[0]
    assert (
        rec.status == "open" and rec.match_score is None and rec.match_pending is False
    )
    s.set_match("acme", "u", score=90, verdict="strong", reasons="")
    # matches() surfaces the migrated row (min_score 0, since the legacy first_seen is months
    # old and the §6 freshness decay pulls its effective score below a 70 gate — decay tested
    # separately; here we only prove the migrated row is matchable).
    assert [r.url for r in s.matches(0)] == ["u"]
    s.close()


def test_migration_backfills_salary_usd(tmp_path: object) -> None:
    # A row with a source salary but no annualized-USD band (predating the salary_usd
    # columns): opening the DB derives it so the salary sort/filter works immediately,
    # without waiting for the board's next crawl.
    db = f"{tmp_path}/legacy.db"  # type: ignore[str-bytes-safe]
    raw = sqlite3.connect(db)
    raw.executescript(
        """
        CREATE TABLE positions (
            site TEXT NOT NULL, url TEXT NOT NULL, content_hash TEXT NOT NULL,
            title TEXT NOT NULL, company TEXT NOT NULL, location TEXT NOT NULL,
            salary_min REAL, salary_max REAL, salary_currency TEXT, salary_period TEXT,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            PRIMARY KEY (site, url)
        );
        INSERT INTO positions (site, url, content_hash, title, company, location,
            salary_min, salary_max, salary_currency, salary_period, first_seen, last_seen)
        VALUES ('acme', 'u', 'h', 'T', 'C', 'L', 10000.0, 20000.0, 'BRL', 'month',
            '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');
        """
    )
    raw.commit()
    raw.close()

    s = StateStore(db)
    try:
        rec = s.open_positions("acme")[0]
        # 10k–20k BRL/mo → ×12 months × 0.18 USD/BRL = 21.6k–43.2k USD/yr
        assert rec.salary_usd_min == pytest.approx(21_600.0)
        assert rec.salary_usd_max == pytest.approx(43_200.0)
    finally:
        s.close()


def test_migration_backfills_relocation(tmp_path: object) -> None:
    # A row with a stored description but no relocation signal (predating the column):
    # opening the DB derives it from the body so the "offers relocation" filter works
    # immediately, without waiting for the board's next crawl.
    db = f"{tmp_path}/legacy.db"  # type: ignore[str-bytes-safe]
    raw = sqlite3.connect(db)
    raw.executescript(
        """
        CREATE TABLE positions (
            site TEXT NOT NULL, url TEXT NOT NULL, content_hash TEXT NOT NULL,
            title TEXT NOT NULL, company TEXT NOT NULL, location TEXT NOT NULL,
            description TEXT, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            PRIMARY KEY (site, url)
        );
        INSERT INTO positions (site, url, content_hash, title, company, location,
            description, first_seen, last_seen)
        VALUES
            ('acme', 'yes', 'h1', 'T', 'C', 'L', 'We offer relocation assistance.',
                '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'),
            ('acme', 'no', 'h2', 'T', 'C', 'L', 'Build distributed systems in Rust.',
                '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');
        """
    )
    raw.commit()
    raw.close()

    s = StateStore(db)
    try:
        by_url = {r.url: r for r in s.open_positions("acme")}
        assert by_url["yes"].relocation == "offered"
        assert by_url["no"].relocation == "unspecified"
        assert [r.url for r in s.search_positions(relocation_offered=True)] == ["yes"]
    finally:
        s.close()


def test_migration_adds_structured_location_surfaces(tmp_path: object) -> None:
    # §4.12: opening a legacy DB must ALTER-in the three structured-location columns,
    # create the ledger/projection/region tables, and seed geo_region_members from the
    # in-code dict (meta-versioned so a GEO_REGION_SEED_VERSION bump reseeds).
    db = f"{tmp_path}/legacy.db"  # type: ignore[str-bytes-safe]
    raw = sqlite3.connect(db)
    raw.executescript(
        """
        CREATE TABLE positions (
            site TEXT NOT NULL, url TEXT NOT NULL, content_hash TEXT NOT NULL,
            title TEXT NOT NULL, company TEXT NOT NULL, location TEXT NOT NULL,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            PRIMARY KEY (site, url)
        );
        INSERT INTO positions (site, url, content_hash, title, company, location, first_seen, last_seen)
        VALUES ('acme', 'u', 'h', 'T', 'C', 'Berlin, Germany',
            '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');
        """
    )
    raw.commit()
    raw.close()

    s = StateStore(db)
    try:
        cols = {row["name"] for row in s._conn.execute("PRAGMA table_info(positions)")}
        assert {"locations_json", "location_display", "loc_llm_at"} <= cols
        tables = {
            row["name"]
            for row in s._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {"location_values", "position_locations", "geo_region_members"} <= tables
        assert s._conn.execute(
            "SELECT 1 FROM geo_region_members WHERE region = 'eu' AND country = 'DE'"
        ).fetchone()
        assert s.get_meta("geo_region_seed_version") is not None
        # Reseed on version change: a poisoned table heals at the next open.
        s._conn.execute(
            "DELETE FROM geo_region_members WHERE region = 'eu' AND country = 'DE'"
        )
        s._conn.execute(
            "INSERT INTO geo_region_members (region, country) VALUES ('eu', 'ZZ')"
        )
        s.set_meta("geo_region_seed_version", "0")
        s._conn.commit()
    finally:
        s.close()

    s2 = StateStore(db)
    try:
        assert s2._conn.execute(
            "SELECT 1 FROM geo_region_members WHERE region = 'eu' AND country = 'DE'"
        ).fetchone()
        assert not s2._conn.execute(
            "SELECT 1 FROM geo_region_members WHERE country = 'ZZ'"
        ).fetchone()
    finally:
        s2.close()


def test_upsert_accepts_locations_json_and_the_record_reads_it_back(
    store: StateStore,
) -> None:
    # locations_json is crawler-owned (in _POSITION_FIELDS); location_display and
    # loc_llm_at are store-owned and must NOT be settable through a crawl upsert.
    store.upsert_position(
        "acme",
        "u",
        fields={
            **FIELDS,
            "locations_json": '["Berlin", "London"]',
            "location_display": "sneaky",
            "loc_llm_at": "sneaky",
        },
        content_hash="h",
    )
    rec = store.open_positions("acme")[0]
    assert rec.locations_json == '["Berlin", "London"]'
    assert rec.location_display is None
    assert rec.loc_llm_at is None


# --- §4.12 structured locations: ledger + projection + backfill ----------------


def _projection(store: StateStore, site: str, url: str) -> list[tuple]:
    return [
        (
            r["seq"],
            r["kind"],
            r["city"],
            r["subdivision"],
            r["country"],
            r["macro_region"],
            r["display"],
            r["source"],
        )
        for r in store._conn.execute(
            "SELECT * FROM position_locations WHERE site = ? AND url = ? ORDER BY seq",
            (site, url),
        )
    ]


def _display(store: StateStore, site: str, url: str) -> str | None:
    row = store._conn.execute(
        "SELECT location_display FROM positions WHERE site = ? AND url = ?", (site, url)
    ).fetchone()
    return row["location_display"] if row else None


def test_refresh_projects_a_single_place_location(store: StateStore) -> None:
    store.upsert_position(
        "acme", "u", fields={**FIELDS, "location": "Berlin, Germany"}, content_hash="h"
    )
    store.refresh_position_locations(
        "acme", "u", location="Berlin, Germany", workplace="hybrid", force=True
    )
    rows = _projection(store, "acme", "u")
    assert len(rows) == 1
    seq, kind, city, sub, country, macro, display, source = rows[0]
    assert (kind, city, country, source) == ("place", "Berlin", "DE", "rules")
    assert display == "Berlin, Germany"
    assert _display(store, "acme", "u") == "Berlin, Germany"
    # The parse landed in the (site, value) ledger as a current-version rules row.
    ledger = store._conn.execute(
        "SELECT * FROM location_values WHERE site = 'acme' AND value = 'Berlin, Germany'"
    ).fetchone()
    assert ledger["parse_source"] == "rules"
    assert ledger["parse_quality"] == "full"


def test_refresh_merges_the_locations_json_array(store: StateStore) -> None:
    store.upsert_position(
        "acme", "u", fields={**FIELDS, "location": "Berlin, Germany"}, content_hash="h"
    )
    store.refresh_position_locations(
        "acme",
        "u",
        location="Berlin, Germany",
        locations_json='["Berlin, Germany", "Remote - US"]',
        workplace="hybrid",
        force=True,
    )
    rows = _projection(store, "acme", "u")
    assert [(r[1], r[4]) for r in rows] == [("place", "DE"), ("remote_fence", "US")]
    assert _display(store, "acme", "u") == "Berlin, Germany · Remote (United States)"


def test_refresh_promotes_countries_to_fence_only_for_remote_rows(
    store: StateStore,
) -> None:
    # working_nomads style: location='Spain' on a remote row means "remote, fenced to
    # Spain" (exactly classify_remote_scope's reading); on a hybrid row it is an office.
    for url, workplace, kind, display in [
        ("r", "remote", "remote_fence", "Remote (Spain)"),
        ("h", "hybrid", "place", "Spain"),
    ]:
        store.upsert_position(
            "acme", url, fields={**FIELDS, "location": "Spain"}, content_hash=url
        )
        store.refresh_position_locations(
            "acme", url, location="Spain", workplace=workplace, force=True
        )
        rows = _projection(store, "acme", url)
        assert [(r[1], r[4]) for r in rows] == [(kind, "ES")], url
        assert _display(store, "acme", url) == display, url


def test_refresh_title_fence_demotes_a_false_worldwide(store: StateStore) -> None:
    # The board's region string parses worldwide, but the title says
    # '[EUROPE ONLY]' — the projection becomes the fence, not remote_anywhere,
    # so remote_in= filters and the display stop lying.
    store.upsert_position(
        "wwr",
        "u",
        fields={**FIELDS, "location": "Anywhere in the World"},
        content_hash="h",
    )
    store.refresh_position_locations(
        "wwr",
        "u",
        location="Anywhere in the World",
        workplace="remote",
        title="[EUROPE ONLY] GTM MarTech Engineer",
        force=True,
    )
    rows = _projection(store, "wwr", "u")
    assert [(r[1], r[5]) for r in rows] == [("remote_fence", "europe")]
    assert _display(store, "wwr", "u") == "Remote (Europe)"
    # Without a fenced title the worldwide claim stands (pinned elsewhere too).
    store.upsert_position(
        "wwr",
        "w",
        fields={**FIELDS, "location": "Anywhere in the World"},
        content_hash="w",
    )
    store.refresh_position_locations(
        "wwr",
        "w",
        location="Anywhere in the World",
        workplace="remote",
        title="GTM MarTech Engineer",
        force=True,
    )
    assert [(r[1],) for r in _projection(store, "wwr", "w")] == [("remote_anywhere",)]


def test_migration_repairs_title_fenced_anywhere_rows(tmp_path: object) -> None:
    # One-shot heal for rows classified 'anywhere' before the title fence existed.
    db = f"{tmp_path}/legacy.db"  # type: ignore[str-bytes-safe]
    raw = sqlite3.connect(db)
    raw.executescript(
        """
        CREATE TABLE positions (
            site TEXT NOT NULL, url TEXT NOT NULL, content_hash TEXT NOT NULL,
            title TEXT NOT NULL, company TEXT NOT NULL, location TEXT NOT NULL,
            workplace TEXT, remote_scope TEXT,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            PRIMARY KEY (site, url)
        );
        INSERT INTO positions VALUES
            ('wwr', 'fenced', 'h1', '[EUROPE ONLY] Platform Engineer', 'C',
             'Anywhere in the World', 'remote', 'anywhere',
             '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'),
            ('wwr', 'open', 'h2', 'Platform Engineer', 'C',
             'Anywhere in the World', 'remote', 'anywhere',
             '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');
        """
    )
    raw.commit()
    raw.close()
    s = StateStore(db)
    try:
        by_url = {r.url: r for r in s.open_positions("wwr")}
        assert by_url["fenced"].remote_scope == "restricted"
        assert by_url["open"].remote_scope == "anywhere"
    finally:
        s.close()


def test_refresh_cross_string_fence_demotes_worldwide(store: StateStore) -> None:
    # audit #143 / sailonchain: one location string fences the role
    # ('Remote - US'), another carries a bare global token ('Worldwide').
    # The explicit fence is the specific signal — the projection keeps the
    # fence and drops the contradicting remote_anywhere row.
    store.upsert_position(
        "sailonchain",
        "u",
        fields={**FIELDS, "location": "Remote - US"},
        content_hash="h",
    )
    store.refresh_position_locations(
        "sailonchain",
        "u",
        location="Remote - US",
        locations_json='["Remote - US", "Worldwide"]',
        workplace="remote",
        force=True,
    )
    rows = _projection(store, "sailonchain", "u")
    assert [(r[1], r[4]) for r in rows] == [("remote_fence", "US")]
    assert _display(store, "sailonchain", "u") == "Remote (United States)"


def test_refresh_single_string_global_plus_country_fences_not_worldwide(
    store: StateStore,
) -> None:
    # web3career stamps its aggregator address onto a remote role:
    # 'Anywhere, United States'. The country wins (geo v4 suppresses the world
    # token), and the remote row promotes it to a US fence — never worldwide.
    store.upsert_position(
        "web3career",
        "u",
        fields={**FIELDS, "location": "Anywhere, United States"},
        content_hash="h",
    )
    store.refresh_position_locations(
        "web3career",
        "u",
        location="Anywhere, United States",
        workplace="remote",
        force=True,
    )
    rows = _projection(store, "web3career", "u")
    assert [(r[1], r[4]) for r in rows] == [("remote_fence", "US")]
    assert _display(store, "web3career", "u") == "Remote (United States)"


def test_refresh_worldwide_and_marker_only(store: StateStore) -> None:
    store.upsert_position(
        "acme",
        "w",
        fields={**FIELDS, "location": "Anywhere in the World"},
        content_hash="w",
    )
    store.refresh_position_locations(
        "acme", "w", location="Anywhere in the World", workplace="remote", force=True
    )
    assert [(r[1],) for r in _projection(store, "acme", "w")] == [("remote_anywhere",)]
    assert _display(store, "acme", "w") == "Remote (Worldwide)"

    # A bare marker parses clean but yields nothing structured: display stays NULL
    # so every renderer falls back to the raw string.
    store.upsert_position(
        "acme", "m", fields={**FIELDS, "location": "Remote"}, content_hash="m"
    )
    store.refresh_position_locations(
        "acme", "m", location="Remote", workplace="remote", force=True
    )
    assert _projection(store, "acme", "m") == []
    assert _display(store, "acme", "m") is None


def test_refresh_skip_path_trusts_a_current_ledger_row(store: StateStore) -> None:
    store.upsert_position(
        "acme", "u", fields={**FIELDS, "location": "Berlin, Germany"}, content_hash="h"
    )
    store.refresh_position_locations(
        "acme", "u", location="Berlin, Germany", workplace="hybrid", force=True
    )
    # Poison the projection, then refresh without force (the 'unchanged' crawl path):
    # the current-version ledger row proves this position was already projected, so
    # nothing is rewritten.
    store._conn.execute(
        "UPDATE position_locations SET display = 'poisoned' WHERE site = 'acme'"
    )
    store._conn.commit()
    store.refresh_position_locations(
        "acme", "u", location="Berlin, Germany", workplace="hybrid", force=False
    )
    assert _projection(store, "acme", "u")[0][6] == "poisoned"
    # force=True rebuilds.
    store.refresh_position_locations(
        "acme", "u", location="Berlin, Germany", workplace="hybrid", force=True
    )
    assert _projection(store, "acme", "u")[0][6] == "Berlin, Germany"


def test_refresh_reparses_a_stale_rules_ledger_row(store: StateStore) -> None:
    now = "2026-07-09T00:00:00+00:00"
    store._conn.execute(
        "INSERT INTO location_values (site, value, places_json, fence_json, parse_quality, "
        "parse_source, parser_version, parsed_at) "
        "VALUES ('acme', 'Berlin, Germany', '[]', '[]', 'fail', 'rules', 0, ?)",
        (now,),
    )
    store._conn.commit()
    store.upsert_position(
        "acme", "u", fields={**FIELDS, "location": "Berlin, Germany"}, content_hash="h"
    )
    store.refresh_position_locations(
        "acme", "u", location="Berlin, Germany", workplace="hybrid", force=False
    )
    ledger = store._conn.execute(
        "SELECT * FROM location_values WHERE site = 'acme' AND value = 'Berlin, Germany'"
    ).fetchone()
    assert ledger["parse_quality"] == "full"
    assert ledger["parser_version"] > 0
    assert _projection(store, "acme", "u") != []


def test_llm_ledger_fill_and_precedence(store: StateStore, monkeypatch) -> None:
    from jobcrawler.runtime import geo

    store.upsert_position(
        "acme", "u", fields={**FIELDS, "location": "Zorgville"}, content_hash="h"
    )
    store.refresh_position_locations(
        "acme", "u", location="Zorgville", workplace="hybrid", force=True
    )
    assert store.location_needs_llm("acme", "Zorgville") is True
    assert _projection(store, "acme", "u") == []

    filled = store.set_location_value_llm(
        "acme",
        "Zorgville",
        places=[{"city": "Zorgville", "subdivision": None, "country": "US"}],
        fence=[],
        remote="none",
        model="m1",
    )
    assert filled is True
    assert store.location_needs_llm("acme", "Zorgville") is False
    rows = _projection(store, "acme", "u")
    assert [(r[1], r[2], r[4], r[7]) for r in rows] == [
        ("place", "Zorgville", "US", "llm_value")
    ]
    assert _display(store, "acme", "u") == "Zorgville, United States"

    # First writer wins: a second fill is a no-op.
    assert (
        store.set_location_value_llm(
            "acme",
            "Zorgville",
            places=[{"city": "Elsewhere", "subdivision": None, "country": "CA"}],
            fence=[],
            remote="none",
            model="m2",
        )
        is False
    )
    assert _display(store, "acme", "u") == "Zorgville, United States"

    # A parser upgrade that still can't parse the string leaves the LLM row standing…
    monkeypatch.setattr(geo, "GEO_PARSER_VERSION", geo.GEO_PARSER_VERSION + 1)
    store.refresh_position_locations(
        "acme", "u", location="Zorgville", workplace="hybrid", force=True
    )
    ledger = store._conn.execute(
        "SELECT * FROM location_values WHERE site = 'acme' AND value = 'Zorgville'"
    ).fetchone()
    assert ledger["parse_source"] == "llm"
    assert _display(store, "acme", "u") == "Zorgville, United States"


def test_clean_rules_parse_beats_a_stale_llm_row(store: StateStore) -> None:
    # Deterministic-first: if the (upgraded) parser resolves a string cleanly, the
    # cached LLM reading is retired.
    now = "2026-07-09T00:00:00+00:00"
    store._conn.execute(
        "INSERT INTO location_values (site, value, places_json, fence_json, parse_quality, "
        "parse_source, parser_version, llm_model, parsed_at) "
        "VALUES ('acme', 'Berlin, Germany', "
        '\'[{"city": "Wrongtown", "subdivision": null, "country": "US", '
        '"macro": null, "confidence": "medium", "raw": ""}]\', \'[]\', '
        "'full', 'llm', 0, 'm1', ?)",
        (now,),
    )
    store._conn.commit()
    store.upsert_position(
        "acme", "u", fields={**FIELDS, "location": "Berlin, Germany"}, content_hash="h"
    )
    store.refresh_position_locations(
        "acme", "u", location="Berlin, Germany", workplace="hybrid", force=True
    )
    ledger = store._conn.execute(
        "SELECT * FROM location_values WHERE site = 'acme' AND value = 'Berlin, Germany'"
    ).fetchone()
    assert ledger["parse_source"] == "rules"
    assert _display(store, "acme", "u") == "Berlin, Germany"


def test_posting_local_llm_rows_and_the_extract_stamp(store: StateStore) -> None:
    # The marker arm: a fence mined from THIS posting's description is posting-local —
    # it survives rules resweeps but dies with the posting's content.
    store.upsert_position(
        "acme", "u", fields={**FIELDS, "location": "Remote"}, content_hash="h"
    )
    store.refresh_position_locations(
        "acme", "u", location="Remote", workplace="remote", force=True
    )
    store.set_position_location_llm(
        "acme", "u", places=[], fence=["US", "CA"], remote="fenced", model="m1"
    )
    assert store.location_llm_stamped("acme", "u") is False
    store.mark_location_extracted("acme", "u")
    assert store.location_llm_stamped("acme", "u") is True
    rows = _projection(store, "acme", "u")
    assert [(r[1], r[4], r[7]) for r in rows] == [
        ("remote_fence", "US", "llm_posting"),
        ("remote_fence", "CA", "llm_posting"),
    ]
    assert _display(store, "acme", "u") == "Remote (United States or Canada)"
    rec = store.open_positions("acme")[0]
    assert rec.loc_llm_at is not None

    # A rules resweep (parser upgrade) keeps posting-local LLM rows…
    store.refresh_position_locations(
        "acme", "u", location="Remote", workplace="remote", force=True
    )
    assert [(r[1], r[7]) for r in _projection(store, "acme", "u")] == [
        ("remote_fence", "llm_posting"),
        ("remote_fence", "llm_posting"),
    ]
    # …but a content change purges them and clears the stamp (the extract cache
    # misses on the new hash, so the pass re-answers from the fresh description).
    store.refresh_position_locations(
        "acme", "u", location="Remote", workplace="remote", force=True, purge_llm=True
    )
    assert _projection(store, "acme", "u") == []
    assert store.open_positions("acme")[0].loc_llm_at is None


def test_backfill_locations_sweeps_a_legacy_db(tmp_path: object) -> None:
    # Opening a DB whose meta parser version is absent/stale parses every distinct
    # string and projects every row — including closed ones and the conflict-(d)
    # class: 'Remote - US' on a hybrid row gains an explicit fence at zero LLM cost.
    db = f"{tmp_path}/legacy.db"  # type: ignore[str-bytes-safe]
    raw = sqlite3.connect(db)
    raw.executescript(
        """
        CREATE TABLE positions (
            site TEXT NOT NULL, url TEXT NOT NULL, content_hash TEXT NOT NULL,
            title TEXT NOT NULL, company TEXT NOT NULL, location TEXT NOT NULL,
            workplace TEXT, status TEXT NOT NULL DEFAULT 'open', closed_at TEXT,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            PRIMARY KEY (site, url)
        );
        INSERT INTO positions (site, url, content_hash, title, company, location,
            workplace, status, first_seen, last_seen)
        VALUES
            ('acme', 'open', 'h1', 'T', 'C', 'Berlin, Germany', 'hybrid', 'open',
                '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'),
            ('acme', 'closed', 'h2', 'T', 'C', 'Lisbon, Portugal', 'onsite', 'closed',
                '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'),
            ('acme', 'fenced', 'h3', 'T', 'C', 'Remote - US', 'hybrid', 'open',
                '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');
        """
    )
    raw.commit()
    raw.close()

    s = StateStore(db)
    try:
        assert _display(s, "acme", "open") == "Berlin, Germany"
        assert _display(s, "acme", "closed") == "Lisbon, Portugal"
        assert [(r[1], r[4]) for r in _projection(s, "acme", "fenced")] == [
            ("remote_fence", "US")
        ]
        assert s.get_meta("geo_parser_version") is not None
        # Idempotent while the version holds: a poisoned display survives a reopen.
        s._conn.execute(
            "UPDATE positions SET location_display = 'poisoned' WHERE url = 'open'"
        )
        s._conn.commit()
    finally:
        s.close()

    s2 = StateStore(db)
    try:
        assert _display(s2, "acme", "open") == "poisoned"
        # …and a version bump resweeps it clean.
        s2.set_meta("geo_parser_version", "0")
        s2._conn.commit()
    finally:
        s2.close()

    s3 = StateStore(db)
    try:
        assert _display(s3, "acme", "open") == "Berlin, Germany"
    finally:
        s3.close()


def test_requeue_salaryless_matches_marks_only_the_extraction_pool(
    store: StateStore,
) -> None:
    # The staged extract-v3 trigger: open + scored at/above the gate + salary-less.
    # Closed, salaried, sub-threshold, unscored, and already-pending rows untouched.
    rows = [
        ("target", "open", 70, None),
        ("salaried", "open", 80, 90000.0),
        ("weak", "open", 40, None),
        ("unscored", "open", None, None),
        ("gone", "closed", 90, None),
    ]
    for url, status, score, salary in rows:
        fields = dict(FIELDS)
        if salary is not None:
            fields.update(
                salary_min=salary,
                salary_max=salary,
                salary_currency="USD",
                salary_period="year",
            )
        store.upsert_position("acme", url, fields=fields, content_hash=url)
        if score is not None:
            store.set_match("acme", url, score=score, verdict="x", reasons="")
    store._conn.execute("UPDATE positions SET status = 'closed' WHERE url = 'gone'")
    store._conn.commit()

    assert store.requeue_salaryless_matches(min_score=60) == 1
    pending = {
        r["url"]
        for r in store._conn.execute(
            "SELECT url FROM positions WHERE match_pending = 1"
        )
    }
    assert pending == {"target"}
    assert store.requeue_salaryless_matches(min_score=60) == 0  # idempotent


def test_set_salary_stamps_llm_source_and_defaults_period(store: StateStore) -> None:
    # LLM answers often omit the period; the §4.6 currency convention keeps the
    # usd band computable instead of minting permanently-NULL-usd rows, and the
    # provenance marker makes the fill distinguishable from source data.
    store.upsert_position("acme", "u", fields=FIELDS, content_hash="h")
    assert store.set_salary(
        "acme",
        "u",
        salary_min=90000.0,
        salary_max=110000.0,
        salary_currency="USD",
        salary_period=None,
    )
    rec = store.open_positions("acme")[0]
    assert rec.salary_period == "year"
    assert rec.salary_usd_min == 90000.0
    assert rec.salary_source == "llm"


def test_salary_repair_sweep_heals_the_measured_corruption_classes(
    tmp_path: object,
) -> None:
    # One sweep per SALARY_PARSER_VERSION at store open (§4.6): each class below is
    # a live-measured corruption shape. Flips only when the flipped reading is
    # plausible; otherwise clears to honest absence (the untouched NULL-guard then
    # lets the §4.7 extract arm re-fill). LLM-sourced rows are never touched.
    db = f"{tmp_path}/legacy.db"  # type: ignore[str-bytes-safe]
    raw = sqlite3.connect(db)
    raw.execute(
        """
        CREATE TABLE positions (
            site TEXT NOT NULL, url TEXT NOT NULL, content_hash TEXT NOT NULL,
            title TEXT NOT NULL, company TEXT NOT NULL, location TEXT NOT NULL,
            salary_min REAL, salary_max REAL, salary_currency TEXT,
            salary_period TEXT, salary_usd_min REAL, salary_usd_max REAL,
            salary_raw TEXT, salary_source TEXT,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            PRIMARY KEY (site, url)
        )
        """
    )
    ts = "2026-01-01T00:00:00+00:00"

    def put(url, lo, hi, cur, per, usd_lo, usd_hi, sraw=None, source=None):
        raw.execute(
            "INSERT INTO positions VALUES ('s', ?, 'h', 'T', 'C', 'L', ?, ?, ?, ?, "
            "?, ?, ?, ?, ?, ?)",
            (url, lo, hi, cur, per, usd_lo, usd_hi, sraw, source, ts, ts),
        )

    put("zero", 0.0, 0.0, "USD", "year", 0.0, 0.0)
    put("hourflip", 107000.0, 164550.0, "USD", "hour", 222560000.0, 342264000.0)
    put("monthflip", 430000.0, 430000.0, "USD", "month", 5160000.0, 5160000.0)
    put("monthclear", 875000000.0, 875000000.0, "USD", "month", 1.05e10, 1.05e10)
    put("halfk", 80.0, 110000.0, "EUR", "year", 86.4, 118800.0)
    put("tinyyear", 78.0, 87.0, "USD", "year", 78.0, 87.0)
    put("absurd", 220000000.0, 220000000.0, "USD", "year", 2.2e8, 2.2e8)
    put("llmrow", 78.0, 87.0, "USD", "year", 78.0, 87.0, source="llm")
    put(
        "rawgood",
        1.0,
        1.0,
        "USD",
        "year",
        1.0,
        1.0,
        sraw="CA$120,000 - CA$150,000",
        source="text",
    )
    put(
        "rawbad",
        500.0,
        500.0,
        "EUR",
        "year",
        540.0,
        540.0,
        sraw="€500 per day",
        source="text",
    )
    raw.commit()
    raw.close()

    s = StateStore(db)
    try:
        rows = {
            r["url"]: r
            for r in s._conn.execute(
                "SELECT url, salary_min, salary_max, salary_currency, salary_period, "
                "salary_usd_min, salary_usd_max, salary_source FROM positions"
            )
        }
        for cleared in ("zero", "monthclear", "tinyyear", "absurd", "rawbad"):
            assert rows[cleared]["salary_min"] is None, cleared
            assert rows[cleared]["salary_source"] is None, cleared
        assert rows["hourflip"]["salary_period"] == "year"
        assert rows["hourflip"]["salary_usd_min"] == 107000.0
        assert rows["monthflip"]["salary_period"] == "year"
        assert rows["monthflip"]["salary_usd_max"] == 430000.0
        assert rows["halfk"]["salary_min"] == 80000.0
        assert rows["halfk"]["salary_usd_min"] == pytest.approx(86400.0)
        assert rows["llmrow"]["salary_min"] == 78.0  # llm rows are never swept
        assert rows["rawgood"]["salary_currency"] == "CAD"
        assert (rows["rawgood"]["salary_min"], rows["rawgood"]["salary_max"]) == (
            120000.0,
            150000.0,
        )
        assert s.get_meta("salary_parser_version") is not None
        # Idempotent while the version holds…
        s._conn.execute(
            "UPDATE positions SET salary_min = 42, salary_max = 42, "
            "salary_currency = 'USD', salary_period = 'year', "
            "salary_usd_min = 42, salary_usd_max = 42 WHERE url = 'zero'"
        )
        s._conn.commit()
    finally:
        s.close()

    s2 = StateStore(db)
    try:
        row = s2._conn.execute(
            "SELECT salary_min FROM positions WHERE url = 'zero'"
        ).fetchone()
        assert row["salary_min"] == 42  # guard held: no resweep
        # …and a version bump resweeps ($42/yr is a tiny-year band → cleared).
        s2.set_meta("salary_parser_version", "0")
        s2._conn.commit()
    finally:
        s2.close()

    s3 = StateStore(db)
    try:
        row = s3._conn.execute(
            "SELECT salary_min FROM positions WHERE url = 'zero'"
        ).fetchone()
        assert row["salary_min"] is None
    finally:
        s3.close()


def test_migration_adds_salary_raw_and_source(tmp_path: object) -> None:
    # §4.6 salary provenance: opening a legacy DB ALTERs in the verbatim printed
    # salary text (crawler-owned) and the provenance marker ('structured'|'text'|
    # 'llm'; NULL = pre-provenance rows).
    db = f"{tmp_path}/legacy.db"  # type: ignore[str-bytes-safe]
    raw = sqlite3.connect(db)
    raw.executescript(
        """
        CREATE TABLE positions (
            site TEXT NOT NULL, url TEXT NOT NULL, content_hash TEXT NOT NULL,
            title TEXT NOT NULL, company TEXT NOT NULL, location TEXT NOT NULL,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            PRIMARY KEY (site, url)
        );
        """
    )
    raw.commit()
    raw.close()
    s = StateStore(db)
    try:
        cols = {row["name"] for row in s._conn.execute("PRAGMA table_info(positions)")}
        assert {"salary_raw", "salary_source"} <= cols
    finally:
        s.close()


def test_upsert_accepts_salary_raw_and_source_and_reads_them_back(
    store: StateStore,
) -> None:
    store.upsert_position(
        "acme",
        "u",
        fields={
            **FIELDS,
            "salary_raw": "$150K – $200K",
            "salary_source": "text",
        },
        content_hash="h",
    )
    rec = store.open_positions("acme")[0]
    assert rec.salary_raw == "$150K – $200K"
    assert rec.salary_source == "text"


def test_search_positions_country_and_remote_in_filters(store: StateStore) -> None:
    # §4.12 structured filters: country= matches any resolved place or fence;
    # remote_in= is kind-scoped (an office place never satisfies it), expands
    # macros through geo_region_members, and worldwide matches every country.
    rows = [
        ("berlin", "Berlin, Germany", "hybrid"),
        ("fenced-us", "Remote - US", "remote"),
        ("emea", "Home based - EMEA", "remote"),
        ("anywhere", "Anywhere in the World", "remote"),
        ("mystery", "Zorgville", "remote"),
    ]
    for url, loc, wp in rows:
        store.upsert_position(
            "acme",
            url,
            fields={**FIELDS, "location": loc, "workplace": wp},
            content_hash=url,
        )
        store.refresh_position_locations(
            "acme", url, location=loc, workplace=wp, force=True
        )

    def urls(*, country: str | None = None, remote_in: str | None = None) -> list[str]:
        return sorted(
            r.url for r in store.search_positions(country=country, remote_in=remote_in)
        )

    assert urls(country="DE") == ["berlin"]
    assert urls(country="de") == ["berlin"]  # case-folded
    assert urls(country="US") == ["fenced-us"]
    assert urls(remote_in="DE") == [
        "anywhere",
        "emea",
    ]  # EMEA expands; office DE doesn't
    assert urls(remote_in="US") == ["anywhere", "fenced-us"]
    assert urls(remote_in="BR") == ["anywhere"]
    assert urls(country="ES") == []


def test_search_exclude_country_drops_only_resolved_matches(
    store: StateStore,
) -> None:
    # The mirror of country=: drop rows with ANY resolved office or remote fence
    # in that country. Composable with relocation_offered — the "relocation but
    # not to the US" view: a US office or a US-only fence means the relocation
    # destination needs a US visa; unknown geography stays in (only confirmed-US
    # drops — an exclusion never fires on absence of evidence).
    rows = [
        ("berlin-relo", "Berlin, Germany", "hybrid", "offered"),
        ("sf-relo", "San Francisco, CA", "hybrid", "offered"),
        ("usfence-relo", "Remote - US", "hybrid", "offered"),
        ("mystery-relo", "Zorgville", "remote", "offered"),
        ("berlin-norelo", "Berlin, Germany", "hybrid", "unspecified"),
    ]
    for url, loc, wp, relo in rows:
        store.upsert_position(
            "acme",
            url,
            fields={**FIELDS, "location": loc, "workplace": wp, "relocation": relo},
            content_hash=url,
        )
        store.refresh_position_locations(
            "acme", url, location=loc, workplace=wp, force=True
        )

    hits = sorted(
        r.url
        for r in store.search_positions(relocation_offered=True, exclude_country="us")
    )
    assert hits == ["berlin-relo", "mystery-relo"]
    # Alone it is just the mirror filter.
    assert sorted(r.url for r in store.search_positions(exclude_country="US")) == [
        "berlin-norelo",
        "berlin-relo",
        "mystery-relo",
    ]


def test_search_mobility_arms_or_together(store: StateStore) -> None:
    # #132: the UI's three mobility checkboxes are alternative routes into a job,
    # so combined they OR (any satisfied arm keeps the row) — the old composition
    # ANDed them into "remote-anywhere AND relocation AND non-US", a near-empty
    # set. Each arm alone must stay exactly its standalone filter.
    rows = [
        # url, location, workplace, relocation, remote_scope
        ("anywhere", "Remote", "remote", "unspecified", "anywhere"),
        ("sf-relo", "San Francisco, CA", "hybrid", "offered", None),
        ("berlin-relo", "Berlin, Germany", "hybrid", "offered", None),
        ("neither", "Berlin, Germany", "onsite", "unspecified", None),
    ]
    for url, loc, wp, relo, scope in rows:
        store.upsert_position(
            "acme",
            url,
            fields={
                **FIELDS,
                "location": loc,
                "workplace": wp,
                "relocation": relo,
                "remote_scope": scope,
            },
            content_hash=url,
        )
        store.refresh_position_locations(
            "acme", url, location=loc, workplace=wp, force=True
        )

    def urls(*arms: str) -> list[str]:
        return sorted(r.url for r in store.search_positions(mobility=list(arms)))

    # Single arms == the standalone filters they mirror.
    assert urls("remote_anywhere") == ["anywhere"]
    assert urls("relocation_offered") == ["berlin-relo", "sf-relo"]
    assert urls("relocation_no_us") == ["berlin-relo"]  # resolved-US office drops
    # Combined arms widen (OR), never narrow to the impossible intersection.
    assert urls("remote_anywhere", "relocation_no_us") == ["anywhere", "berlin-relo"]
    assert urls("remote_anywhere", "relocation_offered", "relocation_no_us") == [
        "anywhere",
        "berlin-relo",
        "sf-relo",
    ]
    with pytest.raises(ValueError, match="jetpack"):
        store.search_positions(mobility=["jetpack"])


def test_search_mobility_remote_anywhere_is_home_relative(
    store: StateStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    # #144: "Remote anywhere" is home-relative — a role remotely workable *from
    # home* is as reachable as a worldwide one. The arm keeps a worldwide anywhere
    # plus any remote fence that covers config.home_country() directly (a BR fence)
    # or through a macro region (LATAM ⊇ BR). A fence for a *different* country
    # (US) does not, and a home-country *office* (a 'place', not a fence) does not —
    # being physically in Brazil is not the same as being remotely hireable from it.
    rows = [
        # url, location, workplace, remote_scope
        ("worldwide", "Anywhere in the World", "remote", "anywhere"),
        ("br-fence", "Remote - Brazil", "remote", "restricted"),
        ("latam-fence", "Remote - LATAM", "remote", "restricted"),
        ("us-fence", "Remote - US", "remote", "restricted"),
        ("br-office", "São Paulo, Brazil", "onsite", None),
    ]
    for url, loc, wp, scope in rows:
        store.upsert_position(
            "acme",
            url,
            fields={**FIELDS, "location": loc, "workplace": wp, "remote_scope": scope},
            content_hash=url,
        )
        store.refresh_position_locations(
            "acme", url, location=loc, workplace=wp, force=True
        )

    def anywhere() -> list[str]:
        return sorted(
            r.url for r in store.search_positions(mobility=["remote_anywhere"])
        )

    # Home = Brazil (the default): worldwide + BR fence + LATAM macro fence.
    monkeypatch.setenv("JOBCRAWLER_HOME_COUNTRY", "BR")
    assert anywhere() == ["br-fence", "latam-fence", "worldwide"]

    # The lever is real, not a hardcoded BR: from the US the US fence joins and the
    # Brazil-only ones drop, while a worldwide anywhere still matches from anywhere.
    monkeypatch.setenv("JOBCRAWLER_HOME_COUNTRY", "US")
    assert anywhere() == ["us-fence", "worldwide"]

    # A malformed override can never reach the inlined SQL literal — it falls back
    # to BR rather than injecting or emptying the result.
    monkeypatch.setenv("JOBCRAWLER_HOME_COUNTRY", "not-a-country")
    assert anywhere() == ["br-fence", "latam-fence", "worldwide"]


def test_search_exclude_email_apply(store: StateStore) -> None:
    # #133: "Hide e-mail apply" drops rows whose apply target is a mailto: link.
    # No apply_url at all is NOT e-mail apply (the posting page itself is the
    # application route), so those rows stay.
    for url, apply_url in [
        ("form", "https://jobs.acme.test/apply/1"),
        ("email", "MAILTO:jobs@acme.test?subject=Engineer"),  # case-folded match
        ("bare", None),
    ]:
        fields: dict[str, object] = dict(FIELDS)
        if apply_url is not None:
            fields["apply_url"] = apply_url
        store.upsert_position("acme", url, fields=fields, content_hash=url)

    hits = sorted(r.url for r in store.search_positions(exclude_email_apply=True))
    assert hits == ["bare", "form"]
    assert len(store.search_positions()) == 3  # off by default


def test_search_query_bridges_synonyms_through_location_display(
    store: StateStore,
) -> None:
    # Raw says 'Brasil'; the projection's display carries the English country name,
    # so q='brazil' now finds the row even with location_normalized unset.
    store.upsert_position(
        "acme",
        "sp",
        fields={**FIELDS, "location": "São Paulo, Brasil"},
        content_hash="h",
    )
    store.refresh_position_locations(
        "acme", "sp", location="São Paulo, Brasil", workplace="hybrid", force=True
    )
    assert [r.url for r in store.search_positions("brazil")] == ["sp"]


def test_search_positions_salary_sort_and_filter_are_usd_coherent(
    store: StateStore,
) -> None:
    # A BRL-monthly and a USD-annual posting rank by their annualized-USD band, not the
    # raw source number (25000 BRL/mo reads larger than 120000 USD/yr, but is far less in USD).
    store.upsert_position(
        "acme",
        "brl",
        fields={
            **FIELDS,
            "title": "BRL role",
            "salary_min": 20000.0,
            "salary_max": 25000.0,
            "salary_currency": "BRL",
            "salary_period": "month",
            "salary_usd_min": 43200.0,
            "salary_usd_max": 54000.0,
        },
        content_hash="h-brl",
    )
    store.upsert_position(
        "acme",
        "usd",
        fields={
            **FIELDS,
            "title": "USD role",
            "salary_min": 120000.0,
            "salary_max": 140000.0,
            "salary_currency": "USD",
            "salary_period": "year",
            "salary_usd_min": 120000.0,
            "salary_usd_max": 140000.0,
        },
        content_hash="h-usd",
    )

    # highest annualized-USD first, despite the BRL row's larger raw number
    assert [r.url for r in store.search_positions(sort="salary")] == ["usd", "brl"]
    # the floor is USD/yr: $60k drops the ~$54k BRL role, keeps the $140k USD role
    assert [r.url for r in store.search_positions(min_salary=60000.0)] == ["usd"]


def test_positions_migration_is_idempotent(tmp_path: object) -> None:
    db = f"{tmp_path}/state.db"  # type: ignore[str-bytes-safe]
    StateStore(db).close()
    # Re-opening re-runs _SCHEMA + _migrate; columns must not be double-added.
    s = StateStore(db)
    cols = [row["name"] for row in s._conn.execute("PRAGMA table_info(positions)")]
    assert cols.count("status") == 1
    assert cols.count("match_pending") == 1
    assert cols.count("external_id") == 1
    s.close()


def test_reopening_an_old_schema_db_twice_is_stable(tmp_path: object) -> None:
    # Open a legacy positions DB (missing the match columns) twice in a row: the second
    # open re-runs the guarded migration and must be a no-op — no error, no lost data.
    db = f"{tmp_path}/legacy.db"  # type: ignore[str-bytes-safe]
    raw = sqlite3.connect(db)
    raw.executescript(
        """
        CREATE TABLE positions (
            site TEXT NOT NULL, url TEXT NOT NULL, content_hash TEXT NOT NULL,
            title TEXT NOT NULL, company TEXT NOT NULL, location TEXT NOT NULL,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            missing_streak INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'open',
            closed_at TEXT, PRIMARY KEY (site, url)
        );
        INSERT INTO positions (site, url, content_hash, title, company, location, first_seen, last_seen)
        VALUES ('acme', 'u', 'h', 'T', 'C', 'L', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');
        """
    )
    raw.commit()
    raw.close()

    StateStore(db).close()  # first open: backfills match_* columns
    s = StateStore(db)  # second open: guarded migration no-ops
    cols = [row["name"] for row in s._conn.execute("PRAGMA table_info(positions)")]
    assert cols.count("match_pending") == 1
    assert [r.url for r in s.open_positions("acme")] == ["u"]  # legacy row intact
    s.close()


# --- description persistence + meta + requeue (§4.7 store-side rematch) ------


def test_positions_persist_the_description_verbatim() -> None:
    # The row is a COMPLETE record of the posting: the §4.7 matcher rebuilds its LLM
    # digest from the store alone, so the description must round-trip untouched.
    s = StateStore(":memory:")
    text = "We build distributed payment systems in Rust.\n\n  Benefits: remote."
    s.upsert_position(
        "acme",
        "https://acme.example/jobs/1",
        fields={"title": "T", "company": "C", "location": "L", "description": text},
        content_hash="h1",
    )
    (rec,) = s.open_positions("acme")
    assert rec.description == text
    s.close()


def test_a_reseen_row_backfills_its_description() -> None:
    # Rows written before the description column existed heal on the next crawl: the
    # upsert refreshes crawler-supplied fields even when the content hash is unchanged.
    s = StateStore(":memory:")
    s.upsert_position(
        "acme",
        "u",
        fields={"title": "T", "company": "C", "location": "L"},
        content_hash="h",
    )
    assert s.open_positions("acme")[0].description is None
    outcome = s.upsert_position(
        "acme",
        "u",
        fields={"title": "T", "company": "C", "location": "L", "description": "Full."},
        content_hash="h",
    )
    assert outcome == "unchanged"  # same hash — yet the field refreshed
    assert s.open_positions("acme")[0].description == "Full."
    s.close()


def test_meta_roundtrip_and_overwrite() -> None:
    s = StateStore(":memory:")
    assert s.get_meta("profile_hash") is None
    s.set_meta("profile_hash", "aaa")
    assert s.get_meta("profile_hash") == "aaa"
    s.set_meta("profile_hash", "bbb")  # upsert, not insert-or-fail
    assert s.get_meta("profile_hash") == "bbb"
    s.close()


def test_requeue_open_matches_parks_all_open_rows_and_keeps_verdicts() -> None:
    # A profile edit invalidates every verdict lazily; the requeue parks the whole OPEN
    # backlog for rescoring (scored rows keep their old verdict until re-applied) while
    # closed history stays untouched.
    s = StateStore(":memory:")
    for url, score in (("u1", 85), ("u2", None)):
        s.upsert_position(
            "acme",
            url,
            fields={"title": "T", "company": "C", "location": "L"},
            content_hash=f"h-{url}",
        )
        if score is not None:
            s.set_match("acme", url, score=score, verdict="strong", reasons="[]")
    s.upsert_position(
        "acme",
        "u3",
        fields={"title": "T", "company": "C", "location": "L"},
        content_hash="h-u3",
    )
    s.mark_closed("acme", "u3", reason="test")

    assert s.requeue_open_matches() == 2
    pending = {r.url for r in s.pending_matches(limit=10)}
    assert pending == {"u1", "u2"}
    by_url = {r.url: r for r in s.open_positions("acme")}
    assert by_url["u1"].match_score == 85  # verdict survives the requeue
    s.close()


def test_clear_match_pending_unparks_without_a_verdict() -> None:
    # A drain-time definite prefilter miss leaves the FIFO but earns no verdict — the
    # row returns to plain "tracked, unscored" (the same state a fresh prefilter miss
    # gets from the runner).
    s = StateStore(":memory:")
    s.upsert_position(
        "acme",
        "u",
        fields={"title": "T", "company": "C", "location": "L"},
        content_hash="h",
    )
    s.set_match_pending("acme", "u")
    s.clear_match_pending("acme", "u")
    assert s.pending_matches(limit=10) == []
    assert s.open_positions("acme")[0].match_score is None
    s.close()


def test_clear_match_pending_wipes_a_stale_verdict() -> None:
    # A REQUEUED row lands on this path precisely because its old score predates the
    # prefilter rule that now rejects it (#93: Netflix/HubSpot US-locked rows scored
    # 76-88 under the pre-geo-rule prompt) — "tracked, unscored" must mean the stale
    # number stops being advertised, not just that the flag drops.
    s = StateStore(":memory:")
    s.upsert_position(
        "acme",
        "u",
        fields={"title": "T", "company": "C", "location": "Remote, US"},
        content_hash="h",
    )
    s.set_match("acme", "u", score=86, verdict="strong", reasons='["r"]', origin="llm")
    s.set_match_pending("acme", "u")
    s.clear_match_pending("acme", "u")
    assert s.pending_matches(limit=10) == []
    rec = s.open_positions("acme")[0]
    assert rec.match_score is None
    assert rec.match_verdict is None
    assert rec.match_reasons is None
    assert rec.matched_at is None
    s.close()


def test_concurrent_reads_and_writes_survive_a_shared_connection(tmp_path):
    """Regression: the web UI (§16) serves reads from an anyio threadpool while the
    §4.9 scheduler writes off-thread, both over one ``check_same_thread=False``
    connection. Unserialized, concurrent stepping raised ``sqlite3.InterfaceError:
    bad parameter or other API misuse`` (a hard 500 on the dashboard). All access now
    funnels through one reentrant lock, so a storm of parallel calls completes cleanly.
    """
    import concurrent.futures

    s = StateStore(str(tmp_path / "concurrent.db"))
    for i in range(40):
        s.upsert_position("acme", f"u{i}", fields=FIELDS, content_hash=f"h{i}")

    errors: list[Exception] = []

    def hammer(n: int) -> None:
        try:
            for r in range(30):
                s.open_counts_by_site()
                s.track_counts()
                s.get_health("acme")
                s.last_run("acme")
                s.open_positions("acme")
                s.mark_seen("acme", f"seen-{n}-{r}")
        except Exception as exc:  # noqa: BLE001 — capture, assert on the main thread
            errors.append(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(hammer, range(8)))

    assert not errors, errors
    s.close()


def test_score_histogram_buckets_open_scored_rows_in_sql():
    # §16 dashboard: the score histogram is a single SQL aggregate, not a 5000-row load.
    # Only open, scored rows count; a score is placed in the first band that contains it.
    s = StateStore(":memory:")

    def scored(url: str, score: int, *, closed: bool = False) -> None:
        s.upsert_position("acme", url, fields=FIELDS, content_hash=url)
        s.set_match("acme", url, score=score, verdict="strong", reasons="[]")
        if closed:
            s.mark_closed("acme", url, reason="filled")

    scored("a", 95)  # band 0: 80–100
    scored("b", 80)  # band 0 (inclusive low edge)
    scored("c", 79)  # band 1: 60–79
    scored("d", 59)  # band 2: 40–59
    scored("e", 0)  # band 4: 0–19
    scored("f", 90, closed=True)  # closed → excluded
    s.upsert_position(
        "acme", "g", fields=FIELDS, content_hash="g"
    )  # unscored → excluded

    bands = [(80, 100), (60, 79), (40, 59), (20, 39), (0, 19)]
    assert s.score_histogram(bands) == [2, 1, 1, 0, 1]
    assert s.score_histogram([]) == []
    s.close()


def test_positions_status_score_index_is_created():
    # The histogram / top-matches / matched-count paths depend on this index to avoid a
    # full corpus scan+sort; _SCHEMA creates it (IF NOT EXISTS, so also on existing DBs).
    s = StateStore(":memory:")
    names = {
        row["name"]
        for row in s._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'positions'"
        )
    }
    assert "positions_status_score" in names
    s.close()
