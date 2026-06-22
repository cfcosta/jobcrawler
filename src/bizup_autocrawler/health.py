"""Health detection & self-healing regeneration (§4.8).

"Regenerate when the site changes," made automatic. A site is flagged **stale**
on a *single* zero-``items_found`` run against a baseline that previously yielded
items — never on zero ``new_items`` (a healthy low-frequency feed legitimately
adds nothing). Because a one-off network blip would otherwise trip it, the
candidate is confirmed by a short **re-probe that re-parses >= 1 item** (not
merely network-OK); one re-probe-cleared blip is tolerated.

A confirmed-stale site is re-entered into §4.2 generation with the *current*
generator. By default (``AUTOCRAWLER_REVIEW_GATE`` on) a gate-passing crawler is
**staged for human promotion**, not delivered live — the site enters a distinct
**pending-review** state (§14.2) and keeps running its old crawler until an operator
runs ``autocrawl promote``. If generation exhausts its budget without a gate-passing
crawler — including a forced extraction — the site enters a **generation-failed**
state instead. Both are terminal-until-recovery: surfaced separately and *not*
silently re-enqueued (a failed regen would spin; re-staging would clobber the
candidate under review). This module owns the decision logic; re-probe and
regeneration are injected (they run the live crawler/generator) so the policy is
tested without them — the injected ``regenerate`` reports which delivery happened.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import Enum

from bizup_autocrawler.state import RunRecord, StateStore


class HealthState(Enum):
    HEALTHY = "healthy"
    STALE = "stale"
    PENDING_REVIEW = "pending_review"
    GENERATION_FAILED = "generation_failed"


class RegenOutcome(Enum):
    """What a self-heal regeneration actually delivered (§4.8, §14.2).

    The injected ``regenerate`` reports this so the policy can distinguish a crawler
    that went live (site healed) from one merely staged for human promotion (still
    awaiting review) from a generation that produced nothing.
    """

    PROMOTED = "promoted"  # gate-passing crawler written live → healed
    STAGED = "staged"  # gate-passing crawler staged for human promotion (§14.2)
    FAILED = "failed"  # no gate-passing crawler produced


# A terminal-until-recovery state has been acted on for the *current* drift, so a
# later assessment must surface it rather than re-running regeneration: re-failing
# would spin, and re-staging would clobber the candidate a human is reviewing.
_HELD_STATES = frozenset(
    {HealthState.GENERATION_FAILED.value, HealthState.PENDING_REVIEW.value}
)

_REGEN_STATE = {
    RegenOutcome.PROMOTED: HealthState.HEALTHY,
    RegenOutcome.STAGED: HealthState.PENDING_REVIEW,
    RegenOutcome.FAILED: HealthState.GENERATION_FAILED,
}


def staleness_candidate(history: list[RunRecord]) -> bool:
    """True if the latest run found zero items against a baseline that did (§4.8).

    ``history`` is newest-first (as :meth:`StateStore.runs_for` returns it).
    """
    if not history:
        return False
    if history[0].items_found != 0:
        return False
    return any(run.items_found > 0 for run in history[1:])


def regeneration_state(outcome: RegenOutcome) -> HealthState:
    """Map a regeneration attempt's delivery outcome to the resulting health state."""
    return _REGEN_STATE[outcome]


class SelfHealer:
    def __init__(
        self,
        store: StateStore,
        *,
        reprobe: Callable[[str], Awaitable[int]],
        regenerate: Callable[[str], Awaitable[RegenOutcome]],
        on_confirmed_failure: Callable[[str], None] | None = None,
    ) -> None:
        self._store = store
        self._reprobe = reprobe  # async (slug) -> items re-parsed
        self._regenerate = regenerate  # async (slug) -> how the regen was delivered
        # Called once a failure is confirmed real (re-probe ruled out a transient
        # outage) — the §4.11 hook that captures the site as a hard optimizer case.
        self._on_confirmed_failure = on_confirmed_failure

    async def assess(self, slug: str) -> HealthState:
        """Detect → confirm → regenerate, persisting and returning the new state."""
        if not staleness_candidate(self._store.runs_for(slug)):
            # Not a candidate (incl. a previously failed/pending site now finding items
            # again) → healthy, which also clears any stuck terminal state.
            return self._record(slug, HealthState.HEALTHY)
        held = self._store.get_health(slug)
        if held in _HELD_STATES:
            # Already acted on this drift — a failed generation (re-running would spin,
            # §4.8) or a crawler staged for human promotion (§14.2; re-staging would
            # clobber the candidate under review). Surface it until the site recovers.
            return HealthState(held)
        if await self._reprobe(slug) > 0:
            return self._record(slug, HealthState.HEALTHY)  # re-parsed: tolerated blip
        # Staleness confirmed: a real failure, not a transient outage. Capture it as a
        # §4.11 hard case (the optimizer learns from failures) before self-healing.
        if self._on_confirmed_failure is not None:
            self._on_confirmed_failure(slug)
        return self._record(slug, regeneration_state(await self._regenerate(slug)))

    def _record(self, slug: str, state: HealthState) -> HealthState:
        self._store.set_health(slug, state.value)
        return state
