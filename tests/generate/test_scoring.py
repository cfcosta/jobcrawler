"""Tests for the scoring core — the graded multi-axis scalar (§6 / §4.11).

Shape-by-necessity, not a flat product: a min/AND veto (on-domain-URL
validity, field presence) gates a bounded weighted sum of the graded axes
(items-yielded ratio, body-length rate, discovery-method quality). The
load-bearing properties: item_count can never buy back an off-domain or
empty-field crawler (veto → 0), yet a single weak-but-acceptable graded axis
does not crater an otherwise-good one (additive, not multiplicative).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bizup_autocrawler.generate.scoring import (
    DEFAULT_WEIGHTS,
    DISCOVERY_QUALITY,
    QualityAxes,
    graded_score,
    items_axis,
)


def _perfect() -> QualityAxes:
    return QualityAxes(
        items_ratio=1.0,
        field_presence=1.0,
        body_quality=1.0,
        url_validity=1.0,
        discovery_quality=1.0,
    )


def test_items_axis_clamps_at_one() -> None:
    assert items_axis(5, 10) == 0.5
    assert items_axis(10, 10) == 1.0
    assert items_axis(15, 10) == 1.0  # exceeding the target buys nothing
    assert items_axis(0, 10) == 0.0


def test_items_axis_rejects_nonpositive_target() -> None:
    with pytest.raises(ValueError):
        items_axis(3, 0)


def test_perfect_axes_score_one() -> None:
    assert graded_score(_perfect()) == pytest.approx(1.0)


def test_off_domain_url_vetoes_to_zero() -> None:
    axes = _perfect().model_copy(update={"url_validity": 0.0})
    assert graded_score(axes) == 0.0


def test_missing_fields_veto_to_zero() -> None:
    axes = _perfect().model_copy(update={"field_presence": 0.0})
    assert graded_score(axes) == 0.0


def test_weak_graded_axis_does_not_crater() -> None:
    # a scrape crawler (discovery 0.4) that is otherwise perfect stays high —
    # a flat 5-way product would drop it to 0.4.
    axes = _perfect().model_copy(
        update={"discovery_quality": DISCOVERY_QUALITY["scrape"]}
    )
    score = graded_score(axes)
    assert 0.85 < score < 1.0


def test_score_is_monotonic_in_items_ratio() -> None:
    lo = graded_score(_perfect().model_copy(update={"items_ratio": 0.3}))
    hi = graded_score(_perfect().model_copy(update={"items_ratio": 0.9}))
    assert hi > lo


def test_score_stays_within_unit_interval() -> None:
    axes = QualityAxes(
        items_ratio=0.6,
        field_presence=0.9,
        body_quality=0.4,
        url_validity=0.8,
        discovery_quality=0.7,
    )
    assert 0.0 <= graded_score(axes) <= 1.0


@pytest.mark.parametrize("bad", [1.5, -0.1])
def test_axes_must_be_bounded_to_unit_interval(bad: float) -> None:
    with pytest.raises(ValidationError):
        QualityAxes(
            items_ratio=bad,
            field_presence=1.0,
            body_quality=1.0,
            url_validity=1.0,
            discovery_quality=1.0,
        )


def test_default_weights_sum_to_one() -> None:
    total = DEFAULT_WEIGHTS.items + DEFAULT_WEIGHTS.body + DEFAULT_WEIGHTS.discovery
    assert total == pytest.approx(1.0)
