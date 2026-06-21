"""Tests for the offline optimization decision logic + GEPA construction (§4.11)."""

from __future__ import annotations

from typing import cast

import dspy
from dspy.utils import DummyLM

from bizup_autocrawler.generate.evaluate import evaluate_generator
from bizup_autocrawler.generate.optimize import (
    build_gepa,
    has_optimization_signal,
    select_optimizer,
    should_promote,
)


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
