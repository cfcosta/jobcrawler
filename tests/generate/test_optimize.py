"""Tests for the offline optimization decision logic + GEPA construction (§4.11)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import dspy
import pytest
from dspy.utils import DummyLM

from jobcrawler.generate.evaluate import evaluate_generator
from jobcrawler.generate.optimize import (
    SATURATION_REMEDY,
    _budget_health_note,
    _pareto_health_note,
    build_gepa,
    gepa_compile,
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


def test_build_gepa_pins_reproducibility_knobs() -> None:
    # Pin the three knobs DSPy already defaults so the run is reproducible and
    # robust to a dspy bump (GEPA internals verified against 3.2.1 — test_dspy_pin).
    def metric(
        gold: object,
        pred: object,
        trace: object = None,
        pred_name: str | None = None,
        pred_trace: object = None,
    ) -> dspy.Prediction:
        return dspy.Prediction(score=1.0, feedback="")

    reflection_lm = cast(dspy.LM, DummyLM([{"x": "y"}]))
    gepa = build_gepa(metric, reflection_lm=reflection_lm)
    assert gepa.candidate_selection_strategy == "pareto"
    assert gepa.reflection_minibatch_size == 3
    assert gepa.seed == 0


class _FakeGepa:
    def compile(self, student: object, *, trainset: object, valset: object) -> str:
        return "COMPILED"


def test_gepa_compile_degrades_selection_for_tiny_valset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A <2-instance valset can't form a Pareto frontier → 'current_best'; the
    # reflection minibatch is clamped to the (tiny) trainset so compile can't blow up.
    captured: dict[str, object] = {}

    def fake_build_gepa(
        metric: object,
        *,
        reflection_lm: object,
        candidate_selection_strategy: str = "pareto",
        reflection_minibatch_size: int = 3,
        **_kw: object,
    ) -> _FakeGepa:
        captured["selection"] = candidate_selection_strategy
        captured["minibatch"] = reflection_minibatch_size
        return _FakeGepa()

    monkeypatch.setattr("jobcrawler.generate.optimize.build_gepa", fake_build_gepa)
    reflection_lm = cast(dspy.LM, DummyLM([{"x": "y"}]))
    out = gepa_compile(
        cast(dspy.Module, "BASE"),
        trainset=[_ex("a")],
        valset=[_ex("b")],  # single val instance
        reflection_lm=reflection_lm,
        metric=lambda *a: 0,
    )
    assert out == "COMPILED"
    assert captured["selection"] == "current_best"
    assert captured["minibatch"] == 1  # clamped to len(trainset)


def test_gepa_compile_uses_pareto_for_adequate_pools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_build_gepa(
        metric: object,
        *,
        reflection_lm: object,
        candidate_selection_strategy: str = "pareto",
        reflection_minibatch_size: int = 3,
        **_kw: object,
    ) -> _FakeGepa:
        captured["selection"] = candidate_selection_strategy
        captured["minibatch"] = reflection_minibatch_size
        return _FakeGepa()

    monkeypatch.setattr("jobcrawler.generate.optimize.build_gepa", fake_build_gepa)
    reflection_lm = cast(dspy.LM, DummyLM([{"x": "y"}]))
    gepa_compile(
        cast(dspy.Module, "BASE"),
        trainset=[_ex(f"t{i}") for i in range(5)],
        valset=[_ex("v1"), _ex("v2")],  # ≥2 → frontier is meaningful
        reflection_lm=reflection_lm,
        metric=lambda *a: 0,
    )
    assert captured["selection"] == "pareto"
    assert captured["minibatch"] == 3  # min(3, 5)


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


def test_evaluate_generator_averages_multiple_passes() -> None:
    # Multi-pass promotion rigor: average several passes so a marginal win on a
    # small held-out isn't promoted on the generator's sampling luck (§4.11).
    class _Prog(dspy.Module):
        def forward(self, **kwargs: object) -> dspy.Prediction:
            return dspy.Prediction(answer="ok")

    def metric(
        example: object, pred: object, *args: object, **kw: object
    ) -> dspy.Prediction:
        return dspy.Prediction(score=1.0, feedback="")

    devset = [dspy.Example(question="q").with_inputs("question")]
    with dspy.context(lm=DummyLM([{"answer": "ok"}])):
        score = evaluate_generator(_Prog(), devset=devset, metric=metric, passes=3)
    assert score == 100.0  # deterministic stub → mean of 3 identical passes


def test_evaluate_generator_rejects_nonpositive_passes() -> None:
    class _Prog(dspy.Module):
        def forward(self, **kwargs: object) -> dspy.Prediction:
            return dspy.Prediction(answer="ok")

    with pytest.raises(ValueError):
        evaluate_generator(_Prog(), devset=[], metric=lambda *a: 0, passes=0)


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


# -- Pareto-health diagnostic (post-compile over-optimization signal) ----------


def _detailed(val_subscores: list[list[float]]) -> SimpleNamespace:
    return SimpleNamespace(
        detailed_results=SimpleNamespace(val_subscores=val_subscores)
    )


def test_pareto_health_note_warns_when_no_candidate_spread() -> None:
    # Every candidate scored identically on every val instance → the frontier
    # collapsed and selection was effectively uniform.
    note = _pareto_health_note(_detailed([[1.0, 0.5], [1.0, 0.5]]))
    assert note is not None
    assert "discriminate" in note


def test_pareto_health_note_silent_when_an_instance_discriminates() -> None:
    # Instance 0 ranks the two candidates differently (1.0 vs 0.8) → healthy.
    assert _pareto_health_note(_detailed([[1.0, 0.5], [0.8, 0.5]])) is None


def test_pareto_health_note_silent_without_stats() -> None:
    assert _pareto_health_note("OPT") is None  # injected string program, no stats
    assert _pareto_health_note(SimpleNamespace(detailed_results=None)) is None
    assert _pareto_health_note(_detailed([[1.0, 0.5]])) is None  # <2 candidates


def test_run_optimization_flags_degenerate_pareto_health() -> None:
    degenerate = _detailed([[1.0, 0.5], [1.0, 0.5]])

    def eval_fn(prog: object, *, devset: object, metric: object) -> float:
        return 95.0 if prog is degenerate else 80.0

    out = run_optimization(
        baseline="BASE",
        optimized_factory=lambda: degenerate,
        trainset=[_ex("a")],
        valset=[_ex("b")],
        holdout=_holdout(10),
        metric=lambda *a: 0,
        minibatch_scores=[1.0, 0.5, 1.0],
        eval_fn=eval_fn,
    )
    assert out["promoted"] is True  # the warning is advisory, not a veto
    assert "pareto_health" in out


def test_run_optimization_omits_pareto_health_for_plain_programs() -> None:
    out = run_optimization(
        baseline="BASE",
        optimized_factory=lambda: "OPT",
        trainset=[_ex("a")],
        valset=[_ex("b")],
        holdout=_holdout(10),
        metric=lambda *a: 0,
        minibatch_scores=[1.0, 0.5, 1.0],
        eval_fn=_winning_eval,
    )
    assert "pareto_health" not in out


# -- Pareto health over the REAL structure: dict-valued, possibly-sparse rows ---
#
# DspyGEPAResult.val_subscores is a list of per-candidate dicts {instance_id: score}
# (the gepa-native form), NOT the list-of-lists `_detailed` fakes. The diagnostic
# must read both and tolerate sparse rows without KeyError.


def _detailed_dicts(rows: list[dict[int, float]]) -> SimpleNamespace:
    return SimpleNamespace(detailed_results=SimpleNamespace(val_subscores=rows))


def test_pareto_health_note_handles_dict_valued_subscores() -> None:
    note = _pareto_health_note(_detailed_dicts([{0: 1.0, 1: 0.5}, {0: 1.0, 1: 0.5}]))
    assert note is not None and "discriminate" in note


def test_pareto_health_note_dict_subscores_can_be_healthy() -> None:
    # Instance 0 ranks the candidates differently (1.0 vs 0.8) → healthy, silent.
    assert (
        _pareto_health_note(_detailed_dicts([{0: 1.0, 1: 0.5}, {0: 0.8, 1: 0.5}]))
        is None
    )


def test_pareto_health_note_tolerates_sparse_dict_rows() -> None:
    # Sparse rows: only instance 0 is shared by both candidates (both score it 1.0).
    # Must not KeyError on the unshared instances; the shared one has no spread → warn.
    note = _pareto_health_note(_detailed_dicts([{0: 1.0, 1: 0.5}, {0: 1.0, 2: 0.9}]))
    assert note is not None


# -- Budget health: too few candidates means validation ate the rollout budget --


def _candidates(n: int) -> SimpleNamespace:
    return SimpleNamespace(
        detailed_results=SimpleNamespace(candidates=[{} for _ in range(n)])
    )


def test_budget_health_note_warns_on_too_few_candidates() -> None:
    note = _budget_health_note(_candidates(1))
    assert note is not None
    assert "explored only 1" in note
    # The cause is low mutation acceptance, NOT validation starving proposal: gepa runs
    # one full-valset eval per accepted candidate, so when n<3 validation consumed only
    # a minority of the budget. The note must not blame the valset (engine invariant
    # num_full_val_evals == len(candidates) makes "validation dominated" + n<3 disjoint).
    assert "mutation acceptance" in note
    assert "shrink the valset" not in note


def test_budget_health_note_silent_when_enough_candidates() -> None:
    assert _budget_health_note(_candidates(3)) is None
    assert _budget_health_note(_candidates(6)) is None


def test_budget_health_note_silent_without_stats() -> None:
    assert _budget_health_note("OPT") is None  # injected string program
    assert _budget_health_note(SimpleNamespace(detailed_results=None)) is None
    # detailed_results present but no `candidates` (the list-of-lists pareto fakes).
    assert _budget_health_note(_detailed([[1.0, 0.5], [1.0, 0.5]])) is None


def test_run_optimization_flags_starved_budget() -> None:
    starved = _candidates(1)  # only the seed survived → an expensive near-no-op

    def eval_fn(prog: object, *, devset: object, metric: object) -> float:
        return 95.0 if prog is starved else 80.0

    out = run_optimization(
        baseline="BASE",
        optimized_factory=lambda: starved,
        trainset=[_ex("a")],
        valset=[_ex("b")],
        holdout=_holdout(10),
        metric=lambda *a: 0,
        minibatch_scores=[1.0, 0.5, 1.0],
        eval_fn=eval_fn,
    )
    assert out["promoted"] is True  # the warning is advisory, not a veto
    assert "budget_health" in out
