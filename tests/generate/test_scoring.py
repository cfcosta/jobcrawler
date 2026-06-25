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
    FORCED_EXTRACTION_PENALTY,
    CandidateResult,
    QualityAxes,
    build_feedback,
    compute_axes,
    graded_score,
    items_axis,
    score_candidate,
    score_from_axes,
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


# -- compute_axes: CandidateResult -> bounded axes ---------------------------


def _sample(url: str = "https://example.com/a") -> dict[str, str]:
    return {"url": url, "title": "A headline", "body": "x" * 600}


def _result(item_count: int, samples: list[dict[str, str]]) -> CandidateResult:
    return CandidateResult(item_count=item_count, samples=samples, pages_fetched=1)


_HOSTS = {"example.com"}


def _axes(result: CandidateResult, discovery_method: str = "feed") -> QualityAxes:
    return compute_axes(
        result,
        target_n=30,
        allowed_hosts=_HOSTS,
        min_body_chars=400,
        discovery_method=discovery_method,
    )


def test_axes_perfect_feed_result_is_all_ones() -> None:
    axes = _axes(_result(30, [_sample()]))
    assert (axes.items_ratio, axes.field_presence, axes.body_quality) == (1.0, 1.0, 1.0)
    assert (axes.url_validity, axes.discovery_quality) == (1.0, 1.0)


def test_axes_items_ratio_clamps_above_target() -> None:
    assert _axes(_result(100, [_sample()])).items_ratio == 1.0


def test_axes_off_domain_sample_zeros_url_validity() -> None:
    axes = _axes(_result(30, [_sample("https://evil.com/a")]))
    assert axes.url_validity == 0.0


def test_axes_short_body_zeros_body_quality() -> None:
    s = _sample()
    s["body"] = "too short"
    assert _axes(_result(30, [s])).body_quality == 0.0


def test_axes_discovery_quality_tracks_method() -> None:
    assert (
        _axes(_result(30, [_sample()]), "scrape").discovery_quality
        == DISCOVERY_QUALITY["scrape"]
    )


def test_axes_empty_samples_zero_the_veto_axes() -> None:
    axes = _axes(_result(30, []))
    assert axes.field_presence == 0.0
    assert axes.url_validity == 0.0


# -- score_candidate: scalar + forced-extraction tie-breaker -----------------


def test_score_perfect_feed_is_one() -> None:
    assert score_candidate(
        _result(30, [_sample()]),
        target_n=30,
        allowed_hosts=_HOSTS,
        min_body_chars=400,
        discovery_method="feed",
    ) == pytest.approx(1.0)


def test_forced_extraction_subtracts_a_small_tiebreaker() -> None:
    clean = score_candidate(
        _result(30, [_sample()]),
        target_n=30,
        allowed_hosts=_HOSTS,
        min_body_chars=400,
        discovery_method="feed",
    )
    forced = score_candidate(
        _result(30, [_sample()]),
        target_n=30,
        allowed_hosts=_HOSTS,
        min_body_chars=400,
        discovery_method="feed",
        forced=True,
    )
    assert forced == pytest.approx(clean - FORCED_EXTRACTION_PENALTY)


def test_off_domain_is_vetoed_to_zero_even_with_many_items() -> None:
    score = score_candidate(
        _result(100, [_sample("https://evil.com/a")]),
        target_n=30,
        allowed_hosts=_HOSTS,
        min_body_chars=400,
        discovery_method="feed",
    )
    assert score == 0.0


def test_forced_penalty_never_goes_below_zero() -> None:
    score = score_candidate(
        _result(0, []),
        target_n=30,
        allowed_hosts=_HOSTS,
        min_body_chars=400,
        discovery_method="scrape",
        forced=True,
    )
    assert score == 0.0


# -- build_feedback: concrete, severity-capped, across-run-aware --------------


def test_feedback_caps_to_most_blocking() -> None:
    fb = build_feedback(
        gate_failures=["AST: import os banned", "0 items", "short body", "off-domain"],
        max_foreground=2,
    )
    assert "import os banned" in fb
    assert "off-domain" not in fb  # capped out as lower-severity


def test_feedback_appends_stability_note() -> None:
    fb = build_feedback(
        gate_failures=["0 items"], stability_note="unstable across runs"
    )
    assert "unstable across runs" in fb


def test_feedback_empty_when_nothing_failed() -> None:
    assert build_feedback(gate_failures=[]) == ""


# -- build_feedback axis breakdown: signal for gate-passing-but-mediocre ------
#
# The bug this fixes: GEPA's reflection LM never sees numeric scores — only the
# feedback string. A candidate that *passes* the gates but scores below 1.0 used
# to emit feedback="", handing the reflector nothing to mutate. The sub-maximal
# axis breakdown is now that signal.


def test_feedback_surfaces_submaximal_axes_when_gates_pass() -> None:
    axes = _perfect().model_copy(update={"items_ratio": 0.3, "discovery_quality": 0.4})
    fb = build_feedback(gate_failures=[], axes=axes)
    assert fb != ""  # was "" before the fix — no signal for GEPA
    assert "item coverage" in fb  # the weakest axis (0.3) leads


def test_feedback_perfect_axes_emit_nothing() -> None:
    # A 1.0 candidate still yields "" — harmless: GEPA skips it (skip_perfect_score).
    assert build_feedback(gate_failures=[], axes=_perfect()) == ""


def test_feedback_axes_are_worst_first_and_capped() -> None:
    axes = QualityAxes(
        items_ratio=0.3,
        field_presence=0.5,
        body_quality=0.9,
        url_validity=1.0,
        discovery_quality=0.4,
    )
    fb = build_feedback(gate_failures=[], axes=axes, max_foreground=2)
    assert len(fb.split("; ")) == 2  # capped
    assert fb.startswith("item coverage")  # worst axis (0.3) first
    assert "discovery method" in fb  # second-worst (0.4)
    assert "field completeness" not in fb and "body text" not in fb  # squeezed out


def test_feedback_gate_failures_take_precedence_over_axes() -> None:
    # Gate failures fill foreground first; axes only fill the remaining slot(s).
    axes = _perfect().model_copy(update={"discovery_quality": 0.4})
    fb = build_feedback(
        gate_failures=["AST: import os banned"], axes=axes, max_foreground=2
    )
    parts = fb.split("; ")
    assert parts[0] == "AST: import os banned"
    assert "discovery method" in parts[1]  # axis fills the one remaining slot


def test_feedback_axes_carry_no_scorer_threshold() -> None:
    # §8 hidden-scorer: the guidance names *what* to improve, never the bar — no
    # comparison operator, no "need"/"require". The candidate's own axis value is
    # allowed (mirrors gate failures keeping the candidate's own counts).
    axes = _perfect().model_copy(update={"items_ratio": 0.5})
    fb = build_feedback(gate_failures=[], axes=axes)
    assert "item coverage" in fb
    assert ">=" not in fb and "<=" not in fb and ">" not in fb and "<" not in fb
    assert "need" not in fb and "require" not in fb


# -- build_feedback forced-extraction remedy: the last empty-feedback path -----
#
# A forced extraction costs FORCED_EXTRACTION_PENALTY but touches no axis, so an
# otherwise-perfect candidate scores 0.95 — sub-perfect (GEPA does NOT skip it) yet
# its axes are all maximal, so the axis breakdown is empty. Without a forced remedy
# the reflection LM would again get feedback="" for a candidate it must mutate.


def test_feedback_forced_extraction_fills_empty_when_axes_are_maximal() -> None:
    fb = build_feedback(gate_failures=[], axes=_perfect(), forced=True)
    assert fb != ""  # the empty-feedback pitfall, closed on its last reachable path
    assert "force-completed" in fb


def test_feedback_forced_remedy_yields_to_a_weaker_axis() -> None:
    # A real axis weakness is more actionable than the 0.05 forced penalty, so it
    # leads; the forced remedy fills the remaining slot.
    axes = _perfect().model_copy(update={"items_ratio": 0.3})
    parts = build_feedback(gate_failures=[], axes=axes, forced=True).split("; ")
    assert parts[0].startswith("item coverage")
    assert "force-completed" in parts[1]


def test_feedback_forced_remedy_squeezed_out_when_foreground_is_full() -> None:
    # Two gate failures already fill the foreground → the forced remedy is dropped
    # (the candidate has bigger problems than a forced final extraction).
    fb = build_feedback(
        gate_failures=["AST: import os banned", "items missing a title"],
        forced=True,
        max_foreground=2,
    )
    assert "force-completed" not in fb


def test_feedback_forced_remedy_carries_no_scorer_threshold() -> None:
    # §8 hidden-scorer: the forced remedy names what to do, never a bar.
    fb = build_feedback(gate_failures=[], axes=_perfect(), forced=True)
    assert ">=" not in fb and "<=" not in fb and ">" not in fb and "<" not in fb
    assert "need" not in fb and "require" not in fb


def test_score_from_axes_is_graded_minus_forced_penalty() -> None:
    axes = _perfect().model_copy(update={"discovery_quality": 0.4})
    assert score_from_axes(axes) == pytest.approx(graded_score(axes))
    assert score_from_axes(axes, forced=True) == pytest.approx(
        graded_score(axes) - FORCED_EXTRACTION_PENALTY
    )


def test_score_from_axes_never_below_zero() -> None:
    empty = QualityAxes(
        items_ratio=0.0,
        field_presence=0.0,
        body_quality=0.0,
        url_validity=0.0,
        discovery_quality=0.0,
    )
    assert score_from_axes(empty, forced=True) == 0.0


def test_feedback_scrubs_scorer_thresholds() -> None:
    # §8 hidden-scorer: the candidate's own count survives, the threshold does not.
    fb = build_feedback(gate_failures=["yielded 2 items, need >= 5"])
    assert "items" in fb
    assert "need" not in fb and ">=" not in fb and "5" not in fb


def test_feedback_scrubs_stability_threshold() -> None:
    fb = build_feedback(
        gate_failures=["0 items"], stability_note="unstable: jaccard >= 0.6"
    )
    assert "unstable" in fb
    assert "0.6" not in fb and ">=" not in fb
