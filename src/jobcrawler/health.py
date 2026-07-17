"""Health detection & self-healing regeneration (§4.8).

"Regenerate when the board changes," made automatic. A site drifts in two ways:

  * **blackout** — a *single* zero-``items_found`` run (zero *positions* found) against a
    baseline that previously yielded positions. This is **never** keyed on ``new_items``:
    a quiet-but-healthy board legitimately adds nothing between crawls (``new_items == 0``
    is normal), so only ``items_found == 0`` — the board returning *no open positions at
    all* — is a blackout. A one-off network blip would trip this, so the candidate is
    confirmed by a short **re-probe that re-parses >= 1 position** (not merely
    network-OK); one re-probe-cleared blip is tolerated. A confirmed 0-yield whose
    board's own ATS *advertises zero openings* is a **validly empty** board, not
    drift: it parks healthy and is re-checked by the sweep, never regenerated
    (regeneration cannot conjure postings that don't exist).
  * **partial drift** — positions still come back, but their descriptions collapse to
    boilerplate/truncated bodies (the description-completeness signal, persisted under the
    ``body_quality`` key, falls below the host-side floor) for the latest *two* runs
    against a baseline that was above it. Two consecutive sub-floor runs already rule
    out a one-off truncation, so this path skips the re-probe and regenerates directly.

The same path also handles the **cold-start** case: a registry entry with no committed
crawler at all (nothing to crawl, nothing to re-probe) is an immediately-confirmed
candidate, so the daemon *writes its first crawler* before the loop can crawl it —
delivered through the very same review gate as a self-heal.

A confirmed-drifted (or never-generated) site is re-entered into §4.2 generation with
the *current* generator. By default (``JOBCRAWLER_REVIEW_GATE`` on) a gate-passing
crawler is
**staged for human promotion**, not delivered live — the site enters a distinct
**pending-review** state (§14.2) and keeps running its old crawler until an operator
runs ``jobcrawl promote``. If generation exhausts its budget without a gate-passing
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

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from enum import Enum

from jobcrawler.notify import notify
from jobcrawler.state import HealthRecord, RunRecord, StateStore

_log = logging.getLogger(__name__)


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
    """True if the latest run found zero positions against a baseline that did (§4.8).

    Keyed on ``items_found`` (the §5 count of open positions the crawl returned), never
    on ``new_items``: a quiet board that adds nothing this crawl is healthy, so only a run
    that returns *no positions at all* against a previously-yielding baseline is stale.
    ``history`` is newest-first (as :meth:`StateStore.runs_for` returns it).
    """
    if not history:
        return False
    if history[0].items_found != 0:
        return False
    return any(run.items_found > 0 for run in history[1:])


def _body_quality(run: RunRecord) -> float | None:
    """The run's recorded description-completeness signal, or None if it carried none.

    Persisted by the runner under the ``body_quality`` signal key: the share of a crawl's
    fetched positions whose description cleared the §4.6 completeness bar. A zero-yield run
    records none (the blackout signal is the position count itself), so this is None there
    — keeping partial-drift detection disjoint from blackout detection.
    """
    if not run.signals:
        return None
    value = run.signals.get("body_quality")
    return float(value) if isinstance(value, (int, float)) else None


def body_quality_collapse(
    history: list[RunRecord], *, floor: float, confirm_runs: int = 2
) -> bool:
    """True if recent descriptions collapsed to boilerplate against a healthy baseline (§4.8).

    Partial drift: a site keeps returning positions but their descriptions fall below the
    §4.6 completeness bar (the ``body_quality`` signal < ``floor``) for the latest
    ``confirm_runs`` runs, while some earlier run was at or above the floor. Requiring two
    consecutive sub-floor runs rules out a one-off upstream truncation, which is why the
    caller can skip the re-probe for this path. ``history`` is newest-first.
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
        board_empty: Callable[[str], Awaitable[bool]] | None = None,
        on_confirmed_failure: Callable[[str], None] | None = None,
        body_quality_floor: float = 0.5,
        clock: Callable[[], datetime] = _utcnow,
        genfail_backoff: float = 6 * 3600.0,
        genfail_backoff_cap: float = 7 * 86400.0,
        genfail_max_attempts: int = 8,
        review_mode: Callable[[], str] = lambda: "on",
        promote: Callable[[str], Awaitable[bool]] | None = None,
        has_crawler: Callable[[str], bool] | None = None,
    ) -> None:
        self._store = store
        self._reprobe = reprobe  # async (slug) -> items re-parsed
        self._regenerate = regenerate  # async (slug) -> how the regen was delivered
        # Is the board *validly empty* — its own ATS API advertising zero open
        # roles? A 0-yield crawl against an empty board is a fact about the site,
        # not crawler drift: regenerating cannot conjure postings that don't
        # exist, and on an empty board a regenerated candidate is promoted via
        # the §6 empty-board admission without writing a run row — so the
        # staleness signal never clears and the sweep regenerates forever (the
        # mistral loop: ~900 LM calls against a board that had simply emptied).
        # When this answers True the site parks HEALTHY instead; the sweep's
        # cheap probe re-checks every pass, and the first crawl that yields again
        # clears the blackout signal naturally. None → feature off (regen as
        # ever).
        self._board_empty = board_empty
        # Does the site have a committed crawler yet? A registry entry with none is a
        # §4.2 cold-start: there's nothing to crawl, so assess writes the first crawler
        # before the loop can crawl it. Defaults to "always present", so a healer wired
        # without this (the pure-drift tests) behaves exactly as it did before.
        self._has_crawler = has_crawler or (lambda _slug: True)
        # Called once a failure is confirmed real (re-probe ruled out a transient
        # outage) — the §4.11 hook that captures the site as a hard optimizer case.
        self._on_confirmed_failure = on_confirmed_failure
        # Host-side partial-drift floor: the minimum description-completeness share a
        # crawl must clear before its descriptions count as collapsed (§8: never reaches
        # the generator/RLM). Kept named ``body_quality_floor`` — the persisted signal key
        # it is compared against is ``body_quality`` — while the operator-facing env knob
        # is ``JOBCRAWLER_DESCRIPTION_QUALITY_FLOOR``.
        self._body_quality_floor = body_quality_floor
        self._clock = clock
        self._genfail_backoff = genfail_backoff
        self._genfail_backoff_cap = genfail_backoff_cap
        # After this many consecutive generation failures a site is parked — held
        # generation_failed and no longer auto-re-generated — so a chronically dead board
        # stops burning generation budget forever. 0 disables the cap (retry indefinitely).
        self._genfail_max_attempts = genfail_max_attempts
        # The current review gate ("on"/"auto"/"off") and a promote-staged-crawler hook;
        # together they auto-clear a pending-review strand when no longer human-gated.
        self._review_mode = review_mode
        self._promote = promote

    async def assess(self, slug: str) -> HealthState:
        """Detect → confirm → regenerate, persisting and returning the new state.

        A registry entry with no committed crawler is a §4.2 cold-start: there's nothing
        to crawl and nothing to re-probe, so it's an immediately-confirmed regenerate
        candidate (write the first crawler) that then runs through the same delivery and
        backoff machinery as a self-heal — staged for review by default, live under the
        ``auto``/``off`` gate. The held-state guards below keep a staged or failed
        bootstrap from re-generating on every sweep.
        """
        history = self._store.runs_for(slug)
        missing = not self._has_crawler(slug)
        blackout = staleness_candidate(history)
        collapse = not blackout and body_quality_collapse(
            history, floor=self._body_quality_floor
        )
        if not missing and not blackout and not collapse:
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
            # Past the attempt cap the site is *parked* — a chronically dead board (a
            # retired URL, an un-crawlable rewrite) stops re-generating so the spend is
            # bounded. It stays surfaced as generation_failed and un-parks only when it
            # heals on its own (→ HEALTHY above) or an operator regenerates it by hand.
            if self._parked(record):
                return HealthState.GENERATION_FAILED
            if record is None or not self._retry_due(record):
                return HealthState.GENERATION_FAILED
        # A blackout might be a transient outage, so confirm it with a re-probe. A
        # body-quality collapse (two consecutive sub-floor runs) and a missing crawler
        # (nothing to crawl) are already confirmed, so they skip straight to regeneration.
        if blackout and not missing:
            if await self._reprobe(slug) > 0:
                # re-parsed: tolerated blip
                return self._record(slug, HealthState.HEALTHY)
            # A confirmed 0-yield can also mean the board itself is empty — its
            # ATS advertising zero openings. That needs watching, not healing:
            # park HEALTHY and let the sweep's probe re-check each pass (see
            # ``board_empty`` in __init__ for why regenerating here loops).
            if self._board_empty is not None and await self._board_empty(slug):
                _log.info(
                    "%s: board is validly empty (its ATS advertises 0 openings); "
                    "parking healthy and watching instead of regenerating",
                    slug,
                )
                return self._record(slug, HealthState.HEALTHY)
        # Drift confirmed: a real failure, not a transient outage. Capture it as a §4.11
        # hard case (the optimizer learns from failures) before self-healing. A cold-start
        # is a new site, not a failure to learn from, so it is not captured.
        if not missing and self._on_confirmed_failure is not None:
            self._on_confirmed_failure(slug)
        try:
            outcome = await self._regenerate(slug)
        except Exception:
            # A regeneration that *crashes* (entrypoint refusing the corpus prefetch,
            # LM transport, sandbox runtime) must land in the same held state as one
            # that produced nothing: an escaping exception writes no health record,
            # so no geometric backoff engages and the site re-enters the same crash
            # every tick — a hot loop the generation-failed hold exists to prevent.
            _log.exception(
                "regeneration crashed for %s; held as generation-failed", slug
            )
            outcome = RegenOutcome.FAILED
        _notify_regen(slug, outcome)
        state = self._record(slug, regeneration_state(outcome))
        if state is HealthState.GENERATION_FAILED and self._just_parked(record):
            self._notify_parked(slug)
        return state

    def note_abandoned(self, slug: str) -> HealthState:
        """Record a §4.9 watchdog-abandoned generation as one failed attempt.

        The liveness watchdog bounds a single ``assess`` by cancelling its coroutine
        when a generation outruns the budget. Delivery runs on a detached ``to_thread``
        worker that keeps going and still writes its ``generation_log`` row — but the
        cancelled coroutine never reaches :meth:`_record`, so the attempt leaves no
        health record. The consecutive-failure counter never bumps, so neither the
        geometric backoff nor the park cap ever engages, and a permanently-slow board
        re-generates on every tick forever (observed: a slow Workday board at 12
        attempts / 622 LM calls with no ``site_health`` row at all). The scheduler calls
        this from the watchdog path so an abandoned generation costs exactly one attempt
        against the same backoff + park budget as any other failure. A later worker that
        still promotes the crawler heals the site (→ HEALTHY) and resets the counter on
        the next sweep; a genuinely dead board parks after ``genfail_max_attempts`` and
        stops burning budget.
        """
        prior = self._store.get_health_record(slug)
        state = self._record(slug, HealthState.GENERATION_FAILED)
        if self._just_parked(prior):
            self._notify_parked(slug)
        return state

    def _notify_parked(self, slug: str) -> None:
        """Operator alert fired once when a site reaches the genfail attempt cap (§4.8)."""
        notify(
            "heal.parked",
            f"parked {slug}: generation failed {self._genfail_max_attempts}x in a "
            "row — no longer auto-retrying; regenerate by hand once the board is "
            "crawlable again",
            slug=slug,
        )

    async def _handle_pending(self, slug: str) -> HealthState:
        """A held pending-review site: hold for a human under ``on``, else auto-promote.

        With the review gate ``on`` the staged crawler awaits ``jobcrawl promote`` —
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

    def _parked(self, record: HealthRecord | None) -> bool:
        """True once a generation-failed site has spent its auto-retry budget (§4.8).

        A parked site is held ``generation_failed`` and never re-generated again, so a
        chronically dead board stops burning budget. ``genfail_max_attempts == 0`` opts
        out (retry forever).
        """
        return (
            self._genfail_max_attempts > 0
            and record is not None
            and record.attempts >= self._genfail_max_attempts
        )

    def _just_parked(self, prior: HealthRecord | None) -> bool:
        """True when the failure just recorded is the one that reaches the attempt cap.

        Fires exactly once at the crossing (``==``): once parked, later sweeps return
        early via :meth:`_parked` and never reach the regeneration path again, so the
        operator is notified a single time rather than every tick.
        """
        if self._genfail_max_attempts <= 0:
            return False
        prior_attempts = prior.attempts if prior is not None else 0
        return prior_attempts + 1 == self._genfail_max_attempts

    def _record(self, slug: str, state: HealthState) -> HealthState:
        # Bump the consecutive-failure counter on generation_failed (drives the retry
        # backoff); any other state resets it to 0.
        self._store.set_health(
            slug,
            state.value,
            bump_attempts=state is HealthState.GENERATION_FAILED,
        )
        return state
