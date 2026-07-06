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
        "employment_type": "full_time",
        "department": "Platform",
    }
    store.upsert_position("acme", "u", fields=fields, content_hash="h")
    rec = store.open_positions("acme")[0]
    assert (rec.discipline, rec.level, rec.workplace) == ("backend", "senior", "remote")
    assert (rec.salary_min, rec.salary_max) == (152800.0, 259200.0)
    assert (rec.salary_currency, rec.salary_period) == ("USD", "year")
    assert (rec.employment_type, rec.department, rec.external_id) == (
        "full_time",
        "Platform",
        "ATS-42",
    )


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
        "discipline",
        "level",
        "workplace",
        "salary_min",
        "salary_max",
        "salary_currency",
        "salary_period",
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
    assert [r.url for r in s.matches(70)] == ["u"]
    s.close()


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
