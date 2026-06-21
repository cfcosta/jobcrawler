"""Tests for the offline optimization decision logic + GEPA construction (§4.11)."""

from __future__ import annotations

from typing import cast

import dspy
import pytest
from dspy.utils import DummyLM

from bizup_autocrawler.generate.evaluate import evaluate_generator
from bizup_autocrawler.generate.optimize import (
    SATURATION_REMEDY,
    build_gepa,
    has_optimization_signal,
    has_per_instance_spread,
    promotion_margin,
    run_optimization,
    select_optimizer,
    should_promote,
)


def _ex(site: str) -> dspy.Example:
    return dspy.Example(
        site_url=f"https://{site}.com",
        corpus={},
        render="auto",
        hints="",
        discovery_method="feed",
        discovery_url="",
        crawler_source="x",
    ).with_inputs("site_url", "corpus", "render", "hints")


def _holdout(n: int) -> list[dspy.Example]:
    return [_ex(f"h{i}") for i in range(n)]


def test_optimizer_ladder_escalates_by_size() -> None:
    assert select_optimizer(0) == "none"
    assert select_optimizer(10) == "bootstrap_fewshot"
    assert select_optimizer(50) == "gepa"
    assert select_optimizer(500) == "mipro"


def test_anti_saturation_needs_submaximal_scores() -> None:
    assert not has_optimization_signal([1.0, 1.0, 1.0])  # all maximal → GEPA no-ops
    assert has_optimization_signal([1.0, 0.5, 1.0, 0.3])  # 50% sub-maximal
    assert not has_optimization_signal([])
    # 1 of 3 sub-maximal = 33% < the 50% floor.
    assert not has_optimization_signal([1.0, 1.0, 0.9], min_submaximal_fraction=0.5)


def test_promotion_requires_beating_incumbent_by_margin() -> None:
    assert should_promote(80.0, 85.0)
    assert not should_promote(80.0, 80.0)  # a tie does not promote
    assert not should_promote(80.0, 82.0, margin=5.0)
    assert should_promote(80.0, 86.0, margin=5.0)


def test_build_gepa_constructs_without_compiling() -> None:
    def metric(
        gold: object,
        pred: object,
        trace: object = None,
        pred_name: str | None = None,
        pred_trace: object = None,
    ) -> dspy.Prediction:
        return dspy.Prediction(score=1.0, feedback="")

    reflection_lm = cast(dspy.LM, DummyLM([{"x": "y"}]))  # DummyLM is-a LM at runtime
    gepa = build_gepa(metric, reflection_lm=reflection_lm)
    assert type(gepa).__name__ == "GEPA"  # ty models GEPA as a value, not a class
    assert gepa.use_merge is False  # §4.11: Merge OFF for v1


def test_evaluate_generator_returns_mean_score() -> None:
    class _Prog(dspy.Module):
        def forward(self, **kwargs: object) -> dspy.Prediction:
            return dspy.Prediction(answer="ok")

    def metric(
        example: object, pred: object, *args: object, **kw: object
    ) -> dspy.Prediction:
        return dspy.Prediction(score=1.0, feedback="")

    devset = [dspy.Example(question="q").with_inputs("question")]
    with dspy.context(lm=DummyLM([{"answer": "ok"}])):
        score = evaluate_generator(_Prog(), devset=devset, metric=metric)
    assert score == 100.0  # dspy.Evaluate scores as a 0–100 percentage


# -- offline optimization orchestration --------------------------------------


def test_promotion_margin_is_the_noise_floor() -> None:
    assert promotion_margin(10) == 10.0
    assert promotion_margin(0) == 100.0  # an empty held-out can never promote


def test_per_instance_spread_needs_a_discriminating_site() -> None:
    assert has_per_instance_spread({"a": [1.0, 0.5]})
    assert not has_per_instance_spread({"a": [1.0, 1.0], "b": [0.5, 0.5]})


def _winning_eval(prog: object, *, devset: object, metric: object) -> float:
    return 95.0 if prog == "OPT" else 80.0


def test_run_optimization_promotes_a_clear_winner() -> None:
    out = run_optimization(
        baseline="BASE",
        optimized_factory=lambda: "OPT",
        trainset=[_ex("a")],
        valset=[_ex("b")],
        holdout=_holdout(10),  # margin 10; +15 clears it
        metric=lambda *a: 0,
        minibatch_scores=[1.0, 0.5, 1.0],
        eval_fn=_winning_eval,
    )
    assert out["promoted"] is True
    assert out["optimized_program"] == "OPT"


def test_run_optimization_short_circuits_on_saturation() -> None:
    def no_compile() -> object:
        raise AssertionError("must not compile when saturated")

    out = run_optimization(
        baseline="BASE",
        optimized_factory=no_compile,
        trainset=[_ex("a")],
        valset=[_ex("b")],
        holdout=[_ex("c")],
        metric=lambda *a: 0,
        minibatch_scores=[1.0, 1.0, 1.0],  # all maximal → no GEPA signal
        eval_fn=lambda *a, **k: 0.0,
    )
    assert out["promoted"] is False
    assert "saturat" in str(out["reason"])
    assert out["remedy"] == SATURATION_REMEDY


def test_run_optimization_rejects_a_pool_leak() -> None:
    with pytest.raises(ValueError):
        run_optimization(
            baseline="BASE",
            optimized_factory=lambda: "OPT",
            trainset=[_ex("a")],
            valset=[_ex("b")],
            holdout=[_ex("a")],  # same site as trainset = leak
            metric=lambda *a: 0,
            minibatch_scores=[0.5],
            eval_fn=lambda *a, **k: 0.0,
        )


def test_run_optimization_holds_when_gain_is_within_noise() -> None:
    def narrow_eval(prog: object, *, devset: object, metric: object) -> float:
        return 85.0 if prog == "OPT" else 80.0  # +5 < margin 10

    out = run_optimization(
        baseline="BASE",
        optimized_factory=lambda: "OPT",
        trainset=[_ex("a")],
        valset=[_ex("b")],
        holdout=_holdout(10),
        metric=lambda *a: 0,
        minibatch_scores=[1.0, 0.5],
        eval_fn=narrow_eval,
    )
    assert out["promoted"] is False
    assert out["optimized_program"] is None
