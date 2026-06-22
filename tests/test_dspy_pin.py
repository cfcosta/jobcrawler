"""§9: the source-pinning strategy depends on dspy staying at the verified version.

The spec verifies dspy internals against pinned 3.2.1 source (RLM caps, the
Evaluate/GEPA metric compatibility, corpus injection). *Any* bump — even a patch —
can silently move those internals, so the tripwire pins the exact version the source
was read against, not just the 3.2.x line. It pairs with pyproject's `<3.3` floor and
runs in CI (the pytest flake check), so a re-lock that moves dspy fails the build and
forces a human to re-verify the assumptions before the new version ships.
"""

from __future__ import annotations

import dspy

# The exact dspy release the §9 source-pinned assumptions were verified against.
# Bumping this is a deliberate act: re-read the RLM / Evaluate / GEPA internals first.
_VERIFIED_DSPY = "3.2.1"


def test_dspy_is_pinned_to_verified_version() -> None:
    assert dspy.__version__ == _VERIFIED_DSPY, (
        f"dspy {dspy.__version__} != source-verified {_VERIFIED_DSPY} (§9): a bump can "
        "silently move the pinned internals — re-verify them, then update _VERIFIED_DSPY"
    )
