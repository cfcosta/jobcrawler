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
from dspy.teleprompt import GEPA, BootstrapFewShotWithRandomSearch

from jobcrawler.generate.evaluate import evaluate_generator
from jobcrawler.trainset import assert_disjoint


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
    """The live GEPA compile (§4.11) — distinct train/val, teacher=None.

    Adapts two GEPA knobs to the pool sizes so a small admitted trainset doesn't
    break the compile: Pareto selection needs ≥2 val instances to form a frontier
    (degrade to ``'current_best'`` below that), and the reflection minibatch must
    not exceed the trainset (clamp it). The gepa rung only runs with ≥20 examples
    (``select_optimizer``), so these are floors, not the common case.
    """
    selection = "pareto" if len(valset) >= 2 else "current_best"
    minibatch = max(1, min(3, len(trainset)))
    gepa = build_gepa(
        metric,
        reflection_lm=reflection_lm,
        candidate_selection_strategy=selection,
        reflection_minibatch_size=minibatch,
    )
    return gepa.compile(baseline, trainset=list(trainset), valset=list(valset))


def bootstrap_compile(
    baseline: dspy.Module,
    *,
    trainset: Sequence[dspy.Example],
    valset: Sequence[dspy.Example],
    metric: Callable[..., float],
    max_bootstrapped_demos: int = 4,
    metric_threshold: float = 0.5,
    num_candidate_programs: int = 8,
) -> dspy.Module:
    """The **M5a** optimizer rung — ``BootstrapFewShotWithRandomSearch`` over the
    owned predictor (the cheap, always-valid step before GEPA; §4.11).

    The trainset is reference-free, so ``max_labeled_demos=0`` (no gold to seed
    labeled demos); only self-bootstrapped demos are kept, gated by
    ``metric_threshold`` against the **float** ``metric`` (see
    ``scoring.make_bootstrap_metric`` — DSPy compares the float, not a Prediction).
    Random search selects the best candidate program on the held-back ``valset``.
    ``max_bootstrapped_demos`` is kept small (a large demo count overfits).
    """
    optimizer = BootstrapFewShotWithRandomSearch(
        metric=metric,
        max_bootstrapped_demos=max_bootstrapped_demos,
        max_labeled_demos=0,
        metric_threshold=metric_threshold,
        num_candidate_programs=num_candidate_programs,
    )
    return optimizer.compile(baseline, trainset=list(trainset), valset=list(valset))


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
    result: dict[str, object] = {
        "promoted": promoted,
        "baseline": baseline_score,
        "optimized": optimized_score,
        "margin": margin,
        "optimized_program": optimized if promoted else None,
    }
    health = _pareto_health_note(optimized)
    if health:
        result["pareto_health"] = health
    budget = _budget_health_note(optimized)
    if budget:
        result["budget_health"] = budget
    return result


def _pareto_health_note(optimized: object) -> str | None:
    """Warn if GEPA's candidates never discriminated across val instances (§4.11).

    GEPA's Pareto sampling only has signal when at least one val instance ranks
    candidates differently (``has_per_instance_spread``). If every instance scored
    every candidate identically, the frontier collapsed to a single co-leader set
    and selection was effectively uniform — the search ran but explored nothing, a
    sign the valset is saturated/non-discriminative. Read from the optional
    ``detailed_results.val_subscores`` (``track_stats=True``); silent when the data
    isn't present (e.g. the injected string programs in tests) or looks healthy.

    Each candidate's per-instance scores arrive as either a ``dict`` keyed by
    instance id (gepa's native, possibly **sparse** form — a candidate need not be
    scored on every instance) or a list indexed by instance (DSPy's annotation / the
    test fakes). We normalize both and compare only the instances at least two
    candidates share, so a sparse row can't ``KeyError`` or fake a collapse.
    """
    detailed = getattr(optimized, "detailed_results", None)
    subscores = getattr(detailed, "val_subscores", None)
    if not subscores or len(subscores) < 2:
        return None  # need ≥2 candidates to compare per-instance ranks
    per_instance: dict[object, list[float]] = {}
    for row in subscores:
        pairs = row.items() if isinstance(row, Mapping) else enumerate(row)
        for inst, value in pairs:
            per_instance.setdefault(inst, []).append(value)
    comparable = {
        str(inst): scores for inst, scores in per_instance.items() if len(scores) >= 2
    }
    if not comparable or has_per_instance_spread(comparable):
        return None
    return (
        "GEPA's valset never discriminated between candidates (no per-instance "
        "spread) — the frontier collapsed and selection was effectively uniform. "
        "The valset is likely saturated/non-discriminative; bias it toward harder, "
        "more varied sites before trusting a promotion. " + SATURATION_REMEDY
    )


def _budget_health_note(optimized: object, *, min_candidates: int = 3) -> str | None:
    """Warn if GEPA explored too few candidates to be a real search (§4.11).

    ``detailed_results.candidates`` is the seed plus every *accepted* mutation — gepa
    appends to the candidate pool only when a proposal strictly beats the incumbent
    on the reflection minibatch (engine acceptance gate). A count below
    ``min_candidates`` therefore means the search barely moved, and the cause is
    **low mutation acceptance**: a saturated/non-discriminative pool on which the
    reflected proposals can't beat the incumbent.

    It is specifically *not* validation starving proposal. gepa runs exactly one
    full-valset evaluation per accepted candidate (a full eval happens iff a
    candidate is added), so when this fires (``n < min_candidates``) validation
    consumed only ``n·|valset|`` of the ~``4·|valset| + const`` budget — a minority,
    leaving the bulk to the reflection phase, which simply didn't land. (A large
    valset *can* dominate the budget, but only in the healthy ``n >= min_candidates``
    case, which returns ``None`` here; DSPy also warns on an oversized valset at
    compile time, and ``cli_optimize`` caps it at ``max(1, n_sites//5)`` regardless.)
    Advisory, never a promotion veto; silent when stats aren't present (injected
    test programs).
    """
    detailed = getattr(optimized, "detailed_results", None)
    candidates = getattr(detailed, "candidates", None)
    if candidates is None:
        return None
    n = len(candidates)
    if n >= min_candidates:
        return None
    return (
        f"GEPA explored only {n} candidate(s) — the search barely moved, almost "
        "always low mutation acceptance: the reflected proposals never beat the "
        "incumbent on the minibatch. " + SATURATION_REMEDY
    )


def build_gepa(
    metric: Callable[..., object],
    *,
    reflection_lm: dspy.LM,
    auto: Literal["light", "medium", "heavy"] = "light",
    track_stats: bool = True,
    candidate_selection_strategy: Literal["pareto", "current_best"] = "pareto",
    reflection_minibatch_size: int = 3,
    seed: int = 0,
):
    """Construct the GEPA optimizer (§4.11 config). ``compile`` is the live step.

    ``reflection_lm`` should be a strong model of a *different* family than the
    cheap RLM ``sub_lm``; ``auto='light'`` starts the budget low (a low accepted-
    mutation count is not failure). (Return type is left to inference: ty models
    dspy's ``GEPA`` as a constructor-typed value, not a class, so neither an
    explicit ``-> GEPA`` annotation nor ``isinstance(x, GEPA)`` type-checks.)

    Three knobs DSPy already defaults are pinned **explicitly** so the run is
    reproducible and robust to a dspy version bump (the GEPA internals were
    verified against 3.2.1 — see ``test_dspy_pin``):

      * ``candidate_selection_strategy='pareto'`` — sample the next parent ∝ the
        number of *valset* instances on which it is among the per-instance best
        (co-leaders counted, per the paper, not sole leadership); this is GEPA's
        core sampling and needs per-instance score variance to work
        (``has_per_instance_spread`` is the post-compile health check). Degrade to
        ``'current_best'`` only when the valset is too small to form a frontier.
      * ``reflection_minibatch_size=3`` — the trainset minibatch each reflection
        step scores; must be ``<=`` trainset size (``gepa_compile`` clamps it).
      * ``seed=0`` — pins minibatch sampling so promotion isn't chasing RNG.
    """
    return GEPA(
        metric=metric,
        reflection_lm=reflection_lm,
        auto=auto,
        track_stats=track_stats,
        use_merge=False,  # §4.11: Merge is OFF for v1 (dspy defaults it on)
        candidate_selection_strategy=candidate_selection_strategy,
        reflection_minibatch_size=reflection_minibatch_size,
        seed=seed,
    )
