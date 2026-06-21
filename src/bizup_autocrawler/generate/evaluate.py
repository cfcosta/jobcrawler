"""dspy.Evaluate over a frozen fixture pool (§4.11 promotion, §12).

The promotion held-out is a *third* pool GEPA never sees, scored only here after
compile. ``evaluate_generator`` runs the program over a frozen devset with the
shared example-based metric (the same callable GEPA used) and returns the mean
score, so a candidate generator and the incumbent are compared on identical,
unseen snapshots before ``should_promote`` decides.
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
) -> float:
    """Return the program's mean metric score (0–100) over the frozen ``devset``."""
    evaluator = dspy.Evaluate(
        devset=list(devset),
        metric=metric,
        num_threads=num_threads,
        display_progress=False,
    )
    return float(evaluator(program).score)
