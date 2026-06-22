"""Tests for metrics / observability (M4)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bizup_autocrawler.health import HealthState
from bizup_autocrawler.metrics import (
    aggregate_runs,
    drift_mttr,
    health_distribution,
    recovery_events,
)
from bizup_autocrawler.state import RunRecord

UTC = timezone.utc
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _run(
    items: int, new: int, errors: int, pages: int, finished: datetime | None
) -> RunRecord:
    return RunRecord(
        id=1,
        site="s",
        started=T0,
        finished=finished,
        items_found=items,
        new_items=new,
        errors=errors,
        pages_fetched=pages,
    )


def test_aggregate_runs_sums_and_error_rate() -> None:
    history = [
        _run(5, 2, 0, 3, T0 + timedelta(hours=2)),
        _run(4, 1, 1, 2, T0 + timedelta(hours=1)),
    ]
    agg = aggregate_runs(history)
    assert (agg.runs, agg.items_found, agg.new_items) == (2, 9, 3)
    assert (agg.errors, agg.pages_fetched) == (1, 5)
    assert agg.error_rate == 0.5
    assert agg.last_finished == T0 + timedelta(hours=2)


def test_aggregate_runs_empty() -> None:
    agg = aggregate_runs([])
    assert agg.runs == 0
    assert agg.error_rate == 0.0
    assert agg.last_finished is None


def test_drift_mttr_odd_count_is_median() -> None:
    events = [
        (T0, T0 + timedelta(hours=1)),
        (T0, T0 + timedelta(hours=3)),
        (T0, T0 + timedelta(hours=2)),
    ]
    assert drift_mttr(events) == timedelta(hours=2)


def test_drift_mttr_even_count_averages_middle_two() -> None:
    events = [
        (T0, T0 + timedelta(hours=2)),
        (T0, T0 + timedelta(hours=4)),
    ]
    assert drift_mttr(events) == timedelta(hours=3)


def test_drift_mttr_no_events() -> None:
    assert drift_mttr([]) is None


def test_health_distribution_counts_all_states() -> None:
    states = [HealthState.HEALTHY, HealthState.HEALTHY, HealthState.STALE]
    dist = health_distribution(states)
    assert dist == {
        "healthy": 2,
        "stale": 1,
        "pending_review": 0,
        "generation_failed": 0,
    }


def test_recovery_events_pairs_dark_to_recovered() -> None:
    # A drift recovery the status surface can feed drift_mttr without a transition log:
    # a live baseline (items>0) → a dark run (items==0) → a recovered run (items>0)
    # yields one (went-dark-finished, recovered-finished) pair.
    history = [
        _run(5, 2, 0, 3, T0),
        _run(0, 0, 1, 1, T0 + timedelta(hours=1)),
        _run(7, 3, 0, 4, T0 + timedelta(hours=3)),
    ]
    assert recovery_events(history) == [
        (T0 + timedelta(hours=1), T0 + timedelta(hours=3))
    ]


def test_recovery_events_ignores_unrecovered_drift() -> None:
    # A site still dark has no recovery yet — MTTR is over *recovered* drifts only.
    history = [
        _run(5, 2, 0, 3, T0),
        _run(0, 0, 1, 1, T0 + timedelta(hours=1)),
    ]
    assert recovery_events(history) == []


def test_recovery_events_needs_a_live_baseline() -> None:
    # A site that has only ever returned zero never "went dark" from healthy.
    history = [
        _run(0, 0, 0, 1, T0),
        _run(0, 0, 0, 1, T0 + timedelta(hours=1)),
    ]
    assert recovery_events(history) == []


def test_recovery_events_sorts_newest_first_history() -> None:
    # runs_for returns newest-first; recovery_events sorts chronologically and uses the
    # FIRST zero run after a live baseline as the dark moment, the first subsequent
    # items>0 run as the recovery.
    history = [
        _run(7, 3, 0, 4, T0 + timedelta(hours=4)),  # recovered (newest)
        _run(0, 0, 1, 1, T0 + timedelta(hours=3)),  # still dark
        _run(0, 0, 1, 1, T0 + timedelta(hours=2)),  # went dark here
        _run(5, 2, 0, 3, T0 + timedelta(hours=1)),  # live baseline (oldest)
    ]
    assert recovery_events(history) == [
        (T0 + timedelta(hours=2), T0 + timedelta(hours=4))
    ]


def test_recovery_events_handles_multiple_cycles() -> None:
    # Two independent dark→recovered cycles yield two events (the dark marker resets).
    history = [
        _run(5, 2, 0, 3, T0),
        _run(0, 0, 1, 1, T0 + timedelta(hours=1)),
        _run(6, 2, 0, 3, T0 + timedelta(hours=2)),
        _run(0, 0, 1, 1, T0 + timedelta(hours=4)),
        _run(8, 4, 0, 5, T0 + timedelta(hours=5)),
    ]
    assert recovery_events(history) == [
        (T0 + timedelta(hours=1), T0 + timedelta(hours=2)),
        (T0 + timedelta(hours=4), T0 + timedelta(hours=5)),
    ]
