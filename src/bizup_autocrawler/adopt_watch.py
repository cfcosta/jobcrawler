"""Post-adoption generator regression watcher + auto-rollback (§4.11, P4c).

"Full auto, gated by proof" adopts an optimized generator the instant it clears the
frozen held-out margin — but the held-out is a *proxy*: it scores the strategy on a
handful of captured hard cases, not the live fleet. A generator can clear that bar and
still regress in production, where it is exercised on every §4.8 self-heal
regeneration. This watcher is the safety net the locked design pairs with
auto-adoption: versioned rollback, made automatic.

An adoption writes a small marker (the adopted path + when). After a probation window
this compares how many sites newly entered ``generation_failed`` in the window *after*
adoption against the equal window *before* it — an adopted generator only affects *new*
regenerations, so a regression surfaces as post-adoption self-heals failing to produce
a gate-passing crawler more often than the prior generator did. If the post-adoption
failures exceed the pre-adoption baseline by the regression delta, it rolls the pointer
back (:func:`generator_pointer.rollback_active`, the same undo
``autocrawl rollback-generator`` performs) and alerts the operator; otherwise the
adoption clears probation. Either way the marker is consumed, so the judgement runs
exactly once per adoption.

The scheduler drives this on the run loop, gated by the §4.8/§4.11 master autonomy
switch (an operator who froze self-modification doesn't want an autonomous rollback
either). It reads only host-side health-log counts — nothing here reaches the
generator/RLM (the §8 hidden-scorer invariant).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from bizup_autocrawler import config, generator_pointer
from bizup_autocrawler.notify import notify
from bizup_autocrawler.state import StateStore

_log = logging.getLogger("bizup_autocrawler.adopt_watch")

# The §4.8 generation-failed health state, by value (the store keeps states as the
# strings ``HealthState`` owns; this module deliberately does not import the enum).
_GENERATION_FAILED = "generation_failed"


def regressed(pre: int, post: int, delta: int) -> bool:
    """True if post-adoption new failures exceed the pre-adoption baseline by ``delta``.

    Comparing against the prior generator's failure count in an equal window (rather
    than an absolute threshold) normalizes for a fleet's ambient flakiness: only an
    increase of at least ``delta`` newly-failing sites attributable to the swap counts
    as a regression worth rolling back.
    """
    return post - pre >= delta


def check_adoption(
    store: StateStore,
    now: datetime,
    *,
    probation: timedelta | None = None,
    regression_delta: int | None = None,
) -> str | None:
    """Judge a probationary generator adoption once its window elapses (§4.11 P4c).

    Returns the action taken — ``"rolled_back"``, ``"rollback_failed"``, or
    ``"confirmed"`` — or None when there is nothing to do yet: the watcher is disabled,
    there is no adoption under probation, or the probation window has not elapsed.
    Idempotent — it consumes the marker on a verdict, so ticks after a decision are
    no-ops until the next adoption.

    ``probation`` / ``regression_delta`` default to the configured knobs; both are
    injectable so the policy is unit-testable without the environment.
    """
    window = config.adopt_probation() if probation is None else probation
    if window is None:
        return None  # watcher disabled (AUTOCRAWLER_ADOPT_PROBATION=off)
    marker = generator_pointer.read_adoption()
    if marker is None:
        return None  # no adoption under probation
    if now < marker.adopted_at + window:
        return None  # still on probation — give the generator time to be exercised
    delta = (
        config.adopt_regression_delta()
        if regression_delta is None
        else regression_delta
    )
    # Equal-length windows on either side of the adoption keep the comparison fair even
    # if this tick fires well after the window closed (e.g. the daemon was down): always
    # measure the first ``window`` after adoption, not "up to now".
    pre = store.count_transitions_to(
        _GENERATION_FAILED, since=marker.adopted_at - window, until=marker.adopted_at
    )
    post = store.count_transitions_to(
        _GENERATION_FAILED, since=marker.adopted_at, until=marker.adopted_at + window
    )
    if regressed(pre, post, delta):
        return _rollback(marker, pre, post)
    generator_pointer.clear_adoption()
    notify(
        "generator.confirmed",
        f"adopted generator cleared probation "
        f"(generation_failed {pre}->{post} over the probation window): {marker.path}",
        path=marker.path,
        pre=pre,
        post=post,
    )
    _log.info(
        "adoption confirmed path=%s gen_failed pre=%d post=%d", marker.path, pre, post
    )
    return "confirmed"


def _rollback(marker: generator_pointer.AdoptionMarker, pre: int, post: int) -> str:
    """Undo a regressing adoption and alert the operator (§4.11 versioned rollback)."""
    ok, restored = generator_pointer.rollback_active()
    generator_pointer.clear_adoption()
    if not ok:
        # The pointer history was already empty (e.g. an operator rolled back manually
        # mid-probation): nothing left to undo, but the regression is still notable.
        notify(
            "generator.rollback_failed",
            f"post-adoption regression on {marker.path} "
            f"(generation_failed {pre}->{post}) but no prior generator to roll back to",
            path=marker.path,
            pre=pre,
            post=post,
        )
        _log.warning(
            "post-adoption regression on %s (gen_failed %d->%d) but nothing to roll back",
            marker.path,
            pre,
            post,
        )
        return "rollback_failed"
    notify(
        "generator.rolled_back",
        f"auto-rolled-back adopted generator after a post-adoption regression "
        f"(generation_failed {pre}->{post} over the probation window)",
        path=marker.path,
        restored=restored,
        pre=pre,
        post=post,
    )
    _log.warning(
        "auto-rolled-back generator %s -> %s after regression (gen_failed %d->%d)",
        marker.path,
        restored if restored is not None else "baseline",
        pre,
        post,
    )
    return "rolled_back"
