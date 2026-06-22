"""Tests for metrics / observability (M4)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bizup_autocrawler.health import HealthState
from bizup_autocrawler.metrics import (
    aggregate_runs,
    drift_mttr,
    health_distribution,
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
