"""The offline GEPA self-improvement job (§4.11) — out-of-band, never in-band.

Decision logic for the optimizer loop plus the GEPA constructor. The live
``compile()`` (a multi-hour run, hundreds of metric calls each spinning a fresh
RLM sandbox) is invoked by the out-of-band job, never the §4.9 scheduler.

  * ``select_optimizer`` — the ladder: escalate by trainset *size*, not straight
    to GEPA (no trainset → none; ~5–20 → few-shot; feedback-rich → GEPA; 200+ →
    MIPRO, documented-not-built in v1).
  * ``has_optimization_signal`` — the load-bearing anti-saturation guard: GEPA
    no-ops when every sampled minibatch score is maximal (a graded scalar is a
    precondition, not the guard), so require a minimum sub-maximal fraction.
  * ``should_promote`` — promote the optimized generator only if it beats the
    incumbent by a margin on the frozen held-out pool GEPA never saw.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Literal

import dspy
from dspy.teleprompt import GEPA

from bizup_autocrawler.generate.evaluate import evaluate_generator
from bizup_autocrawler.trainset import assert_disjoint


def select_optimizer(trainset_size: int) -> str:
    if trainset_size <= 0:
        return "none"  # keep §6 BestOfN/Refine, which need no trainset
    if trainset_size < 20:
        return "bootstrap_fewshot"  # ~5–20: generalization-guided few-shot
    if trainset_size < 200:
        return "gepa"  # feedback-rich: instruction evolution
    return "mipro"  # 200+: documented in §4.11, not built in v1


def has_optimization_signal(
    scores: Sequence[float],
    *,
    min_submaximal_fraction: float = 0.2,
    max_score: float = 1.0,
) -> bool:
    """True iff enough sampled scores are sub-maximal for GEPA to have signal."""
    if not scores:
        return False
    submaximal = sum(1 for s in scores if s < max_score)
    return submaximal / len(scores) >= min_submaximal_fraction


def should_promote(baseline: float, candidate: float, *, margin: float = 0.0) -> bool:
    """Promote iff ``candidate`` beats ``baseline`` by ``margin`` on the held-out."""
    return candidate > baseline + margin


def promotion_margin(holdout_size: int) -> float:
    """The noise-floor margin ~ 1/|held-out| on the 0–100 scale (§4.11).

    One flipped held-out example moves an Evaluate score by 100/H, so a smaller
    improvement is within eval noise and must not trigger promotion.
    """
    return 100.0 / holdout_size if holdout_size else 100.0


def has_per_instance_spread(per_site_scores: Mapping[str, Sequence[float]]) -> bool:
    """True iff some site's score varies across candidates (§4.11).

    GEPA's Pareto sampling consumes per-site score *variance*; a scalar that is
    high-variance across the trainset but flat per-site collapses sampling to
    uniform. At least one site must discriminate between candidates.
    """
    return any(len(set(scores)) > 1 for scores in per_site_scores.values())


SATURATION_REMEDY = (
    "baseline ≈ optimized and/or no sub-maximal scores — the valset is saturated, "
    "so GEPA's reflection LM never fires. Remedies (§4.11): (i) re-bias the trainset "
    "toward hard/discriminative cases (recently-drifted, many-retry, barely-passing); "
    "(ii) run the optimizer on a cheaper executor to manufacture failure signal "
    "(promotion still re-scores on the deployment tier); (iii) accept and defer."
)


def gepa_compile(
    baseline: dspy.Module,
    *,
    trainset: Sequence[dspy.Example],
    valset: Sequence[dspy.Example],
    reflection_lm: dspy.LM,
    metric: Callable[..., object],
) -> dspy.Module:
    """The live GEPA compile (§4.11) — distinct train/val, teacher=None."""
    gepa = build_gepa(metric, reflection_lm=reflection_lm)
    return gepa.compile(baseline, trainset=list(trainset), valset=list(valset))


def run_optimization(
    *,
    baseline: object,
    optimized_factory: Callable[[], object],
    trainset: Sequence[dspy.Example],
    valset: Sequence[dspy.Example],
    holdout: Sequence[dspy.Example],
    metric: Callable[..., object],
    minibatch_scores: Sequence[float],
    eval_fn: Callable[..., float] = evaluate_generator,
) -> dict[str, object]:
    """The offline self-improvement decision flow (§4.11) — out-of-band only.

    Asserts the three pools are disjoint (a leak GEPA exploits), short-circuits
    when there is no trainset or the minibatch is saturated, then compiles (via
    the injected ``optimized_factory``, the live GEPA step) and promotes only if
    the optimized program beats the incumbent by the noise-floor margin on the
    frozen held-out pool GEPA never saw. ``optimized_factory``/``eval_fn`` are
    injected so the decisions are tested without a live compile.
    """
    assert_disjoint(trainset, valset, holdout)
    if select_optimizer(len(trainset)) == "none":
        return {"promoted": False, "reason": "no trainset; keep §6 BestOfN/Refine"}
    if not has_optimization_signal(minibatch_scores):
        return {"promoted": False, "reason": "saturated", "remedy": SATURATION_REMEDY}

    optimized = optimized_factory()
    baseline_score = eval_fn(baseline, devset=holdout, metric=metric)
    optimized_score = eval_fn(optimized, devset=holdout, metric=metric)
    margin = promotion_margin(len(holdout))
    promoted = should_promote(baseline_score, optimized_score, margin=margin)
    return {
        "promoted": promoted,
        "baseline": baseline_score,
        "optimized": optimized_score,
        "margin": margin,
        "optimized_program": optimized if promoted else None,
    }


def build_gepa(
    metric: Callable[..., object],
    *,
    reflection_lm: dspy.LM,
    auto: Literal["light", "medium", "heavy"] = "light",
    track_stats: bool = True,
):
    """Construct the GEPA optimizer (§4.11 config). ``compile`` is the live step.

    ``reflection_lm`` should be a strong model of a *different* family than the
    cheap RLM ``sub_lm``; ``auto='light'`` starts the budget low (a low accepted-
    mutation count is not failure). (Return type is left to inference: ty models
    dspy's ``GEPA`` as a constructor-typed value, not a class, so neither an
    explicit ``-> GEPA`` annotation nor ``isinstance(x, GEPA)`` type-checks.)
    """
    return GEPA(
        metric=metric,
        reflection_lm=reflection_lm,
        auto=auto,
        track_stats=track_stats,
        use_merge=False,  # §4.11: Merge is OFF for v1 (dspy defaults it on)
    )
