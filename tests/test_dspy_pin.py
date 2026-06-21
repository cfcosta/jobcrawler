"""§9: the source-pinning strategy depends on dspy staying on the 3.2.x line.

The spec verifies dspy internals against pinned 3.2.1 source (RLM caps, the
Evaluate/GEPA metric compatibility, corpus injection). A minor-version bump can
silently move those, so this is the regression tripwire pyproject's `<3.3` floor
pairs with.
"""

from __future__ import annotations

import dspy


def test_dspy_is_pinned_to_3_2_x() -> None:
    assert dspy.__version__.startswith("3.2"), (
        f"dspy {dspy.__version__} is off the source-pinned 3.2.x line (§9)"
    )
