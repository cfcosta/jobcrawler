"""Tests for the post-adoption generator regression watcher (§4.11 P4c).

"Full auto, gated by proof" auto-adopts a generator the moment it clears the held-out
margin; this watcher is the paired safety net — after a probation window it compares
how many sites newly entered ``generation_failed`` in the window after adoption against
the equal window before it, and rolls the pointer back (the same undo
``rollback-generator`` performs) when the increase clears the regression delta. These
tests pin the pure delta rule, the probation gate, the confirmed / rolled-back /
rollback-failed verdicts, the equal-window fairness, and the disabled path.

Each test points :func:`config.generator_dir` at a fresh ``tmp_path`` (so the adoption
marker + pointer live in an isolated dir) and captures notifications instead of POSTing.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from jobcrawler import adopt_watch, config, generator_pointer
from jobcrawler.state import StateStore

T_ADOPT = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)
WINDOW = timedelta(hours=48)


@pytest.fixture(autouse=True)
def _isolated_generator_dir(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(config, "generator_dir", lambda: str(tmp_path))


@pytest.fixture
def clock() -> list[datetime]:
    """A one-element mutable holder for the store's clock, set before each seed."""
    return [T_ADOPT]


@pytest.fixture
def store(clock: list[datetime]) -> Iterator[StateStore]:
    s = StateStore(":memory:", clock=lambda: clock[0])
    yield s
    s.close()


@pytest.fixture
def events(monkeypatch: MonkeyPatch) -> list[tuple[str, dict[str, object]]]:
    captured: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        adopt_watch,
        "notify",
        lambda event, message, **fields: captured.append((event, fields)),
    )
    return captured


def _seed_failures(
    store: StateStore, clock: list[datetime], at: datetime, n: int, *, prefix: str
) -> None:
    """Log ``n`` distinct sites entering generation_failed at wall-clock ``at``.

    Each site is unique so each is a real None→generation_failed transition (the store
    logs a row only on a state *change*), landing in the health_log at time ``at``.
    """
    clock[0] = at
    for i in range(n):
        store.set_health(f"{prefix}{i}", "generation_failed")


# -- the pure delta rule -----------------------------------------------------


def test_regressed_is_a_delta_over_the_baseline() -> None:
    assert adopt_watch.regressed(0, 2, 2) is True  # 2 new failures, none before
    assert adopt_watch.regressed(0, 1, 2) is False  # one extra is within noise
    assert adopt_watch.regressed(3, 5, 2) is True  # +2 over an already-flaky fleet
    assert (
        adopt_watch.regressed(3, 4, 2) is False
    )  # +1 over baseline → not a regression
    assert adopt_watch.regressed(5, 2, 2) is False  # post < pre: the swap *helped*


# -- the probation gate ------------------------------------------------------


def test_no_marker_is_a_noop(
    store: StateStore, events: list[tuple[str, dict[str, object]]]
) -> None:
    assert adopt_watch.check_adoption(store, T_ADOPT, probation=WINDOW) is None
    assert events == []


def test_within_probation_is_a_noop(
    store: StateStore, events: list[tuple[str, dict[str, object]]]
) -> None:
    generator_pointer.record_adoption("generators/generator-1.json", at=T_ADOPT)
    verdict = adopt_watch.check_adoption(
        store, T_ADOPT + timedelta(hours=1), probation=WINDOW
    )
    assert verdict is None
    assert events == []
    assert generator_pointer.read_adoption() is not None  # marker untouched


def test_disabled_watcher_is_a_noop(
    store: StateStore,
    monkeypatch: MonkeyPatch,
    events: list[tuple[str, dict[str, object]]],
) -> None:
    # JOBCRAWLER_ADOPT_PROBATION=off ⇒ config returns None; even a ripe, regressing
    # marker is left strictly alone (the margin gate is the only protection then).
    monkeypatch.setattr(config, "adopt_probation", lambda: None)
    generator_pointer.set_active("generators/generator-1.json")
    generator_pointer.record_adoption("generators/generator-1.json", at=T_ADOPT)
    _seed_failures(store, [T_ADOPT], T_ADOPT + timedelta(hours=24), 5, prefix="post")

    assert adopt_watch.check_adoption(store, T_ADOPT + WINDOW * 9) is None
    assert events == []
    assert generator_pointer.read_adoption() is not None  # marker untouched


# -- verdicts ----------------------------------------------------------------


def test_clean_adoption_is_confirmed(
    store: StateStore,
    clock: list[datetime],
    events: list[tuple[str, dict[str, object]]],
) -> None:
    generator_pointer.set_active("generators/generator-1.json")
    generator_pointer.record_adoption("generators/generator-1.json", at=T_ADOPT)
    # No new failures after adoption → nothing to roll back.
    verdict = adopt_watch.check_adoption(
        store, T_ADOPT + WINDOW, probation=WINDOW, regression_delta=2
    )
    assert verdict == "confirmed"
    assert events == [
        (
            "generator.confirmed",
            {"path": "generators/generator-1.json", "pre": 0, "post": 0},
        )
    ]
    assert generator_pointer.read_adoption() is None  # marker consumed
    assert generator_pointer.active_generator_path() == "generators/generator-1.json"


def test_regression_rolls_the_pointer_back(
    store: StateStore,
    clock: list[datetime],
    events: list[tuple[str, dict[str, object]]],
) -> None:
    generator_pointer.set_active("generators/generator-1.json")  # baseline → g1
    generator_pointer.record_adoption("generators/generator-1.json", at=T_ADOPT)
    # Two sites newly fail under the adopted generator, none before it → +2 ≥ delta.
    _seed_failures(store, clock, T_ADOPT + timedelta(hours=24), 2, prefix="post")

    verdict = adopt_watch.check_adoption(
        store, T_ADOPT + WINDOW, probation=WINDOW, regression_delta=2
    )

    assert verdict == "rolled_back"
    assert events == [
        (
            "generator.rolled_back",
            {
                "path": "generators/generator-1.json",
                "restored": None,
                "pre": 0,
                "post": 2,
            },
        )
    ]
    assert generator_pointer.active_generator_path() is None  # rolled back to baseline
    assert generator_pointer.read_adoption() is None  # marker consumed


def test_ambient_flakiness_does_not_trip_a_rollback(
    store: StateStore,
    clock: list[datetime],
    events: list[tuple[str, dict[str, object]]],
) -> None:
    # The fleet was already failing before the swap (pre=3); one extra after (post=4) is
    # below the delta, so the baseline comparison correctly confirms rather than rolls back.
    generator_pointer.set_active("generators/generator-1.json")
    generator_pointer.record_adoption("generators/generator-1.json", at=T_ADOPT)
    _seed_failures(store, clock, T_ADOPT - timedelta(hours=24), 3, prefix="pre")
    _seed_failures(store, clock, T_ADOPT + timedelta(hours=24), 4, prefix="post")

    verdict = adopt_watch.check_adoption(
        store, T_ADOPT + WINDOW, probation=WINDOW, regression_delta=2
    )

    assert verdict == "confirmed"
    assert events[0][0] == "generator.confirmed"
    assert events[0][1] == {"path": "generators/generator-1.json", "pre": 3, "post": 4}
    assert generator_pointer.active_generator_path() == "generators/generator-1.json"


def test_rollback_failed_when_there_is_nothing_to_undo(
    store: StateStore,
    clock: list[datetime],
    events: list[tuple[str, dict[str, object]]],
) -> None:
    # A regression is detected but the pointer history is empty (e.g. an operator
    # manually rolled back mid-probation): surface it as rollback_failed, still consume
    # the marker, and never leave a half-applied state.
    generator_pointer.record_adoption("generators/generator-1.json", at=T_ADOPT)
    _seed_failures(store, clock, T_ADOPT + timedelta(hours=24), 3, prefix="post")

    verdict = adopt_watch.check_adoption(
        store, T_ADOPT + WINDOW, probation=WINDOW, regression_delta=2
    )

    assert verdict == "rollback_failed"
    assert events[0][0] == "generator.rollback_failed"
    assert generator_pointer.read_adoption() is None  # marker consumed even on failure


def test_post_window_is_fixed_length_not_up_to_now(
    store: StateStore,
    clock: list[datetime],
    events: list[tuple[str, dict[str, object]]],
) -> None:
    # Equal-window fairness: a failure *after* the probation window must not count, even
    # if the watcher tick fires long after the window closed. One failure inside the
    # window (post=1 < delta) confirms; were the post-window "up to now" it'd be 2 and
    # would wrongly roll back.
    generator_pointer.set_active("generators/generator-1.json")
    generator_pointer.record_adoption("generators/generator-1.json", at=T_ADOPT)
    _seed_failures(store, clock, T_ADOPT + timedelta(hours=24), 1, prefix="in")
    _seed_failures(
        store, clock, T_ADOPT + WINDOW + timedelta(hours=24), 1, prefix="out"
    )

    verdict = adopt_watch.check_adoption(
        store, T_ADOPT + WINDOW * 3, probation=WINDOW, regression_delta=2
    )

    assert verdict == "confirmed"  # only the in-window failure counted (post=1)
    assert events[0][1]["post"] == 1
    assert generator_pointer.active_generator_path() == "generators/generator-1.json"
