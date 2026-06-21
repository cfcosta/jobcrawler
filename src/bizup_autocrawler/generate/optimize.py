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

from collections.abc import Callable, Sequence
from typing import Literal

import dspy
from dspy.teleprompt import GEPA


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
