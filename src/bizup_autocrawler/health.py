"""Health detection & self-healing regeneration (§4.8).

"Regenerate when the site changes," made automatic. A site drifts in two ways:

  * **blackout** — a *single* zero-``items_found`` run against a baseline that
    previously yielded items (never zero ``new_items``: a healthy low-frequency feed
    legitimately adds nothing). A one-off network blip would trip this, so the
    candidate is confirmed by a short **re-probe that re-parses >= 1 item** (not
    merely network-OK); one re-probe-cleared blip is tolerated.
  * **partial drift** — items still come back, but their bodies collapse to teasers
    (``body_quality`` falls below the host-side floor) for the latest *two* runs
    against a baseline that was above it. Two consecutive sub-floor runs already rule
    out a one-off truncation, so this path skips the re-probe and regenerates directly.

A confirmed-drifted site is re-entered into §4.2 generation with the *current*
generator. By default (``AUTOCRAWLER_REVIEW_GATE`` on) a gate-passing crawler is
**staged for human promotion**, not delivered live — the site enters a distinct
**pending-review** state (§14.2) and keeps running its old crawler until an operator
runs ``autocrawl promote``. If generation exhausts its budget without a gate-passing
crawler — including a forced extraction — the site enters a **generation-failed**
state instead. Both are held rather than silently re-enqueued (an immediate failed
regen would spin; re-staging would clobber the candidate under review), but neither
is a dead end:

  * a **generation-failed** site is re-tried with the *current* generator after a
    geometric backoff (``attempts`` failures → ``base × 2^(attempts-1)``, capped),
    so the offline §4.11 loop improving the generator eventually gets another shot;
  * a **pending-review** site is auto-cleared when the review gate is no longer ``on``
    (an operator switched to ``auto``/``off`` after a crawler was staged): the staged
    crawler is promoted live rather than waiting forever for a human who isn't coming.

This module owns the decision logic; re-probe, regeneration, and promotion are
injected (they run the live crawler/generator) so the policy is tested without them —
the injected ``regenerate`` reports which delivery happened.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from enum import Enum

from bizup_autocrawler.notify import notify
from bizup_autocrawler.state import HealthRecord, RunRecord, StateStore


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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


def _body_quality(run: RunRecord) -> float | None:
    """The run's recorded ``body_quality`` signal, or None if it carried none.

    A zero-yield run records no ``body_quality`` (the blackout signal is the item
    count itself), so this is None there — keeping partial-drift detection disjoint
    from blackout detection.
    """
    if not run.signals:
        return None
    value = run.signals.get("body_quality")
    return float(value) if isinstance(value, (int, float)) else None


def body_quality_collapse(
    history: list[RunRecord], *, floor: float, confirm_runs: int = 2
) -> bool:
    """True if recent runs' bodies collapsed to teasers against a healthy baseline (§4.8).

    Partial drift: a site keeps returning items but their bodies fall below the
    completeness bar (``body_quality`` < ``floor``) for the latest ``confirm_runs``
    runs, while some earlier run was at or above the floor. Requiring two consecutive
    sub-floor runs rules out a one-off upstream truncation, which is why the caller can
    skip the re-probe for this path. ``history`` is newest-first.
    """
    recent = history[:confirm_runs]
    if len(recent) < confirm_runs:
        return False  # not enough runs yet to confirm a persistent collapse
    for run in recent:
        bq = _body_quality(run)
        if run.items_found == 0 or bq is None or bq >= floor:
            return False
    return any(
        (bq := _body_quality(run)) is not None and bq >= floor
        for run in history[confirm_runs:]
    )


def regeneration_state(outcome: RegenOutcome) -> HealthState:
    """Map a regeneration attempt's delivery outcome to the resulting health state."""
    return _REGEN_STATE[outcome]


_REGEN_NOTICE = {
    RegenOutcome.PROMOTED: (
        "heal.promoted",
        "self-healed {slug}: regenerated crawler promoted live",
    ),
    RegenOutcome.STAGED: (
        "heal.staged",
        "self-healed {slug}: regenerated crawler staged for review",
    ),
    RegenOutcome.FAILED: (
        "heal.failed",
        "self-heal of {slug} failed: no gate-passing crawler produced",
    ),
}


def _notify_regen(slug: str, outcome: RegenOutcome) -> None:
    """Push an operator alert for a completed self-heal (§4.8 autonomous action)."""
    event, template = _REGEN_NOTICE[outcome]
    notify(event, template.format(slug=slug), slug=slug, outcome=outcome.value)


class SelfHealer:
    def __init__(
        self,
        store: StateStore,
        *,
        reprobe: Callable[[str], Awaitable[int]],
        regenerate: Callable[[str], Awaitable[RegenOutcome]],
        on_confirmed_failure: Callable[[str], None] | None = None,
        body_quality_floor: float = 0.5,
        clock: Callable[[], datetime] = _utcnow,
        genfail_backoff: float = 6 * 3600.0,
        genfail_backoff_cap: float = 7 * 86400.0,
        review_mode: Callable[[], str] = lambda: "on",
        promote: Callable[[str], Awaitable[bool]] | None = None,
    ) -> None:
        self._store = store
        self._reprobe = reprobe  # async (slug) -> items re-parsed
        self._regenerate = regenerate  # async (slug) -> how the regen was delivered
        # Called once a failure is confirmed real (re-probe ruled out a transient
        # outage) — the §4.11 hook that captures the site as a hard optimizer case.
        self._on_confirmed_failure = on_confirmed_failure
        # Host-side partial-drift floor (§8: never reaches the generator/RLM).
        self._body_quality_floor = body_quality_floor
        self._clock = clock
        self._genfail_backoff = genfail_backoff
        self._genfail_backoff_cap = genfail_backoff_cap
        # The current review gate ("on"/"auto"/"off") and a promote-staged-crawler hook;
        # together they auto-clear a pending-review strand when no longer human-gated.
        self._review_mode = review_mode
        self._promote = promote

    async def assess(self, slug: str) -> HealthState:
        """Detect → confirm → regenerate, persisting and returning the new state."""
        history = self._store.runs_for(slug)
        blackout = staleness_candidate(history)
        collapse = not blackout and body_quality_collapse(
            history, floor=self._body_quality_floor
        )
        if not blackout and not collapse:
            # Not a candidate (incl. a previously failed/pending site now healthy
            # again) → healthy, which also clears any stuck terminal state.
            return self._record(slug, HealthState.HEALTHY)
        record = self._store.get_health_record(slug)
        held = record.state if record is not None else None
        if held == HealthState.PENDING_REVIEW.value:
            return await self._handle_pending(slug)
        if held == HealthState.GENERATION_FAILED.value:
            # Held, but not forever: re-try with the (offline-improved) generator once
            # the geometric backoff elapses; until then surface the failed state (§4.8).
            if record is None or not self._retry_due(record):
                return HealthState.GENERATION_FAILED
        # A blackout might be a transient outage, so confirm it with a re-probe; a
        # body-quality collapse is already confirmed by two consecutive sub-floor runs
        # (a markup change, not a network blip), so it skips straight to regeneration.
        if blackout and await self._reprobe(slug) > 0:
            return self._record(slug, HealthState.HEALTHY)  # re-parsed: tolerated blip
        # Drift confirmed: a real failure, not a transient outage. Capture it as a
        # §4.11 hard case (the optimizer learns from failures) before self-healing.
        if self._on_confirmed_failure is not None:
            self._on_confirmed_failure(slug)
        outcome = await self._regenerate(slug)
        _notify_regen(slug, outcome)
        return self._record(slug, regeneration_state(outcome))

    async def _handle_pending(self, slug: str) -> HealthState:
        """A held pending-review site: hold for a human under ``on``, else auto-promote.

        With the review gate ``on`` the staged crawler awaits ``autocrawl promote`` —
        surface PENDING_REVIEW unchanged. Once the gate is no longer human-gated
        (``auto``/``off``), a still-staged crawler is a strand: promote it live (it
        already passed the §6 gate) and clear to HEALTHY. A failed/absent promotion
        leaves it pending rather than spinning the generator.
        """
        if self._review_mode() == "on" or self._promote is None:
            return HealthState.PENDING_REVIEW
        if await self._promote(slug):
            notify(
                "heal.promoted",
                f"auto-promoted staged crawler for {slug} "
                "(review gate no longer human-gated)",
                slug=slug,
            )
            return self._record(slug, HealthState.HEALTHY)
        return HealthState.PENDING_REVIEW

    def _retry_due(self, record: HealthRecord) -> bool:
        """True once a generation-failed site's geometric backoff has elapsed (§4.8)."""
        backoff = self._genfail_backoff * (2 ** max(0, record.attempts - 1))
        backoff = min(backoff, self._genfail_backoff_cap)
        return self._clock() >= record.updated_at + timedelta(seconds=backoff)

    def _record(self, slug: str, state: HealthState) -> HealthState:
        # Bump the consecutive-failure counter on generation_failed (drives the retry
        # backoff); any other state resets it to 0.
        self._store.set_health(
            slug,
            state.value,
            bump_attempts=state is HealthState.GENERATION_FAILED,
        )
        return state
