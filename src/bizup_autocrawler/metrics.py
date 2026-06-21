"""Metrics & observability (M4).

Pure aggregations over the §5 run history and §4.8 health signals — the numbers
an operator (or the optional status surface) reads. ``drift_mttr`` is the §4.8
observability metric: median time from a previously-healthy site going dark to a
gate-passing replacement being *enabled*. It is tracked, never an exit gate.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from bizup_autocrawler.health import HealthState
from bizup_autocrawler.state import RunRecord


@dataclass(frozen=True, slots=True)
class RunAggregate:
    runs: int
    items_found: int
    new_items: int
    errors: int
    pages_fetched: int
    last_finished: datetime | None

    @property
    def error_rate(self) -> float:
        return self.errors / self.runs if self.runs else 0.0


def aggregate_runs(history: Sequence[RunRecord]) -> RunAggregate:
    """Fold a site's run history into totals + last-finished (for §4.8/observability)."""
    finished = [r.finished for r in history if r.finished is not None]
    return RunAggregate(
        runs=len(history),
        items_found=sum(r.items_found for r in history),
        new_items=sum(r.new_items for r in history),
        errors=sum(r.errors for r in history),
        pages_fetched=sum(r.pages_fetched for r in history),
        last_finished=max(finished) if finished else None,
    )


def drift_mttr(events: Sequence[tuple[datetime, datetime]]) -> timedelta | None:
    """Median (went-dark → replacement-enabled) over drift events, or None (§4.8)."""
    if not events:
        return None
    deltas = sorted(enabled - dark for dark, enabled in events)
    mid = len(deltas) // 2
    if len(deltas) % 2 == 1:
        return deltas[mid]
    return (deltas[mid - 1] + deltas[mid]) / 2


def health_distribution(states: Sequence[HealthState]) -> dict[str, int]:
    """Count sites by health state — always reports all states (0 if absent)."""
    counts = {state.value: 0 for state in HealthState}
    for state in states:
        counts[state.value] += 1
    return counts
