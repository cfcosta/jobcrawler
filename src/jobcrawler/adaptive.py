"""Adaptive crawl cadence — the §4.9 hit-rate controller (P1).

A static schedule either over-polls a quiet board (wasted requests, rude) or
under-polls a busy one (missed / stale postings). This module computes the *next*
crawl interval from recent outcomes so cadence tracks each source's real posting
rate, bounded by a politeness floor and a staleness ceiling.

The control signal is deliberately a boolean per crawl — "did this crawl find at
least one new open position" (``new_items > 0``) — not the raw new-position count,
which the ingest path can miscount; a hit/miss is robust to that. Over a recent
window the **hit rate** (fraction of crawls that hit) says whether we check too
often or too rarely relative to how fast the board posts.

**Target.** To check a source ``target_n`` times per posting on average, a steady
``target_n``× over-check makes ~1 crawl in ``target_n`` hit — so the desired hit
rate is ``1 / target_n`` (``target_n = 2`` ⇒ 0.5; a board posting twice a day
settles near ~3-4 checks/day).

**Control law (AIMD-style, geometric).** Bang-bang with a hold band:

  * hit rate **above** target (busier than we check) → *shrink* the interval
    (check more often), multiplicatively and fast (``× down``, default ×0.5) so a
    board that starts posting heavily is caught within a couple of cycles — a fresh
    opening must not be missed;
  * hit rate **below** target (quieter than we check) → *grow* the interval (back
    off), multiplicatively but gentler (``× up``, default ×1.5) so a lull does not
    immediately blind us to a resumption;
  * within the deadband around target → hold (prevents dithering).

The step is multiplicative, not additive, on purpose: the floor→ceiling range
spans ~100× (minutes to a day), and only geometric steps traverse it in a handful
of observations — additive-by-floor steps would take a quiet daily source weeks to
back off. The asymmetry (fast shrink, gentle grow) biases toward responsiveness,
which is the right error to make for a job-board crawler. The result is always clamped
to ``[floor_secs, ceiling_secs]``; a cold site with too few observations holds its
current interval (only re-clamped).

Pure and side-effect-free: persistence (the per-site learned interval) and wiring
(reading run history, logging changes) live in the state store and scheduler.
"""

from __future__ import annotations

from collections.abc import Sequence


def _clamp(value: float, floor: float, ceiling: float) -> float:
    return max(floor, min(ceiling, value))


def next_interval(
    current_secs: float,
    hits: Sequence[bool],
    *,
    floor_secs: float,
    ceiling_secs: float,
    target_hit_rate: float,
    window: int = 8,
    down: float = 0.5,
    up: float = 1.5,
    deadband: float = 0.1,
    min_observations: int = 3,
) -> float:
    """Next crawl interval (seconds) given recent hit/miss outcomes.

    ``hits`` is newest-first (matching ``StateStore.runs_for``); the most recent
    ``window`` observations are used. ``target_hit_rate`` is ``1 / target_n``.
    The return is clamped to ``[floor_secs, ceiling_secs]``. With fewer than
    ``min_observations`` samples the current interval is held (only re-clamped), so
    a freshly-seeded site does not swing on a single crawl.
    """
    recent = list(hits[:window])
    if len(recent) < min_observations:
        return _clamp(current_secs, floor_secs, ceiling_secs)
    hit_rate = sum(1 for hit in recent if hit) / len(recent)
    if hit_rate > target_hit_rate + deadband:
        nxt = current_secs * down  # busy → check more often, fast
    elif hit_rate < target_hit_rate - deadband:
        nxt = current_secs * up  # quiet → back off, gently
    else:
        nxt = current_secs  # within the hold band
    return _clamp(nxt, floor_secs, ceiling_secs)
