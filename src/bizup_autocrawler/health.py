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
    ) -> None:
        self._store = store
        self._reprobe = reprobe  # async (slug) -> items re-parsed
        self._regenerate = regenerate  # async (slug) -> produced a gate-passing crawler

    async def assess(self, slug: str) -> HealthState:
        """Detect → confirm → regenerate, returning the resulting health state."""
        if not staleness_candidate(self._store.runs_for(slug)):
            return HealthState.HEALTHY
        if await self._reprobe(slug) > 0:
            return HealthState.HEALTHY  # the re-probe re-parsed items: a tolerated blip
        return regeneration_state(gate_passed=await self._regenerate(slug))
