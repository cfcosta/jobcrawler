"""Health detection & self-healing regeneration (§4.8).

"Regenerate when the site changes," made automatic. A site is flagged **stale**
on a *single* zero-``items_found`` run against a baseline that previously yielded
items — never on zero ``new_items`` (a healthy low-frequency feed legitimately
adds nothing). Because a one-off network blip would otherwise trip it, the
candidate is confirmed by a short **re-probe that re-parses >= 1 item** (not
merely network-OK); one re-probe-cleared blip is tolerated.

A confirmed-stale site is re-entered into §4.2 generation with the *current*
generator; the new script is validated and proposed to replace the old (review
gate, §8). If generation exhausts its budget without a gate-passing crawler —
including a forced extraction — the site enters a distinct **generation-failed**
state, surfaced separately and *not* silently re-enqueued (which would spin).
This module owns the decision logic; re-probe and regeneration are injected
(they run the live crawler/generator) so the policy is tested without them.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import Enum

from bizup_autocrawler.state import RunRecord, StateStore


class HealthState(Enum):
    HEALTHY = "healthy"
    STALE = "stale"
    GENERATION_FAILED = "generation_failed"


def staleness_candidate(history: list[RunRecord]) -> bool:
    """True if the latest run found zero items against a baseline that did (§4.8).

    ``history`` is newest-first (as :meth:`StateStore.runs_for` returns it).
    """
    if not history:
        return False
    if history[0].items_found != 0:
        return False
    return any(run.items_found > 0 for run in history[1:])


def regeneration_state(*, gate_passed: bool) -> HealthState:
    """Map a regeneration attempt's outcome to the resulting health state."""
    return HealthState.HEALTHY if gate_passed else HealthState.GENERATION_FAILED


class SelfHealer:
    def __init__(
        self,
        store: StateStore,
        *,
        reprobe: Callable[[str], Awaitable[int]],
        regenerate: Callable[[str], Awaitable[bool]],
        on_confirmed_failure: Callable[[str], None] | None = None,
    ) -> None:
        self._store = store
        self._reprobe = reprobe  # async (slug) -> items re-parsed
        self._regenerate = regenerate  # async (slug) -> produced a gate-passing crawler
        # Called once a failure is confirmed real (re-probe ruled out a transient
        # outage) — the §4.11 hook that captures the site as a hard optimizer case.
        self._on_confirmed_failure = on_confirmed_failure

    async def assess(self, slug: str) -> HealthState:
        """Detect → confirm → regenerate, persisting and returning the new state."""
        if not staleness_candidate(self._store.runs_for(slug)):
            # Not a candidate (incl. a previously generation-failed site now finding
            # items again) → healthy, which also clears any stuck failed state.
            return self._record(slug, HealthState.HEALTHY)
        if self._store.get_health(slug) == HealthState.GENERATION_FAILED.value:
            # Already failed generation against this drift; do not re-enqueue the same
            # failing generation (it would spin, §4.8) — surface it until it recovers.
            return HealthState.GENERATION_FAILED
        if await self._reprobe(slug) > 0:
            return self._record(slug, HealthState.HEALTHY)  # re-parsed: tolerated blip
        # Staleness confirmed: a real failure, not a transient outage. Capture it as a
        # §4.11 hard case (the optimizer learns from failures) before self-healing.
        if self._on_confirmed_failure is not None:
            self._on_confirmed_failure(slug)
        return self._record(
            slug, regeneration_state(gate_passed=await self._regenerate(slug))
        )

    def _record(self, slug: str, state: HealthState) -> HealthState:
        self._store.set_health(slug, state.value)
        return state
