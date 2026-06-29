"""Tests for the §4.9 adaptive hit-rate controller (P1).

``next_interval`` is a pure function: given the current interval and a recent
window of hit/miss outcomes, it returns the next interval clamped to a politeness
floor and a staleness ceiling. These tests pin the control law (direction,
clamps, hysteresis, cold-start) so the scheduler integration can rely on it.
"""

from __future__ import annotations

from bizup_autocrawler.adaptive import next_interval

FLOOR = 900.0  # 15m politeness floor
CEILING = 86400.0  # 1d staleness ceiling
TARGET = 0.5  # target_n = 2 → check ~twice per post


def test_busy_source_shrinks_interval() -> None:
    # Every recent crawl found new items → we are under-checking → shrink (fast).
    nxt = next_interval(
        3600.0,
        [True] * 8,
        floor_secs=FLOOR,
        ceiling_secs=CEILING,
        target_hit_rate=TARGET,
    )
    assert nxt == 1800.0  # 3600 × down(0.5)


def test_quiet_source_grows_interval() -> None:
    # Every recent crawl found nothing → we are over-checking → back off (gentle).
    nxt = next_interval(
        3600.0,
        [False] * 8,
        floor_secs=FLOOR,
        ceiling_secs=CEILING,
        target_hit_rate=TARGET,
    )
    assert nxt == 5400.0  # 3600 × up(1.5)


def test_at_target_holds_interval() -> None:
    # Hit rate inside the deadband around target → no change.
    hits = [True, False] * 4  # 4/8 = 0.5 == target
    nxt = next_interval(
        3600.0, hits, floor_secs=FLOOR, ceiling_secs=CEILING, target_hit_rate=TARGET
    )
    assert nxt == 3600.0


def test_deadband_absorbs_small_deviations() -> None:
    # 5/8 = 0.625 is only just above target; with deadband 0.1 the band is
    # [0.4, 0.6], so 0.625 *does* trip a shrink, but 0.5±small holds. Verify a
    # value strictly inside the band holds.
    hits = [True, True, True, False, False, False, False, False]  # 3/8 = 0.375
    nxt = next_interval(
        3600.0,
        hits,
        floor_secs=FLOOR,
        ceiling_secs=CEILING,
        target_hit_rate=TARGET,
        deadband=0.2,  # band [0.3, 0.7] → 0.375 holds
    )
    assert nxt == 3600.0


def test_shrink_is_clamped_to_floor() -> None:
    nxt = next_interval(
        1000.0,
        [True] * 8,
        floor_secs=FLOOR,
        ceiling_secs=CEILING,
        target_hit_rate=TARGET,
    )
    assert nxt == FLOOR  # 1000 × 0.5 = 500 < floor → clamped


def test_grow_is_clamped_to_ceiling() -> None:
    nxt = next_interval(
        80000.0,
        [False] * 8,
        floor_secs=FLOOR,
        ceiling_secs=CEILING,
        target_hit_rate=TARGET,
    )
    assert nxt == CEILING  # 80000 × 1.5 = 120000 > ceiling → clamped


def test_cold_start_holds_current_when_too_few_observations() -> None:
    # Fewer than min_observations samples → don't swing on noise; hold (re-clamped).
    nxt = next_interval(
        3600.0,
        [True, True],  # only 2 observations, min is 3
        floor_secs=FLOOR,
        ceiling_secs=CEILING,
        target_hit_rate=TARGET,
    )
    assert nxt == 3600.0


def test_cold_start_still_clamps_an_out_of_range_current() -> None:
    # A tightened ceiling must take effect even before enough observations exist.
    nxt = next_interval(
        100000.0,
        [],
        floor_secs=FLOOR,
        ceiling_secs=CEILING,
        target_hit_rate=TARGET,
    )
    assert nxt == CEILING


def test_only_the_window_is_considered() -> None:
    # window=4: only the 4 newest (all misses) count, so it grows despite older hits.
    hits = [False, False, False, False, True, True, True, True]  # newest-first
    nxt = next_interval(
        3600.0,
        hits,
        floor_secs=FLOOR,
        ceiling_secs=CEILING,
        target_hit_rate=TARGET,
        window=4,
    )
    assert nxt == 5400.0  # 0/4 hit rate → grow


def test_custom_factors_are_honored() -> None:
    nxt = next_interval(
        4000.0,
        [True] * 8,
        floor_secs=FLOOR,
        ceiling_secs=CEILING,
        target_hit_rate=TARGET,
        down=0.25,
    )
    assert nxt == 1000.0  # 4000 × 0.25


def test_repeated_hits_converge_to_floor() -> None:
    interval = 3600.0
    for _ in range(20):
        interval = next_interval(
            interval,
            [True] * 8,
            floor_secs=FLOOR,
            ceiling_secs=CEILING,
            target_hit_rate=TARGET,
        )
    assert interval == FLOOR  # geometric shrink pins at the politeness floor


def test_repeated_misses_converge_to_ceiling() -> None:
    interval = 1800.0
    for _ in range(40):
        interval = next_interval(
            interval,
            [False] * 8,
            floor_secs=FLOOR,
            ceiling_secs=CEILING,
            target_hit_rate=TARGET,
        )
    assert interval == CEILING  # geometric growth pins at the staleness ceiling


def test_oscillating_source_stays_within_bounds() -> None:
    # A source whose hit rate hovers around target keeps the interval bounded — it
    # never escapes [floor, ceiling] regardless of the dithering between steps.
    interval = 3600.0
    patterns = [[True] * 8, [False] * 8, [True, False] * 4, [True] * 6 + [False] * 2]
    for i in range(60):
        interval = next_interval(
            interval,
            patterns[i % len(patterns)],
            floor_secs=FLOOR,
            ceiling_secs=CEILING,
            target_hit_rate=TARGET,
        )
        assert FLOOR <= interval <= CEILING


def test_higher_target_n_tolerates_lower_hit_rate() -> None:
    # target_n = 4 → target hit rate 0.25. A 0.25 hit rate should hold, where a
    # target_n = 2 (target 0.5) would grow the interval.
    hits = [True, False, False, False] * 2  # 2/8 = 0.25
    held = next_interval(
        3600.0,
        hits,
        floor_secs=FLOOR,
        ceiling_secs=CEILING,
        target_hit_rate=0.25,  # target_n = 4
    )
    grown = next_interval(
        3600.0,
        hits,
        floor_secs=FLOOR,
        ceiling_secs=CEILING,
        target_hit_rate=0.5,  # target_n = 2
    )
    assert held == 3600.0
    assert grown == 5400.0
