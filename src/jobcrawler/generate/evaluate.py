"""dspy.Evaluate over a frozen fixture pool (§4.11 promotion, §12).

The promotion held-out is a *third* pool GEPA never sees, scored only here after
compile. ``evaluate_generator`` runs the program over a frozen devset with the
shared example-based metric (the same callable GEPA used) and returns the mean
score, so a candidate generator and the incumbent are compared on identical,
unseen snapshots before ``should_promote`` decides.

The generator is **stochastic** — each pass re-samples a fresh strategy plan and
a fresh RLM rollout, so a single pass over a small held-out is noisy (a candidate
can win or lose a one-site holdout on sampling luck alone). ``passes`` averages
several independent passes so ``should_promote`` compares stable estimates rather
than chasing eval noise — promotion rigor the spec calls for (§4.11).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import dspy


def evaluate_generator(
    program: dspy.Module,
    *,
    devset: Sequence[dspy.Example],
    metric: Callable[..., object],
    num_threads: int = 1,
    passes: int = 1,
) -> float:
    """Return the program's mean metric score (0–100) over the frozen ``devset``.

    ``passes > 1`` re-runs the whole evaluation that many times and averages the
    per-pass means — averaging out the generator's sampling variance so a marginal
    win on a small held-out isn't promoted on luck. ``passes=1`` (the default) is
    a single pass, unchanged.
    """
    if passes < 1:
        raise ValueError("passes must be >= 1")
    evaluator = dspy.Evaluate(
        devset=list(devset),
        metric=metric,
        num_threads=num_threads,
        display_progress=False,
    )
    scores = [float(evaluator(program).score) for _ in range(passes)]
    return sum(scores) / len(scores)
