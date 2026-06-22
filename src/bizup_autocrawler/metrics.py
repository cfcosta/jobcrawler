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


def recovery_events(
    history: Sequence[RunRecord],
) -> list[tuple[datetime, datetime]]:
    """Derive (went-dark → recovered) pairs from one site's run history (§4.8).

    ``drift_mttr`` wants (dark, enabled) timestamps, but the state store keeps no
    health-transition log; the only durable signal is the §5 run history. So the
    status surface reconstructs *observed* drift recoveries from it: starting from a
    live baseline (a finished run that found items), the first subsequent finished run
    that finds none marks the site going dark, and the next finished run that finds
    items again marks recovery. Each completed dark→recovered cycle yields one pair;
    an as-yet-unrecovered drift yields nothing (MTTR is over recovered drifts only),
    and a site that has only ever returned zero never went dark *from healthy*.

    History need not be sorted (``runs_for`` returns newest-first); only finished runs
    count. This measures recovery as observed at the item level — broader than §4.8's
    "gate-passing replacement enabled" (it also catches a site that recovered on its
    own), so the surface labels it *observed*, never claims the precise regen metric.
    """
    runs = sorted(
        ((r.finished, r.items_found) for r in history if r.finished is not None),
        key=lambda pair: pair[0],
    )
    events: list[tuple[datetime, datetime]] = []
    seen_alive = False
    dark_at: datetime | None = None
    for finished, items in runs:
        if items > 0:
            if dark_at is not None:
                events.append((dark_at, finished))
                dark_at = None
            seen_alive = True
        elif seen_alive and dark_at is None:
            dark_at = finished
    return events
